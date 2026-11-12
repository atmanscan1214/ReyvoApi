"""Background jobs executed by the Arq worker process.

Each job receives the Arq context, which carries the shared engine/session
factory created in ``reyvo.workers.worker.startup``. Jobs are idempotent where
possible and log a compact summary so worker output stays useful.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from reyvo.core.config import get_settings
from reyvo.core.utils import utcnow
from reyvo.services import knowledge_service
from reyvo.workers import sender

logger = logging.getLogger("reyvo.workers.jobs")


async def send_due_enrollments(ctx: dict[str, Any]) -> int:
    """Run one sender pass without blocking the API process."""

    count = await sender.run_once(session_factory=ctx["session_factory"])
    if count:
        logger.info("Sender pass found %s due enrollment(s).", count)
    return count


async def cleanup_chat_history(ctx: dict[str, Any]) -> int:
    """Delete conversations whose last activity is older than the retention window."""

    days = get_settings().chat_retention_days
    if days <= 0:
        logger.info("Chat retention is disabled (CHAT_RETENTION_DAYS=0).")
        return 0

    cutoff = utcnow() - timedelta(days=days)
    async with ctx["session_factory"]() as session:
        deleted = await knowledge_service.purge_conversations_before(session, cutoff)
    logger.info("Deleted %s chat conversation(s) older than %s.", deleted, cutoff.date())
    return deleted


async def reindex_design_document(ctx: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Re-ingest the design document; unchanged checksums are skipped unless forced."""

    async with ctx["session_factory"]() as session:
        result = await knowledge_service.ingest_design_document(session, force=force)
    logger.info(
        "Knowledge reindex finished (skipped=%s, chunks=%s).",
        result.skipped,
        result.chunk_count,
    )
    return {
        "slug": result.slug,
        "title": result.title,
        "version": result.version,
        "checksum": result.checksum,
        "chunk_count": result.chunk_count,
        "embedded_chunk_count": result.embedded_chunk_count,
        "skipped": result.skipped,
    }
