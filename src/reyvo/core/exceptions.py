from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected application errors returned as a stable envelope."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PreflightError(AppError):
    status_code = 422
    code = "preflight_failed"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
