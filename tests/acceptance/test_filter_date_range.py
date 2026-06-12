"""Acceptance tests: created_after / created_before date range filter (Feature 2)"""
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import update


@pytest.mark.asyncio
async def test_created_after_filter(client: AsyncClient, db_session):
    """GET /api/v1/orders?created_after=<iso> returns only orders created after that date."""
    from app.models.order import Order

    # Use unique year 2024 to avoid cross-test data leakage
    task_order_ids = ["TN-DR-001", "TN-DR-002", "TN-DR-003"]
    order_ids = []
    for tno in task_order_ids:
        r = await client.post(
            "/api/v1/orders/ingest",
            json={"task_order_id": tno, "order_snapshot": {"skc": "SKC-DR"}, "raw_detail": {}},
            headers={"X-ARMS-User": "auditor-a"},
        )
        assert r.status_code == 200
        order_ids.append(r.json()["order_id"])

    dates = [
        datetime(2024, 1, 10, 12, 0, 0),
        datetime(2024, 2, 15, 12, 0, 0),
        datetime(2024, 3, 20, 12, 0, 0),
    ]
    for oid, dt in zip(order_ids, dates):
        stmt = update(Order).where(Order.id == oid).values(created_at=dt)
        await db_session.execute(stmt)

    # created_after=2024-02-01: should return 2 (Feb 15 and Mar 20)
    r_after = await client.get("/api/v1/orders", params={"created_after": "2024-02-01"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_after.json()["total"] == 2

    # created_after=2024-03-01: should return 1 (Mar 20)
    r_after2 = await client.get("/api/v1/orders", params={"created_after": "2024-03-01"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_after2.json()["total"] == 1

    # created_after far future: 0
    r_future = await client.get("/api/v1/orders", params={"created_after": "2028-01-01"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_future.json()["total"] == 0

    # Cleanup: delete created orders to prevent cross-test leakage
    for oid in order_ids:
        await db_session.execute(sa_delete(Order).where(Order.id == oid))
    await db_session.commit()


@pytest.mark.asyncio
async def test_created_before_filter(client: AsyncClient, db_session):
    """GET /api/v1/orders?created_before=<iso> returns only orders created before that date."""
    from app.models.order import Order

    # Use unique year 2023 to avoid cross-test data leakage
    task_order_ids = ["TN-DR2-01", "TN-DR2-02", "TN-DR2-03"]
    order_ids = []
    for tno in task_order_ids:
        r = await client.post(
            "/api/v1/orders/ingest",
            json={"task_order_id": tno, "order_snapshot": {"skc": "SKC-DR2"}, "raw_detail": {}},
            headers={"X-ARMS-User": "auditor-a"},
        )
        assert r.status_code == 200
        order_ids.append(r.json()["order_id"])

    dates = [
        datetime(2023, 1, 10, 12, 0, 0),
        datetime(2023, 2, 15, 12, 0, 0),
        datetime(2023, 3, 20, 12, 0, 0),
    ]
    for oid, dt in zip(order_ids, dates):
        stmt = update(Order).where(Order.id == oid).values(created_at=dt)
        await db_session.execute(stmt)

    # created_before=2023-02-01: should return 1 (Jan 10)
    r_before = await client.get("/api/v1/orders", params={"created_before": "2023-02-01"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_before.json()["total"] == 1

    # created_before far past: 0
    r_past = await client.get("/api/v1/orders", params={"created_before": "2020-01-01"}, headers={"X-ARMS-User": "auditor-a"})
    assert r_past.json()["total"] == 0

    # Cleanup
    for oid in order_ids:
        await db_session.execute(sa_delete(Order).where(Order.id == oid))
    await db_session.commit()


@pytest.mark.asyncio
async def test_created_date_range_combined(client: AsyncClient, db_session):
    """created_after + created_before together define a closed interval."""
    from app.models.order import Order

    # Use unique year 2022 to avoid cross-test data leakage
    task_order_ids = ["TN-DR3-01", "TN-DR3-02", "TN-DR3-03"]
    order_ids = []
    for tno in task_order_ids:
        r = await client.post(
            "/api/v1/orders/ingest",
            json={"task_order_id": tno, "order_snapshot": {"skc": "SKC-DR3"}, "raw_detail": {}},
            headers={"X-ARMS-User": "auditor-a"},
        )
        assert r.status_code == 200
        order_ids.append(r.json()["order_id"])

    dates = [
        datetime(2022, 1, 10, 12, 0, 0),
        datetime(2022, 2, 15, 12, 0, 0),
        datetime(2022, 3, 20, 12, 0, 0),
    ]
    for oid, dt in zip(order_ids, dates):
        stmt = update(Order).where(Order.id == oid).values(created_at=dt)
        await db_session.execute(stmt)

    # Range 2022-02-01 to 2022-03-15: should return 1 (Feb 15)
    r_range = await client.get(
        "/api/v1/orders",
        params={"created_after": "2022-02-01", "created_before": "2022-03-15"},
        headers={"X-ARMS-User": "auditor-a"},
    )
    assert r_range.json()["total"] == 1

    # Cleanup
    for oid in order_ids:
        await db_session.execute(sa_delete(Order).where(Order.id == oid))
    await db_session.commit()


@pytest.mark.asyncio
async def test_invalid_date_returns_422(client: AsyncClient):
    """Invalid date string should return 422 Unprocessable Entity."""
    r = await client.get("/api/v1/orders", params={"created_after": "not-a-date"}, headers={"X-ARMS-User": "auditor-a"})
    assert r.status_code == 422

    r2 = await client.get("/api/v1/orders", params={"created_before": "2026/01/01"}, headers={"X-ARMS-User": "auditor-a"})
    assert r2.status_code == 422
