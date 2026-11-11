from __future__ import annotations

import anyio
import workos
from workos.types.sso.profile import ProfileAndToken

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ServiceUnavailableError, UnauthorizedError


class WorkOSSSOClient:
    """Thin async wrapper around the synchronous WorkOS SSO SDK."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.workos_api_key
        self._client_id = settings.workos_client_id
        self._redirect_uri = settings.workos_redirect_uri

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._client_id)

    def _client(self) -> workos.WorkOSClient:
        if not self.configured:
            raise ServiceUnavailableError(
                "WorkOS SSO is not configured. "
                "Set WORKOS_API_KEY and WORKOS_CLIENT_ID to enable it."
            )
        return workos.WorkOSClient(
            api_key=self._api_key,
            client_id=self._client_id,
            request_timeout=20,
        )

    async def authorization_url(
        self,
        *,
        state: str,
        organization_id: str | None = None,
        connection_id: str | None = None,
        login_hint: str | None = None,
        domain_hint: str | None = None,
    ) -> str:
        if not organization_id and not connection_id:
            raise ServiceUnavailableError(
                "This workspace has no WorkOS organization or SSO connection configured."
            )

        def _call() -> str:
            return self._client().sso.get_authorization_url(
                redirect_uri=self._redirect_uri,
                state=state,
                organization_id=organization_id,
                connection_id=connection_id,
                login_hint=login_hint,
                domain_hint=domain_hint,
            )

        try:
            return await anyio.to_thread.run_sync(_call)
        except ServiceUnavailableError:
            raise
        except Exception as exc:  # WorkOS SDK errors are provider-specific
            raise ServiceUnavailableError(
                "WorkOS could not build an SSO authorization URL."
            ) from exc

    async def profile_and_token(self, *, code: str) -> ProfileAndToken:
        def _call() -> ProfileAndToken:
            return self._client().sso.get_profile_and_token(code)

        try:
            return await anyio.to_thread.run_sync(_call)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            raise UnauthorizedError("WorkOS could not validate this SSO sign-in.") from exc


def get_workos_client() -> WorkOSSSOClient:
    return WorkOSSSOClient()
