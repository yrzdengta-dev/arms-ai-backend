"""Acceptance tests: Order idempotency (tests 6-9)"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_duplicate_ingest_creates_one_order(client: AsyncClient, sample_ingest_payload):
    # First ingest
    r1 = await client.post(
        "/api/v1/orders/ingest",
        json=sample_ingest_payload,
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["created"] is True

    # Same payload again
    r2 = await client.post(
        "/api/v1/orders/ingest",
        json=sample_ingest_payload,
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["created"] is False
    assert data2["order_id"] == data1["order_id"]

    # Verify only one order exists
    list_resp = await client.get(
        "/api/v1/orders",
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert list_resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_changed_snapshot_increments_version(client: AsyncClient, sample_ingest_payload):
    r1 = await client.post(
        "/api/v1/orders/ingest",
        json=sample_ingest_payload,
        headers={"X-ARMS-User": "auditor-a"},
    )
    v1 = r1.json()["order_version"]
    assert v1 == 1

    # Change the snapshot
    changed = {**sample_ingest_payload}
    changed["order_snapshot"] = {**sample_ingest_payload["order_snapshot"], "skc": "SKC-002"}
    r2 = await client.post(
        "/api/v1/orders/ingest",
        json=changed,
        headers={"X-ARMS-User": "auditor-a"},
    )
    v2 = r2.json()["order_version"]
    assert v2 > v1


@pytest.mark.asyncio
async def test_blank_x_arms_user_returns_401(client: AsyncClient):
    resp = await client.get(
        "/api/v1/orders",
        headers={"X-ARMS-User": "   "},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_overly_long_x_arms_user_returns_401(client: AsyncClient):
    resp = await client.get(
        "/api/v1/orders",
        headers={"X-ARMS-User": "a" * 129},
    )
    assert resp.status_code == 401
