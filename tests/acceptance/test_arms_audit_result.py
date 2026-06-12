"""Acceptance tests: ARMS manual audit result fields (arms_audit_*)"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_ingest_with_arms_audit_fields(client: AsyncClient):
    """Ingest with arms_audit_* fields → detail retrieves them, synced_at is set."""
    payload = {
        "task_order_id": "TN-ARMS-001",
        "order_snapshot": {"skc": "SKC-ARMS1"},
        "raw_detail": {},
        "arms_audit_status": "1",
        "arms_audit_result": "驳回",
        "arms_reject_reason": "日期不一致",
    }
    r = await client.post("/api/v1/orders/ingest", json=payload, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 200

    # Detail should return all 4 arms_ fields
    detail = await client.get(f"/api/v1/orders/{payload['task_order_id']}", headers={"X-ARMS-User": "auditor-a"})
    assert detail.status_code == 200
    d = detail.json()
    assert d["arms_audit_status"] == "1"
    assert d["arms_audit_result"] == "驳回"
    assert d["arms_reject_reason"] == "日期不一致"
    assert d["arms_status_synced_at"] is not None


@pytest.mark.asyncio
async def test_reingest_updates_arms_audit_fields(client: AsyncClient):
    """Re-ingest same task_order_id with new arms_audit_result → detail reflects new value."""
    # First ingest
    r1 = await client.post("/api/v1/orders/ingest", json={
        "task_order_id": "TN-ARMS-002",
        "order_snapshot": {"skc": "SKC-ARMS2"},
        "raw_detail": {},
        "arms_audit_status": "1",
        "arms_audit_result": "驳回",
        "arms_reject_reason": "old reason",
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r1.status_code == 200

    # Re-ingest with changed values
    r2 = await client.post("/api/v1/orders/ingest", json={
        "task_order_id": "TN-ARMS-002",
        "order_snapshot": {"skc": "SKC-ARMS2"},
        "raw_detail": {},
        "arms_audit_status": "1",
        "arms_audit_result": "通过",
        "arms_reject_reason": "",
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r2.status_code == 200

    detail = await client.get("/api/v1/orders/TN-ARMS-002", headers={"X-ARMS-User": "auditor-a"})
    assert detail.status_code == 200
    d = detail.json()
    assert d["arms_audit_result"] == "通过"
    assert d["arms_reject_reason"] == ""
    assert d["arms_status_synced_at"] is not None


@pytest.mark.asyncio
async def test_filter_by_arms_audit_result(client: AsyncClient):
    """GET /orders?arms_audit_result=驳回 → returns only rejected, total matches."""
    # Ingest 2 reject + 1 pass
    for i, result in enumerate(["驳回", "通过", "驳回"], 1):
        r = await client.post("/api/v1/orders/ingest", json={
            "task_order_id": f"TN-ARMS-F{i}",
            "order_snapshot": {"skc": f"SKC-F{i}"},
            "raw_detail": {},
            "arms_audit_result": result,
        }, headers={"X-ARMS-User": "auditor-a"})
        assert r.status_code == 200

    # Filter by 驳回
    resp = await client.get("/api/v1/orders", params={"arms_audit_result": "驳回"}, headers={"X-ARMS-User": "auditor-a"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["arms_audit_result"] == "驳回"

    # Filter by 通过
    resp2 = await client.get("/api/v1/orders", params={"arms_audit_result": "通过"}, headers={"X-ARMS-User": "auditor-a"})
    assert resp2.json()["total"] == 1


@pytest.mark.asyncio
async def test_filter_by_arms_audit_status(client: AsyncClient):
    """GET /orders?arms_audit_status=1 → returns only audited orders."""
    for i, status in enumerate(["1", "0", "1"], 1):
        r = await client.post("/api/v1/orders/ingest", json={
            "task_order_id": f"TN-ARMS-S{i}",
            "order_snapshot": {"skc": f"SKC-S{i}"},
            "raw_detail": {},
            "arms_audit_status": status,
        }, headers={"X-ARMS-User": "auditor-a"})
        assert r.status_code == 200

    resp = await client.get("/api/v1/orders", params={"arms_audit_status": "1"}, headers={"X-ARMS-User": "auditor-a"})
    assert resp.json()["total"] == 2

    resp0 = await client.get("/api/v1/orders", params={"arms_audit_status": "0"}, headers={"X-ARMS-User": "auditor-a"})
    assert resp0.json()["total"] == 1


@pytest.mark.asyncio
async def test_ingest_without_arms_audit_fields_is_backward_compatible(client: AsyncClient):
    """Ingest without arms_audit_* fields → all 4 are None, no error."""
    r = await client.post("/api/v1/orders/ingest", json={
        "task_order_id": "TN-ARMS-BC",
        "order_snapshot": {"skc": "SKC-BC"},
        "raw_detail": {},
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 200

    detail = await client.get("/api/v1/orders/TN-ARMS-BC", headers={"X-ARMS-User": "auditor-a"})
    assert detail.status_code == 200
    d = detail.json()
    assert d["arms_audit_status"] is None
    assert d["arms_audit_result"] is None
    assert d["arms_reject_reason"] is None
    assert d["arms_status_synced_at"] is None


@pytest.mark.asyncio
async def test_list_shows_arms_audit_fields(client: AsyncClient):
    """List orders returns arms_audit_status and arms_audit_result in items."""
    r = await client.post("/api/v1/orders/ingest", json={
        "task_order_id": "TN-ARMS-LIST",
        "order_snapshot": {"skc": "SKC-LIST"},
        "raw_detail": {},
        "arms_audit_status": "1",
        "arms_audit_result": "驳回",
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 200

    resp = await client.get("/api/v1/orders", headers={"X-ARMS-User": "auditor-a"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    item = next(i for i in items if i["task_order_id"] == "TN-ARMS-LIST")
    assert item["arms_audit_status"] == "1"
    assert item["arms_audit_result"] == "驳回"


@pytest.mark.asyncio
async def test_combined_arms_filters(client: AsyncClient):
    """Combined arms_audit_status + arms_audit_result → AND semantics, total matches."""
    # Ingest 4 orders with different combinations
    combos = [
        ("TN-ARMS-C1", "1", "驳回"),
        ("TN-ARMS-C2", "1", "通过"),
        ("TN-ARMS-C3", "0", "驳回"),
        ("TN-ARMS-C4", "0", ""),  # empty result, no audit
    ]
    for tid, status, result in combos:
        r = await client.post("/api/v1/orders/ingest", json={
            "task_order_id": tid,
            "scene_id": "ARMS-SCENE",
            "order_snapshot": {"skc": f"SKC-{tid}"},
            "raw_detail": {},
            "arms_audit_status": status,
            "arms_audit_result": result,
        }, headers={"X-ARMS-User": "auditor-a"})
        assert r.status_code == 200

    # status=1 + result=驳回 → only TN-ARMS-C1
    r = await client.get("/api/v1/orders", params={
        "arms_audit_status": "1",
        "arms_audit_result": "驳回",
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["task_order_id"] == "TN-ARMS-C1"

    # status=0 + result=驳回 → only TN-ARMS-C3
    r2 = await client.get("/api/v1/orders", params={
        "arms_audit_status": "0",
        "arms_audit_result": "驳回",
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["task_order_id"] == "TN-ARMS-C3"

    # Combined with scene_id (stable across pipeline lifecycle)
    r3 = await client.get("/api/v1/orders", params={
        "arms_audit_status": "1",
        "scene_id": "ARMS-SCENE",
    }, headers={"X-ARMS-User": "auditor-a"})
    assert r3.json()["total"] == 2  # TN-ARMS-C1 and TN-ARMS-C2
