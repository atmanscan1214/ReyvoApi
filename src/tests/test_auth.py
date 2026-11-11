from __future__ import annotations


async def test_register_login_and_me(client, register_account, auth_headers):
    tokens = await register_account()
    assert tokens["token_type"] == "bearer"
    assert tokens["tenant"]["name"] == "Acme Staffing"

    me = await client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["role"] == "owner"

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]


async def test_login_rejects_wrong_password(client, register_account):
    await register_account()
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_duplicate_registration_conflicts(client, register_account):
    await register_account()
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "correct-horse-battery-staple",
            "full_name": "Another Owner",
            "agency_name": "Other Agency",
        },
    )
    assert response.status_code == 409


async def test_refresh_rotates_tokens(client, register_account):
    tokens = await register_account()
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed["access_token"] != tokens["access_token"]
    assert refreshed["tenant"]["id"] == tokens["tenant"]["id"]


async def test_me_requires_authentication(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
