from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def slugify(value: str, max_length: int = 48) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or "workspace"


def unique_slug(base: str) -> str:
    return f"{slugify(base)}-{secrets.token_hex(3)}"


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain}"


def split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]
