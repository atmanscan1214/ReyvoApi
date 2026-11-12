"""Arq worker process: scheduled and on-demand background jobs.

Run with either:

    arq reyvo.workers.worker.WorkerSettings
    python -m reyvo.workers.worker

The worker owns its own database engine and reuses it across jobs. ``REDIS_URL``
must point at the same Redis used by the API; without it the worker cannot
start, while the API remains usable with in-process rate limiting.
"""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings
from arq.worker import run_worker

from reyvo.core.config import get_settings
from reyvo.db.session import (
    build_engine,
    build_session_factory,
    dispose_engine,
    init_models,
)
from reyvo.workers.jobs import (
    cleanup_chat_history,
    reindex_design_document,
    send_due_enrollments,
)
from reyvo.workers.queue import JOB_QUEUE_NAME, job_redis_settings

logger = logging.getLogger("reyvo.workers")


async def startup(ctx: dict) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    if settings.auto_create_tables:
        await init_models(engine)
    ctx["engine"] = engine
    ctx["session_factory"] = build_session_factory(engine)
    logger.info("Worker connected to the database.")


async def shutdown(ctx: dict) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await dispose_engine(engine)
    logger.info("Worker database connection closed.")


def cron_jobs() -> list:
    return [
        # The sender scaffold is a no-op until the provider send step lands, but
        # the schedule and worker boundary are in place.
        cron(send_due_enrollments, second={0, 30}, run_at_startup=True),
        cron(cleanup_chat_history, hour=3, minute=0),
        cron(reindex_design_document, hour={0, 6, 12, 18}, minute=5),
    ]


class WorkerSettings:
    functions = [send_due_enrollments, cleanup_chat_history, reindex_design_document]
    cron_jobs = cron_jobs()
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = job_redis_settings() or RedisSettings()
    queue_name = JOB_QUEUE_NAME
    max_jobs = 10
    job_timeout = 600
    keep_result = 3600
    max_tries = 3


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
