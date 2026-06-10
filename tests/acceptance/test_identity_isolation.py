"""Acceptance tests: Identity isolation (tests 1-5)"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_missing_x_arms_user_returns_401(client: AsyncClient):
    response = await client.get("/api/v1/orders")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_a_orders_not_visible_to_user_b(client: AsyncClient, sample_ingest_payload):
    # User A uploads
    ingest_resp = await client.post(
        "/api/v1/orders/ingest",
        json=sample_ingest_payload,
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert ingest_resp.status_code == 200

    # User B cannot see it
    list_resp = await client.get(
        "/api/v1/orders",
        headers={"X-ARMS-User": "auditor-b"},
    )
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_user_b_cannot_read_user_a_order(client: AsyncClient, sample_ingest_payload):
    # User A uploads
    await client.post(
        "/api/v1/orders/ingest",
        json=sample_ingest_payload,
        headers={"X-ARMS-User": "auditor-a"},
    )

    # User B tries to read
    resp = await client.get(
        f"/api/v1/orders/{sample_ingest_payload['task_order_id']}",
        headers={"X-ARMS-User": "auditor-b"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_b_cannot_retry_user_a_order(client: AsyncClient, sample_ingest_payload):
    await client.post(
        "/api/v1/orders/ingest",
        json=sample_ingest_payload,
        headers={"X-ARMS-User": "auditor-a"},
    )

    resp = await client.post(
        f"/api/v1/orders/{sample_ingest_payload['task_order_id']}/retry",
        headers={"X-ARMS-User": "auditor-b"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_request_body_owner_user_id_is_ignored(client: AsyncClient, sample_ingest_payload):
    payload = {**sample_ingest_payload, "owner_user_id": "hacked-user"}
    resp = await client.post(
        "/api/v1/orders/ingest",
        json=payload,
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert resp.status_code == 200

    detail = await client.get(
        f"/api/v1/orders/{payload['task_order_id']}",
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert detail.status_code == 200
    # owner_user_id in response should not be the hacked value
    data = detail.json()
    assert data["owner_user_id"] != "hacked-user"
