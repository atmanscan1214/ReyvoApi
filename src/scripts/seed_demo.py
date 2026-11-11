from __future__ import annotations

import asyncio
import sys

from reyvo.core.config import get_settings
from reyvo.db.models import User
from reyvo.db.session import build_engine, build_session_factory
from reyvo.schemas.auth import RegisterRequest
from reyvo.services import auth_service
from sqlalchemy import select

DEMO_EMAIL = "demo@reyvo.com"
DEMO_PASSWORD = "ReyvoDemo123!"
DEMO_NAME = "Demo Owner"
DEMO_AGENCY = "Reyvo Demo Agency"


async def seed() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session:
            existing = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
            if existing is not None:
                print(f"Demo account already exists: {DEMO_EMAIL}")
                return

            principal = await auth_service.register(
                session,
                RegisterRequest(
                    email=DEMO_EMAIL,
                    password=DEMO_PASSWORD,
                    full_name=DEMO_NAME,
                    agency_name=DEMO_AGENCY,
                ),
            )
            print("Demo account created")
            print(f"  email:    {DEMO_EMAIL}")
            print(f"  password: {DEMO_PASSWORD}")
            print(f"  tenant:   {principal.tenant.name} ({principal.tenant.slug})")
    finally:
        await engine.dispose()


def main() -> None:
    # psycopg's async driver cannot use the ProactorEventLoop on Windows.
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(seed())
    else:
        asyncio.run(seed())


if __name__ == "__main__":
    main()
