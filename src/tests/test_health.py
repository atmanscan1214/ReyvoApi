from __future__ import annotations


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


async def test_readiness(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_request_id_header(client):
    response = await client.get("/health", headers={"X-Request-ID": "test-request-1"})
    assert response.headers["x-request-id"] == "test-request-1"


async def test_scalar_api_reference_is_served(client):
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "scalar" in docs.text.lower()
    assert "/openapi.json" in docs.text

    schema = await client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Reyvo API"

    # The legacy Swagger UI / ReDoc pages are no longer mounted.
    assert (await client.get("/redoc")).status_code == 404
