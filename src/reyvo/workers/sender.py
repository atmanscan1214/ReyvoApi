"""Sender worker scaffold.

The HTTP API never sends bulk email inline. This module is the isolated process
boundary for the send loop. It is intentionally conservative: the scheduling
query and safety checks are implemented, while the actual provider send call is
left as an explicit extension point so the scaffold cannot accidentally email a
real list.

Run with:

    python -m reyvo.workers.sender

Required next steps before production use:
- claim due enrollments atomically (SELECT ... FOR UPDATE SKIP LOCKED)
- enforce per-inbox daily/hourly caps and business-hour windows
- render the step template
- call the Gmail/Graph adapter
- persist the Message row and advance the enrollment
- handle retries, bounces, and reply stops
"""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy import select

from reyvo.core.config import get_settings
from reyvo.core.utils import utcnow
from reyvo.db.enums import CampaignStatus, EnrollmentStatus
from reyvo.db.models import Campaign, CampaignEnrollment
from reyvo.db.session import build_engine, build_session_factory

logger = logging.getLogger("reyvo.workers.sender")


async def find_due_enrollments(limit: int = 100) -> list[CampaignEnrollment]:
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session:
            stmt = (
                select(CampaignEnrollment)
                .join(Campaign, Campaign.id == CampaignEnrollment.campaign_id)
                .where(
                    CampaignEnrollment.status == EnrollmentStatus.ACTIVE,
                    CampaignEnrollment.next_send_at.is_not(None),
                    CampaignEnrollment.next_send_at <= utcnow(),
                    Campaign.status == CampaignStatus.ACTIVE,
                )
                .order_by(CampaignEnrollment.next_send_at)
                .limit(limit)
            )
            return list((await session.scalars(stmt)).all())
    finally:
        await engine.dispose()


async def run_once() -> int:
    due = await find_due_enrollments()
    if due:
        logger.warning(
            "%s enrollment(s) are due, but the provider send step is not implemented "
            "in this scaffold. Refusing to send.",
            len(due),
        )
    return len(due)


async def run_forever(interval_seconds: int = 30) -> None:
    stop = asyncio.Event()

    def request_stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:  # Windows
            signal.signal(sig, request_stop)

    logger.info("Sender worker started (interval=%ss)", interval_seconds)
    while not stop.is_set():
        try:
            count = await run_once()
            if count:
                logger.info("Processed %s due enrollment(s)", count)
        except Exception:
            logger.exception("Sender iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
    logger.info("Sender worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
