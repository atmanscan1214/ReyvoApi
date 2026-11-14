"""SMTP transport adapter.

This adapter lets Reyvo send through any authenticated SMTP relay (Brevo,
Amazon SES SMTP, Postmark, Mailgun, and similar) using the deployment-level
``SMTP_*`` settings instead of per-inbox OAuth tokens.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from reyvo.core.config import get_settings
from reyvo.core.exceptions import BadRequestError, ServiceUnavailableError
from reyvo.integrations.email.base import (
    EmailProviderError,
    OAuthTokens,
    OutboundEmail,
    ProviderAccountInfo,
    SendResult,
)


class SmtpAdapter:
    provider = "smtp"

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password or ""
        self._from_email = settings.smtp_from_email or settings.smtp_username
        self._from_name = settings.smtp_from_name
        self._starttls = settings.smtp_starttls
        self._use_ssl = settings.smtp_use_ssl
        self._timeout = settings.smtp_timeout_seconds

    def _require_config(self) -> None:
        if not self._host or not self._from_email:
            raise ServiceUnavailableError(
                "SMTP is not configured. Set SMTP_HOST and either SMTP_FROM_EMAIL "
                "or SMTP_USERNAME."
            )

    def _connection(self):
        self._require_config()
        context = ssl.create_default_context()
        if self._use_ssl:
            return smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout,
                context=context,
            )
        return smtplib.SMTP(self._host, self._port, timeout=self._timeout)

    def _authenticate(self, client: smtplib.SMTP) -> None:
        client.ehlo()
        if self._starttls and not self._use_ssl:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if self._username:
            client.login(self._username, self._password)

    def _send_blocking(self, message: EmailMessage) -> SendResult:
        client = self._connection()
        try:
            self._authenticate(client)
            refused = client.send_message(message) or {}
            if refused:
                raise EmailProviderError(
                    "SMTP relay refused recipient(s): " + ", ".join(sorted(refused))
                )
        except smtplib.SMTPException as exc:
            raise EmailProviderError(f"SMTP send failed: {exc}") from exc
        finally:
            try:
                client.quit()
            except smtplib.SMTPException:
                client.close()

        message_id = str(message["Message-ID"] or "")
        return SendResult(
            provider_message_id=message_id or None,
            rfc_message_id=message_id or None,
        )

    def _verify_blocking(self) -> None:
        client = self._connection()
        try:
            self._authenticate(client)
        except smtplib.SMTPException as exc:
            raise EmailProviderError(f"SMTP connection failed: {exc}") from exc
        finally:
            try:
                client.quit()
            except smtplib.SMTPException:
                client.close()

    async def verify_connection(self) -> None:
        """Connect, negotiate TLS and authenticate without sending a message."""

        await asyncio.to_thread(self._verify_blocking)

    async def send_email(self, *, access_token: str, email: OutboundEmail) -> SendResult:
        del access_token  # SMTP uses deployment credentials, not OAuth.
        self._require_config()

        message = EmailMessage()
        message["From"] = (
            formataddr((email.from_name or self._from_name, self._from_email))
            if (email.from_name or self._from_name)
            else self._from_email
        )
        message["To"] = email.to_email
        message["Subject"] = email.subject
        domain = self._from_email.rsplit("@", 1)[-1]
        rfc_message_id = make_msgid(domain=domain)
        message["Message-ID"] = rfc_message_id
        if email.reply_to:
            message["Reply-To"] = email.reply_to
        if email.in_reply_to:
            message["In-Reply-To"] = email.in_reply_to
        if email.references:
            message["References"] = " ".join(email.references)

        message.set_content(email.body_text or _strip_html(email.body_html))
        message.add_alternative(email.body_html, subtype="html")

        result = await asyncio.to_thread(self._send_blocking, message)
        if result.rfc_message_id is None:
            result.rfc_message_id = rfc_message_id
        return result

    # The adapter contract is shared with OAuth providers; SMTP has no OAuth flow.
    def build_authorization_url(self, *, state: str) -> str:
        del state
        raise BadRequestError("SMTP accounts do not use an OAuth authorization flow.")

    async def exchange_code(self, *, code: str) -> OAuthTokens:
        del code
        raise BadRequestError("SMTP accounts do not use an OAuth authorization flow.")

    async def refresh_access_token(self, *, refresh_token: str) -> OAuthTokens:
        del refresh_token
        raise BadRequestError("SMTP accounts do not use OAuth refresh tokens.")

    async def fetch_account_info(self, *, access_token: str) -> ProviderAccountInfo:
        del access_token
        self._require_config()
        return ProviderAccountInfo(email=self._from_email, display_name=self._from_name)


def _strip_html(value: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", value)
