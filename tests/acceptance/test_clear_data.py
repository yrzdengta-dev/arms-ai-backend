"""Acceptance tests: POST /api/v1/orders/clear — 清空数据 (DEV only)."""
import pytest
from app.core.config import Settings


async def _ingest(client, task_order_id, headers):
    payload = {
        "task_order_id": task_order_id,
        "task_uuid": f"uuid-{task_order_id}",
        "scene_id": "7",
        "audit_point_id": "9",
        "audit_node": "UserAudit_test",
        "order_snapshot": {"skc": "test-skc", "certificate_type_name": "CPC合规信息"},
    }
    r = await client.post("/api/v1/orders/ingest", json=payload, headers=headers)
    assert r.status_code == 200


def _enable_debug(monkeypatch):
    """Enable DEBUG + admin for clear endpoint tests."""
    settings = Settings(DEBUG=True, admin_accounts=["SHEINsgs-5zs"])
    monkeypatch.setattr("app.api.v1.endpoints.orders.get_settings", lambda: settings)
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_clear_orders_deletes_all(client, monkeypatch):
    h = {"X-ARMS-User": "auditor-a"}
    await _ingest(client, "CLEAR-001", h)
    await _ingest(client, "CLEAR-002", h)

    r = await client.get("/api/v1/orders?page_size=5", headers=h)
    assert r.status_code == 200
    assert r.json()["total"] >= 2

    _enable_debug(monkeypatch)

    r = await client.post("/api/v1/orders/clear", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["deleted_orders"] >= 2
    for k in ("deleted_order_events", "deleted_audit_results", "deleted_order_files",
              "deleted_processing_jobs", "deleted_task_outbox"):
        assert k in data

    r = await client.get("/api/v1/orders?page_size=5", headers=h)
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_clear_orders_403_not_debug(client):
    h = {"X-ARMS-User": "auditor-a"}
    r = await client.post("/api/v1/orders/clear", headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_clear_scope_own_only(client, monkeypatch):
    h_a = {"X-ARMS-User": "auditor-a"}
    h_b = {"X-ARMS-User": "auditor-b"}

    await _ingest(client, "CLEAR-S-A1", h_a)
    await _ingest(client, "CLEAR-S-B1", h_b)

    _enable_debug(monkeypatch)

    r = await client.post("/api/v1/orders/clear", headers=h_a)
    assert r.status_code == 200
    assert r.json()["deleted_orders"] >= 1

    r = await client.get("/api/v1/orders", headers=h_a)
    assert r.json()["total"] == 0

    r = await client.get("/api/v1/orders", headers=h_b)
    assert r.json()["total"] >= 1

    # cleanup B
    await client.post("/api/v1/orders/clear", headers=h_b)


@pytest.mark.asyncio
async def test_clear_scope_all_admin(client, monkeypatch):
    h_admin = {"X-ARMS-User": "SHEINsgs-5zs"}

    await _ingest(client, "CLEAR-ADM1", h_admin)

    _enable_debug(monkeypatch)

    r = await client.post("/api/v1/orders/clear?scope=all", headers=h_admin)
    assert r.status_code == 200
    assert r.json()["deleted_orders"] >= 1

    r = await client.get("/api/v1/orders", headers=h_admin)
    assert r.json()["total"] == 0
