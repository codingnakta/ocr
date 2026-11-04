from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user
from app.models import Document, Export, Job, Page, User


def owned_document(document_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Document:
    doc = db.get(Document, document_id)
    # same 404 for "missing" and "not yours": never reveal other users' ids
    if doc is None or doc.owner_id != user.id:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return doc


def resolve_page(db: Session, doc: Document, page_ref: str) -> Page:
    """`page_ref` is a page id or a 1-based page number."""
    page = None
    if page_ref.isdigit() and len(page_ref) < 7:
        n = int(page_ref)
        page = db.scalar(select(Page).where(Page.document_id == doc.id).order_by(Page.position)
                         .offset(n - 1).limit(1)) if n >= 1 else None
    else:
        page = db.get(Page, page_ref)
        if page is not None and page.document_id != doc.id:
            page = None
    if page is None:
        raise HTTPException(404, "페이지를 찾을 수 없습니다.")
    return page


def owned_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.owner_id != user.id:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return job


def owned_export(export_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Export:
    ex = db.get(Export, export_id)
    if ex is None or ex.owner_id != user.id:
        raise HTTPException(404, "내보내기를 찾을 수 없습니다.")
    return ex
