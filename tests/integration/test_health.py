"""Integration tests: Health and readiness (tests 31-33)"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_returns_200(client: AsyncClient, monkeypatch):
    async def healthy():
        return True

    monkeypatch.setattr("app.core.database.is_db_healthy", healthy)
    monkeypatch.setattr("app.main._check_redis", healthy)
    monkeypatch.setattr("app.main._check_minio", healthy)

    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_ready_returns_503_when_dependency_is_unavailable(
    client: AsyncClient,
    monkeypatch,
):
    async def healthy():
        return True

    async def unhealthy():
        return False

    monkeypatch.setattr("app.core.database.is_db_healthy", healthy)
    monkeypatch.setattr("app.main._check_redis", healthy)
    monkeypatch.setattr("app.main._check_minio", unhealthy)

    resp = await client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["minio"] == "unavailable"


@pytest.mark.asyncio
async def test_api_docs_accessible(client: AsyncClient):
    resp = await client.get("/docs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_openapi_schema(client: AsyncClient):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "paths" in schema
