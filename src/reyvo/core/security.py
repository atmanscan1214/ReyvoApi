from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import timedelta
from typing import Any, Literal

import jwt
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash

from reyvo.core.config import get_settings
from reyvo.core.exceptions import UnauthorizedError
from reyvo.core.utils import utcnow

_password_hash = PasswordHash.recommended()

TokenType = Literal["access", "refresh", "oauth_state", "sso_state"]
UNSUBSCRIBE_SALT = "reyvo-unsubscribe"


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def _encode_token(payload: dict[str, Any], expires_delta: timedelta) -> str:
    settings = get_settings()
    now = utcnow()
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(body, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    settings = get_settings()
    delta = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    return _encode_token(
        {"sub": str(user_id), "tid": str(tenant_id), "role": role, "type": "access"},
        delta,
    )


def create_refresh_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    expires_delta: timedelta | None = None,
) -> str:
    settings = get_settings()
    delta = expires_delta or timedelta(days=settings.refresh_token_expire_days)
    return _encode_token(
        {"sub": str(user_id), "tid": str(tenant_id), "type": "refresh"},
        delta,
    )


def create_oauth_state(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: str,
    expires_delta: timedelta = timedelta(minutes=10),
) -> str:
    return _encode_token(
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "provider": provider,
            "type": "oauth_state",
        },
        expires_delta,
    )


def decode_token(
    token: str,
    *,
    expected_type: TokenType | None = None,
    require_subject: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("The token is invalid or has expired.") from exc

    if expected_type is not None and payload.get("type") != expected_type:
        raise UnauthorizedError("The token type is not valid for this operation.")

    if require_subject and not payload.get("sub"):
        raise UnauthorizedError("The token is missing a subject.")

    return payload


def create_sso_state(
    *,
    tenant_id: uuid.UUID,
    return_to: str | None = None,
    login_hint: str | None = None,
    expires_delta: timedelta = timedelta(minutes=10),
) -> str:
    return _encode_token(
        {
            "sub": str(tenant_id),
            "tid": str(tenant_id),
            "type": "sso_state",
            "return_to": return_to,
            "login_hint": login_hint,
        },
        expires_delta,
    )


def decode_sso_state(token: str) -> dict[str, Any]:
    return decode_token(token, expected_type="sso_state")


def create_unsubscribe_token(*, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str:
    settings = get_settings()
    serializer = URLSafeTimedSerializer(settings.secret_key, salt=UNSUBSCRIBE_SALT)
    return serializer.dumps({"tid": str(tenant_id), "cid": str(contact_id)})


def verify_unsubscribe_token(
    token: str, *, max_age_seconds: int = 60 * 60 * 24 * 90
) -> dict[str, str]:
    settings = get_settings()
    serializer = URLSafeTimedSerializer(settings.secret_key, salt=UNSUBSCRIBE_SALT)
    try:
        data = serializer.loads(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise UnauthorizedError("This unsubscribe link has expired.") from exc
    except BadSignature as exc:
        raise UnauthorizedError("This unsubscribe link is not valid.") from exc

    if not isinstance(data, dict) or "tid" not in data or "cid" not in data:
        raise UnauthorizedError("This unsubscribe link is not valid.")
    return data


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.token_encryption_key:
        key = settings.token_encryption_key.encode()
    else:
        # Development fallback only. Production must set TOKEN_ENCRYPTION_KEY.
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise UnauthorizedError("Stored provider credentials could not be decrypted.") from exc
