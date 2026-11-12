from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from reyvo.core.redis import ping_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))

    settings = request.app.state.settings
    redis_status = "disabled"
    if settings.redis_url:
        redis_status = "ok" if await ping_redis() else "unavailable"
    return {
        "status": "ready" if redis_status != "unavailable" else "degraded",
        "redis": redis_status,
    }
