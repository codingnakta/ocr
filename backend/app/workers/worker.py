"""Durable DB-backed work queue.

Claiming uses an atomic compare-and-set UPDATE (status pending -> running) so several
worker threads/processes can share the queue on SQLite or PostgreSQL without double
processing. All state lives in the database: after a crash/restart, tasks left
`running` are re-queued and API calls left `started` become `uncertain` (possibly
billed — their estimated cost keeps counting against the cost limit).

Run standalone:  python -m app.workers.worker
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from datetime import timedelta

from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.core.db import init_db, session_scope
from app.models import Export, Job, PageTask, utcnow
from app.ocr.base import CostLimitExceeded, OcrError
from app.services import jobs as job_svc
from app.services.export_service import run_export
from app.services.ocr_service import mark_stale_calls_uncertain, process_page

log = logging.getLogger("pilgibot.worker")


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def recover(stale_only: bool = False) -> dict:
    """Re-queue tasks whose worker died. On startup of a single-process deployment all
    `running` tasks are orphans; with several workers only stale locks are taken."""
    s = get_settings()
    cutoff = utcnow() - timedelta(seconds=s.task_lock_timeout_s)
    with session_scope() as db:
        q = update(PageTask).where(PageTask.status == "running")
        if stale_only:
            q = q.where(or_(PageTask.locked_at.is_(None), PageTask.locked_at < cutoff))
        n_tasks = db.execute(q.values(status="pending", locked_by=None, locked_at=None,
                                      last_error="작업자 중단으로 재시도")).rowcount or 0
        db.execute(update(Job).where(Job.status == "running").values(status="queued"))
        qe = update(Export).where(Export.status == "running")
        n_exp = db.execute(qe.values(status="queued")).rowcount or 0
    n_calls = mark_stale_calls_uncertain(0 if not stale_only else s.task_lock_timeout_s)
    if n_tasks or n_calls or n_exp:
        log.warning("recovered tasks=%s exports=%s uncertain_calls=%s", n_tasks, n_exp, n_calls)
    return {"tasks": n_tasks, "exports": n_exp, "uncertain_calls": n_calls}


def claim_task(wid: str) -> tuple[str, str] | None:
    with session_scope() as db:
        rows = db.execute(
            select(PageTask.id, PageTask.job_id).join(Job, Job.id == PageTask.job_id)
            .where(PageTask.status == "pending", Job.status.in_(("queued", "running")), Job.kind == "ocr")
            .order_by(Job.created_at, PageTask.seq).limit(5)
        ).all()
        for tid, jid in rows:
            res = db.execute(update(PageTask).where(PageTask.id == tid, PageTask.status == "pending")
                             .values(status="running", locked_by=wid, locked_at=utcnow(), started_at=utcnow(),
                                     attempts=PageTask.attempts + 1))
            if res.rowcount == 1:
                db.execute(update(Job).where(Job.id == jid, Job.status == "queued").values(status="running"))
                return tid, jid
    return None


def claim_export(wid: str) -> str | None:
    with session_scope() as db:
        ids = db.scalars(select(Export.id).where(Export.status == "queued").order_by(Export.created_at).limit(3)).all()
        for eid in ids:
            if db.execute(update(Export).where(Export.id == eid, Export.status == "queued")
                          .values(status="running")).rowcount == 1:
                return eid
    return None


def handle_task(task_id: str, job_id: str) -> None:
    s = get_settings()
    with session_scope() as db:
        task = db.get(PageTask, task_id)
        job = db.get(Job, job_id)
        if task is None or job is None:
            return
        if job.status not in ("queued", "running"):
            task.status = "pending"  # paused/cancelled meanwhile: give it back untouched
            if job.status == "cancelled":
                task.status = "cancelled"
            task.locked_by = None
            return
        page_id, mode, owner = task.page_id, job.mode, job.owner_id
        steps = (job.params or {}).get("preprocess") or {}
        db.expunge(job)
    error = None
    retry = False
    cost_stop = None
    info: dict = {}
    try:
        info = process_page(page_id, mode, job, owner, steps)
        log.info("page done job=%s page=%s %s", job_id, page_id, info)
    except CostLimitExceeded as e:
        cost_stop = str(e)
    except OcrError as e:
        error = str(e)
    except Exception as e:  # unexpected: keep the job alive, record the page failure
        log.exception("page failed job=%s page=%s", job_id, page_id)
        error = f"내부 오류: {type(e).__name__}"
        retry = True
    with session_scope() as db:
        task = db.get(PageTask, task_id)
        job = db.get(Job, job_id)
        if task is None or job is None:
            return
        task.locked_by = None
        task.finished_at = utcnow()
        if cost_stop:
            task.status = "pending"
            task.attempts = max(0, task.attempts - 1)
            if job.status in ("queued", "running"):
                job.status = "paused"
                job.stop_reason = "cost_limit"
                job.error = cost_stop
        elif error:
            from app.models import Page

            if retry and task.attempts < s.task_max_attempts:
                task.status = "pending"
                task.last_error = error
            else:
                task.status = "failed"
                task.last_error = error
                page = db.get(Page, page_id)
                if page is not None and page.ocr_status != "done":
                    page.ocr_status = "failed"
                    page.ocr_error = error
        else:
            task.status = "done"
            task.last_error = None
            task.from_cache = bool(info.get("from_cache"))
        job_svc.finalize_if_done(db, job)


class Worker:
    def __init__(self, concurrency: int | None = None):
        s = get_settings()
        self.concurrency = concurrency or s.worker_concurrency
        self.poll = s.worker_poll_interval_s
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.wid = worker_id()

    def _loop(self, n: int) -> None:
        wid = f"{self.wid}#{n}"
        last_cleanup = 0.0
        while not self.stop_event.is_set():
            try:
                if n == 0 and time.monotonic() - last_cleanup > 3600:
                    last_cleanup = time.monotonic()
                    from app.services.retention import cleanup_expired

                    cleanup_expired()
                eid = claim_export(wid) if n == 0 else None
                if eid:
                    try:
                        run_export(eid)
                    except Exception as e:
                        log.exception("export failed %s", eid)
                        with session_scope() as db:
                            ex = db.get(Export, eid)
                            if ex:
                                ex.status = "failed"
                                ex.error = f"내보내기 실패: {type(e).__name__}: {str(e)[:200]}"
                    continue
                claimed = claim_task(wid)
                if claimed:
                    handle_task(*claimed)
                    continue
            except Exception:
                log.exception("worker loop error")
            self.stop_event.wait(self.poll)

    def start(self) -> None:
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, args=(i,), name=f"ocr-worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self, timeout: float = 30.0) -> None:
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from app.core.logging import install_redaction

    install_redaction()
    init_db()
    recover(stale_only=not get_settings().worker_recover_all)
    w = Worker()
    w.start()
    log.info("worker started (%s threads)", w.concurrency)
    signal.signal(signal.SIGTERM, lambda *_: w.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: w.stop_event.set())
    while not w.stop_event.is_set():
        time.sleep(1)
    w.stop()


if __name__ == "__main__":
    main()
