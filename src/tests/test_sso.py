from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from reyvo.core.security import create_sso_state
from reyvo.services import sso_service

AUTHORIZE_URL = "https://api.workos.com/sso/authorize?client_id=client_test_workos"


def make_profile(**overrides):
    data = {
        "id": "prof_workos_123",
        "email": "new.user@acme.com",
        "first_name": "New",
        "last_name": "User",
        "organization_id": "org_acme",
        "connection_id": "conn_acme",
        "role": SimpleNamespace(slug="admin"),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeWorkOSClient:
    def __init__(self) -> None:
        self.url = AUTHORIZE_URL
        self.profile = make_profile()
        self.authorize_kwargs: dict | None = None
        self.codes: list[str] = []

    async def authorization_url(self, **kwargs) -> str:
        self.authorize_kwargs = kwargs
        return self.url

    async def profile_and_token(self, *, code: str):
        self.codes.append(code)
        return SimpleNamespace(profile=self.profile)


@pytest.fixture
def workos(monkeypatch) -> FakeWorkOSClient:
    fake = FakeWorkOSClient()
    monkeypatch.setattr(sso_service, "get_workos_client", lambda: fake)
    return fake


async def enable_sso(
    client,
    auth_headers,
    tokens,
    *,
    organization_id: str = "org_acme",
    connection_id: str | None = "conn_acme",
):
    response = await client.patch(
        "/api/v1/tenants/current/sso",
        json={
            "workos_organization_id": organization_id,
            "workos_sso_connection_id": connection_id,
        },
        headers=auth_headers(tokens),
    )
    assert response.status_code == 200, response.text
    return response.json()


def state_for(tokens) -> str:
    return create_sso_state(tenant_id=uuid.UUID(tokens["tenant"]["id"]))


async def test_sso_status_defaults_to_disabled(client, register_account, auth_headers):
    tokens = await register_account()
    response = await client.get("/api/v1/tenants/current/sso", headers=auth_headers(tokens))
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "workos"
    assert body["available"] is True
    assert body["configured"] is False
    assert body["enabled"] is False


async def test_sso_settings_update_and_disable(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    body = await enable_sso(client, auth_headers, tokens)
    assert body["configured"] is True
    assert body["enabled"] is True
    assert body["organization_id"] == "org_acme"

    # keep the connection but clear the organization
    response = await client.patch(
        "/api/v1/tenants/current/sso",
        json={"workos_organization_id": None},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["organization_id"] is None
    assert response.json()["connection_id"] == "conn_acme"
    assert response.json()["enabled"] is True

    disabled = await client.delete("/api/v1/tenants/current/sso", headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["configured"] is False
    assert disabled.json()["enabled"] is False


async def test_sso_organization_cannot_be_linked_twice(client, register_account, auth_headers):
    first = await register_account()
    await enable_sso(client, auth_headers, first)

    second = await register_account(email="second@example.com", agency_name="Second Staffing")
    response = await client.patch(
        "/api/v1/tenants/current/sso",
        json={"workos_organization_id": "org_acme"},
        headers=auth_headers(second),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_authorize_requires_tenant_sso_configuration(
    client, register_account, auth_headers, workos
):
    tokens = await register_account()
    response = await client.get(
        "/api/v1/auth/sso/authorize", params={"tenant_slug": tokens["tenant"]["slug"]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_authorize_returns_workos_url(client, register_account, auth_headers, workos):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)

    response = await client.get(
        "/api/v1/auth/sso/authorize",
        params={
            "tenant_slug": tokens["tenant"]["slug"],
            "login_hint": "new.user@acme.com",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authorization_url"] == AUTHORIZE_URL
    assert body["state"]
    assert workos.authorize_kwargs is not None
    assert workos.authorize_kwargs["organization_id"] == "org_acme"
    assert workos.authorize_kwargs["connection_id"] == "conn_acme"
    assert workos.authorize_kwargs["login_hint"] == "new.user@acme.com"


async def test_authorize_rejects_foreign_return_to(client, register_account, auth_headers, workos):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)

    response = await client.get(
        "/api/v1/auth/sso/authorize",
        params={
            "tenant_slug": tokens["tenant"]["slug"],
            "return_to": "https://evil.example.com/steal",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_callback_provisions_new_sso_user(client, register_account, auth_headers, workos):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)

    response = await client.post(
        "/api/v1/auth/sso/callback",
        json={"code": "auth_code_1", "state": state_for(tokens)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant"]["id"] == tokens["tenant"]["id"]
    assert workos.codes == ["auth_code_1"]

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "new.user@acme.com"
    assert me.json()["user"]["full_name"] == "New User"
    # The workspace already had an owner, so the WorkOS role is used.
    assert me.json()["role"] == "admin"


async def test_callback_links_existing_local_account(
    client, register_account, auth_headers, workos
):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)
    original_me = await client.get("/api/v1/auth/me", headers=auth_headers(tokens))

    workos.profile = make_profile(email="owner@example.com", id="prof_owner")

    response = await client.post(
        "/api/v1/auth/sso/callback",
        json={"code": "auth_code_owner", "state": state_for(tokens)},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.json()["user"]["id"] == original_me.json()["user"]["id"]
    assert me.json()["role"] == "owner"

    # Local password authentication still works after linking WorkOS.
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "owner@example.com",
            "password": "correct-horse-battery-staple",
        },
    )
    assert login.status_code == 200


async def test_callback_rejects_workspace_mismatch(client, register_account, auth_headers, workos):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)
    workos.profile = make_profile(organization_id="org_someone_else")

    response = await client.post(
        "/api/v1/auth/sso/callback",
        json={"code": "auth_code_2", "state": state_for(tokens)},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_callback_rejects_connection_mismatch(client, register_account, auth_headers, workos):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)
    workos.profile = make_profile(connection_id="conn_other")

    response = await client.post(
        "/api/v1/auth/sso/callback",
        json={"code": "auth_code_3", "state": state_for(tokens)},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_callback_rejects_invalid_state(client, workos):
    response = await client.post(
        "/api/v1/auth/sso/callback",
        json={"code": "auth_code_4", "state": "not-a-signed-state"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_get_callback_redirects_with_tokens(client, register_account, auth_headers, workos):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)

    authorize = await client.get(
        "/api/v1/auth/sso/authorize",
        params={
            "tenant_slug": tokens["tenant"]["slug"],
            "return_to": "http://localhost:3000/sso/done",
        },
    )
    assert authorize.status_code == 200
    state = authorize.json()["state"]

    response = await client.get(
        "/api/v1/auth/sso/callback",
        params={"code": "auth_code_redirect", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == ("http://localhost:3000/sso/done")
    fragment = parse_qs(parsed.fragment)
    assert fragment["token_type"] == ["bearer"]
    assert fragment["tenant"] == [tokens["tenant"]["slug"]]
    assert fragment["access_token"][0]


async def test_sso_unavailable_when_platform_not_configured(
    client, register_account, auth_headers, app, workos, monkeypatch
):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)
    monkeypatch.setattr(app.state.settings, "workos_api_key", None)

    status = await client.get("/api/v1/tenants/current/sso", headers=auth_headers(tokens))
    assert status.status_code == 200
    assert status.json()["available"] is False
    assert status.json()["enabled"] is False

    response = await client.get(
        "/api/v1/auth/sso/authorize",
        params={"tenant_slug": tokens["tenant"]["slug"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_local_login_unaffected_by_sso_platform_configuration(
    client, register_account, auth_headers, workos
):
    tokens = await register_account()
    await enable_sso(client, auth_headers, tokens)

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "owner@example.com",
            "password": "correct-horse-battery-staple",
        },
    )
    assert login.status_code == 200
    assert login.json()["tenant"]["id"] == tokens["tenant"]["id"]
