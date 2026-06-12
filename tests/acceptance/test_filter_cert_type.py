"""Acceptance tests: cert_type filter (Feature 1)"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_cert_type_filter_single_value(client: AsyncClient):
    """GET /api/v1/orders?cert_type=CPC returns only orders with certificate_type_name=CPC."""
    # Ingest 3 orders with different cert types
    base = {
        "task_uuid": "uuid-ct",
        "order_snapshot": {"skc": "SKC-CT"},
        "raw_detail": {},
    }
    for i, cert_name in enumerate(["CPC", "CE", "FCC"]):
        payload = {
            **base,
            "task_order_id": f"TN-CT-00{i+1}",
            "order_snapshot": {**base["order_snapshot"], "certificate_type_name": cert_name},
        }
        r = await client.post("/api/v1/orders/ingest", json=payload, headers={"X-ARMS-User": "auditor-a"})
        assert r.status_code == 200

    # Without cert_type: all 3
    r_all = await client.get("/api/v1/orders", headers={"X-ARMS-User": "auditor-a"})
    assert r_all.json()["total"] == 3

    # cert_type=CPC: only 1
    r_cpc = await client.get("/api/v1/orders", params={"cert_type": "CPC"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_cpc.json()["total"] == 1
    assert r_cpc.json()["items"][0]["certificate_type_name"] == "CPC"

    # cert_type=CPC,CE: 2 results
    r_multi = await client.get("/api/v1/orders", params={"cert_type": "CPC,CE"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_multi.json()["total"] == 2
    ct_names = {item["certificate_type_name"] for item in r_multi.json()["items"]}
    assert ct_names == {"CPC", "CE"}

    # cert_type=NONEXIST: 0
    r_none = await client.get("/api/v1/orders", params={"cert_type": "NONEXIST"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_none.json()["total"] == 0


@pytest.mark.asyncio
async def test_cert_type_filter_empty_string_ignored(client: AsyncClient):
    """Empty cert_type should be treated as no filter."""
    r = await client.get("/api/v1/orders", params={"cert_type": ""}, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 200
