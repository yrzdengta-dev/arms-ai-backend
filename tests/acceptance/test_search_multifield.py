"""Acceptance tests: search expanded to SKC / product / supplier (Feature 3)"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_search_by_skc(client: AsyncClient):
    """Search by SKC keyword should match order_snapshot.skc."""
    for i, skc in enumerate(["LAPTOP-X1", "PHONE-Y2", "TABLET-Z3"]):
        r = await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": f"TN-SCH-00{i+1}",
                "order_snapshot": {"skc": skc, "product_name": f"Product {i+1}", "supplier_name": f"Supplier {i+1}"},
                "raw_detail": {},
            },
            headers={"X-ARMS-User": "auditor-a"},
        )
        assert r.status_code == 200

    # Search by SKC keyword
    r = await client.get("/api/v1/orders", params={"search": "LAPTOP"}, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["skc"] == "LAPTOP-X1"

    # Search by partial SKC
    r2 = await client.get("/api/v1/orders", params={"search": "PHONE"}, headers={"X-ARMS-User": "auditor-a"})
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["skc"] == "PHONE-Y2"


@pytest.mark.asyncio
async def test_search_by_supplier(client: AsyncClient):
    """Search by supplier keyword should match order_snapshot.supplier_name."""
    for i, supplier in enumerate(["ACME Corp", "Globex Inc", "Initech LLC"]):
        r = await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": f"TN-SCH2-0{i+1}",
                "order_snapshot": {"skc": f"SKC-{i}", "product_name": f"P{i}", "supplier_name": supplier},
                "raw_detail": {},
            },
            headers={"X-ARMS-User": "auditor-a"},
        )
        assert r.status_code == 200

    # Search by supplier name
    r = await client.get("/api/v1/orders", params={"search": "ACME"}, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["supplier_name"] == "ACME Corp"

    # Search by partial supplier
    r2 = await client.get("/api/v1/orders", params={"search": "Globex"}, headers={"X-ARMS-User": "auditor-a"})
    assert r2.json()["total"] == 1


@pytest.mark.asyncio
async def test_search_by_product_name(client: AsyncClient):
    """Search by product name keyword should match order_snapshot.product_name."""
    r = await client.post(
        "/api/v1/orders/ingest",
        json={
            "task_order_id": "TN-SCH3-01",
            "order_snapshot": {"skc": "SKC-X", "product_name": "Wireless Mouse Pro", "supplier_name": "TechSupply"},
            "raw_detail": {},
        },
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert r.status_code == 200

    resp = await client.get("/api/v1/orders", params={"search": "Mouse"}, headers={"X-ARMS-User": "auditor-a"})
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_search_by_task_order_id_still_works(client: AsyncClient):
    """Search by task_order_id should still work (no regression)."""
    r = await client.post(
        "/api/v1/orders/ingest",
        json={
            "task_order_id": "TN-SCH4-UNIQUE",
            "order_snapshot": {"skc": "SKC-Y", "product_name": "Thing", "supplier_name": "Co"},
            "raw_detail": {},
        },
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert r.status_code == 200

    resp = await client.get("/api/v1/orders", params={"search": "SCH4-UNIQUE"}, headers={"X-ARMS-User": "auditor-a"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["task_order_id"] == "TN-SCH4-UNIQUE"
