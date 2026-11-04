"""Delete documents (and their files) older than RETENTION_DAYS. 0 = keep forever."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Document, utcnow
from app.services.storage import doc_prefix, get_storage

log = logging.getLogger(__name__)


def delete_document_files(document_id: str) -> None:
    get_storage().delete_prefix(doc_prefix(document_id))


def cleanup_expired() -> int:
    days = get_settings().retention_days
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    n = 0
    with session_scope() as db:
        for doc in db.scalars(select(Document).where(Document.updated_at < cutoff)).all():
            delete_document_files(doc.id)
            db.delete(doc)
            n += 1
    if n:
        log.info("retention: deleted %s expired documents", n)
    return n
