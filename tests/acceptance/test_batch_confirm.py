"""Acceptance tests: Batch Confirm API (P0)

Verifies POST /api/v1/orders/batch-confirm:
- Happy path: batch confirm multiple orders
- Idempotent: repeated confirm returns already_confirmed
- Mixed visibility: other user's orders are skipped
- Empty array: 422 validation
- Event emission: order.confirmed event per confirmed order
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.order_event import OrderEvent


BATCH_CONFIRM_URL = "/api/v1/orders/batch-confirm"


class TestBatchConfirmHappyPath:
    @pytest.mark.asyncio
    async def test_batch_confirm_success(self, client: AsyncClient, default_headers):
        """Batch confirm multiple orders, verify response details."""
        # Create 3 orders
        for i in range(1, 4):
            resp = await client.post(
                "/api/v1/orders/ingest",
                json={
                    "task_order_id": f"TN-BC-00{i}",
                    "order_snapshot": {"skc": f"SKC-B{i}"},
                    "raw_detail": {},
                },
                headers=default_headers,
            )
            assert resp.status_code == 200

        # Batch confirm
        resp = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-001", "TN-BC-002", "TN-BC-003"]},
            headers=default_headers,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "results" in data
        assert "summary" in data
        results = data["results"]
        assert len(results) == 3
        for r in results:
            assert r["status"] == "confirmed", f"Expected confirmed, got {r}"
            assert "confirmed_at" in r

        summary = data["summary"]
        assert summary["total"] == 3
        assert summary["confirmed"] == 3
        assert summary.get("already_confirmed", 0) == 0
        assert summary.get("skipped", 0) == 0

    @pytest.mark.asyncio
    async def test_batch_confirm_idempotent(self, client: AsyncClient, default_headers):
        """Repeated batch confirm is idempotent, no errors, marks already_confirmed."""
        await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": "TN-BC-IDEM",
                "order_snapshot": {"skc": "SKC-IDEM"},
                "raw_detail": {},
            },
            headers=default_headers,
        )

        # First confirm
        r1 = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-IDEM"]},
            headers=default_headers,
        )
        assert r1.status_code == 200
        assert r1.json()["results"][0]["status"] == "confirmed"

        # Second confirm (idempotent)
        r2 = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-IDEM"]},
            headers=default_headers,
        )
        assert r2.status_code == 200, f"Idempotent call should return 200, got {r2.status_code}: {r2.text}"
        assert r2.json()["results"][0]["status"] == "already_confirmed"
        assert r2.json()["summary"]["already_confirmed"] == 1
        assert r2.json()["summary"]["confirmed"] == 0


class TestBatchConfirmEdgeCases:
    @pytest.mark.asyncio
    async def test_batch_confirm_mixed_visibility(self, client: AsyncClient, default_headers):
        """Orders from other users are skipped, not blocking the batch."""
        # Create order as auditor-a
        await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": "TN-BC-MINE",
                "order_snapshot": {"skc": "SKC-MINE"},
                "raw_detail": {},
            },
            headers=default_headers,
        )
        # Create order as auditor-b
        await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": "TN-BC-OTHER",
                "order_snapshot": {"skc": "SKC-OTHER"},
                "raw_detail": {},
            },
            headers={"X-ARMS-User": "auditor-b"},
        )

        # Confirm both as auditor-a — auditor-b's should be skipped
        resp = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-MINE", "TN-BC-OTHER", "TN-BC-GHOST"]},
            headers=default_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        results = {r["task_order_id"]: r["status"] for r in data["results"]}

        assert results.get("TN-BC-MINE") == "confirmed"
        assert results.get("TN-BC-OTHER") == "skipped"
        assert results.get("TN-BC-GHOST") == "skipped"

        summary = data["summary"]
        assert summary["confirmed"] == 1
        assert summary["skipped"] == 2

    @pytest.mark.asyncio
    async def test_batch_confirm_known_and_unknown_ids(self, client: AsyncClient, default_headers):
        """Non-existent IDs are skipped, existing ones confirmed."""
        await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": "TN-BC-REAL",
                "order_snapshot": {"skc": "SKC-REAL"},
                "raw_detail": {},
            },
            headers=default_headers,
        )

        resp = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-REAL", "TN-BC-FAKE1", "TN-BC-FAKE2"]},
            headers=default_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        results = {r["task_order_id"]: r["status"] for r in data["results"]}
        assert results.get("TN-BC-REAL") == "confirmed"
        assert results.get("TN-BC-FAKE1") == "skipped"
        assert results.get("TN-BC-FAKE2") == "skipped"

    @pytest.mark.asyncio
    async def test_batch_confirm_empty_ids(self, client: AsyncClient, default_headers):
        """Empty task_order_ids array returns 422."""
        resp = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": []},
            headers=default_headers,
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    @pytest.mark.asyncio
    async def test_batch_confirm_too_many_ids(self, client: AsyncClient, default_headers):
        """Over 200 task_order_ids returns 422."""
        resp = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": [f"TN-{i:05d}" for i in range(201)]},
            headers=default_headers,
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


class TestBatchConfirmEvents:
    @pytest.mark.asyncio
    async def test_batch_confirm_emits_events(self, client: AsyncClient, default_headers, db_session):
        """Each confirmed order creates an order.confirmed event."""
        for i in range(1, 3):
            await client.post(
                "/api/v1/orders/ingest",
                json={
                    "task_order_id": f"TN-BC-EVT-{i}",
                    "order_snapshot": {"skc": f"SKC-EVT{i}"},
                    "raw_detail": {},
                },
                headers=default_headers,
            )

        resp = await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-EVT-1", "TN-BC-EVT-2"]},
            headers=default_headers,
        )
        assert resp.status_code == 200

        # Check events in DB
        stmt = (
            select(OrderEvent)
            .where(OrderEvent.event_type == "order.confirmed")
            .order_by(OrderEvent.id.asc())
        )
        events = (await db_session.execute(stmt)).scalars().all()
        assert len(events) == 2, f"Expected 2 order.confirmed events, got {len(events)}"
        task_ids = {e.payload.get("task_order_id") for e in events}
        assert task_ids == {"TN-BC-EVT-1", "TN-BC-EVT-2"}

    @pytest.mark.asyncio
    async def test_batch_confirm_idempotent_no_duplicate_events(self, client: AsyncClient, default_headers, db_session):
        """Idempotent confirm does not create duplicate events."""
        await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": "TN-BC-EVT-DUP",
                "order_snapshot": {"skc": "SKC-DUP"},
                "raw_detail": {},
            },
            headers=default_headers,
        )

        # First confirm
        await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-EVT-DUP"]},
            headers=default_headers,
        )
        # Count events after first
        stmt = select(OrderEvent).where(OrderEvent.event_type == "order.confirmed")
        count1 = len((await db_session.execute(stmt)).scalars().all())

        # Second confirm (idempotent)
        await client.post(
            BATCH_CONFIRM_URL,
            json={"task_order_ids": ["TN-BC-EVT-DUP"]},
            headers=default_headers,
        )
        count2 = len((await db_session.execute(stmt)).scalars().all())

        assert count2 == count1, f"Idempotent call should not create duplicate events: {count1} -> {count2}"
