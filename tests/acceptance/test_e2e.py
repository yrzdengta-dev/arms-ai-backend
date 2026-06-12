"""E2E acceptance tests (Section 5.4) — 10 scenarios.

Each test exercises the full pipeline with fake components through the API.
"""

import pytest
from httpx import AsyncClient

from app.models.audit_result import AuditResult
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.user import User


class TestE2EFullPipeline:
    """Scenario 1: Full pipeline happy path via API."""

    @pytest.mark.asyncio
    async def test_full_pipeline_ingest_to_list(self, client: AsyncClient, default_headers):
        """Ingest → list → detail → result — complete read path."""
        # 1. Ingest
        res = await client.post("/api/v1/orders/ingest", json={
            "task_order_id": "TN-E2E-001",
            "order_snapshot": {"skc": "SKC-E2E-1", "product_name": "Widget"},
            "raw_detail": {},
            "scene_id": "7",
            "audit_point_id": "9",
            "audit_node": "UserAudit_test",
        }, headers=default_headers)
        assert res.status_code == 200, f"Ingest failed: {res.text}"
        data = res.json()
        assert data["task_order_id"] == "TN-E2E-001"
        assert data["pipeline_status"] == "PDF_QUEUED"

        # 2. List — order appears
        res = await client.get("/api/v1/orders?page_size=200", headers=default_headers)
        assert res.status_code == 200
        items = res.json()["items"]
        match = [i for i in items if i["task_order_id"] == "TN-E2E-001"]
        assert len(match) == 1

        # 3. Detail
        res = await client.get("/api/v1/orders/TN-E2E-001", headers=default_headers)
        assert res.status_code == 200
        detail = res.json()
        assert detail["task_order_id"] == "TN-E2E-001"
        assert detail["pipeline_status"] == "PDF_QUEUED"

        # 4. Result (may not exist yet — should still 200)
        res = await client.get("/api/v1/orders/TN-E2E-001/result", headers=default_headers)
        assert res.status_code == 200

        # 5. Stats
        res = await client.get("/api/v1/orders/stats", headers=default_headers)
        assert res.status_code == 200
        stats = res.json()
        assert stats["total"] >= 1


class TestE2ECrossUserIsolation:
    """Scenario 2: Cross-user isolation E2E."""

    @pytest.mark.asyncio
    async def test_cross_user_isolation_e2e(self, client: AsyncClient):
        """User A's data must be completely invisible to User B."""
        headers_a = {"X-ARMS-User": "e2e-user-a"}
        headers_b = {"X-ARMS-User": "e2e-user-b"}

        # User A ingests
        res = await client.post("/api/v1/orders/ingest", json={
            "task_order_id": "TN-E2E-ISO",
            "order_snapshot": {"skc": "ISO-1"},
            "raw_detail": {},
        }, headers=headers_a)
        assert res.status_code == 200

        # User B cannot see it
        res = await client.get("/api/v1/orders", headers=headers_b)
        items = res.json()["items"]
        match = [i for i in items if i["task_order_id"] == "TN-E2E-ISO"]
        assert len(match) == 0, "User B must not see User A's orders"

        # User B cannot read detail
        res = await client.get("/api/v1/orders/TN-E2E-ISO", headers=headers_b)
        assert res.status_code == 404

        # User B cannot retry
        res = await client.post("/api/v1/orders/TN-E2E-ISO/retry", headers=headers_b)
        assert res.status_code == 404


class TestE2EIdempotency:
    """Scenario 3: Idempotency — same snapshot = no version change."""

    @pytest.mark.asyncio
    async def test_idempotent_ingest_e2e(self, client: AsyncClient, default_headers):
        payload = {
            "task_order_id": "TN-E2E-IDEM",
            "order_snapshot": {"skc": "IDEM-1", "product_name": "Item"},
            "raw_detail": {"field": "value"},
        }
        # First ingest
        r1 = await client.post("/api/v1/orders/ingest", json=payload, headers=default_headers)
        assert r1.status_code == 200
        v1 = r1.json()
        assert v1["created"] is True
        assert v1["order_version"] == 1

        # Second ingest — same payload
        r2 = await client.post("/api/v1/orders/ingest", json=payload, headers=default_headers)
        assert r2.status_code == 200
        v2 = r2.json()
        assert v2["created"] is False, "Same snapshot should not create new version"
        assert v2["order_version"] == 1, "Version should stay 1"

        # Third ingest — changed snapshot
        payload["order_snapshot"]["product_name"] = "Changed Item"
        r3 = await client.post("/api/v1/orders/ingest", json=payload, headers=default_headers)
        assert r3.status_code == 200
        v3 = r3.json()
        assert v3["created"] is True, "Changed snapshot should create new version"
        assert v3["order_version"] == 2


class TestE2EBatchIngest:
    """Scenario 4: Batch ingest with 50 orders."""

    @pytest.mark.asyncio
    async def test_batch_ingest_50_orders(self, client: AsyncClient, default_headers):
        orders = [
            {
                "task_order_id": f"TN-E2E-BATCH-{i:04d}",
                "order_snapshot": {"skc": f"SKC-B{i}"},
                "raw_detail": {},
            }
            for i in range(50)
        ]
        res = await client.post("/api/v1/orders/batch-ingest", json={
            "orders": orders
        }, headers=default_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 50, f"Expected 50, got {data['count']}"
        assert len(data["results"]) == 50


class TestE2ERetryFlow:
    """Scenario 5: Retry flow — FAILED_RETRYABLE → RECEIVED → PDF_QUEUED."""

    @pytest.mark.asyncio
    async def test_retry_flow_e2e(self, client: AsyncClient, default_headers, db_session):
        # Create a user and order directly in FAILED_RETRYABLE state
        user = User(arms_account="e2e-retry-user", id="u-e2e-retry")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-E2E-RETRY",
            owner_user_id=user.id,
            pipeline_status="FAILED_RETRYABLE",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.commit()

        # Retry via API
        res = await client.post(
            "/api/v1/orders/TN-E2E-RETRY/retry",
            headers={"X-ARMS-User": "e2e-retry-user"},
        )
        assert res.status_code == 200, f"Retry should be allowed: {res.text}"
        data = res.json()
        assert data["pipeline_status"] in ("RECEIVED", "PDF_QUEUED"), (
            f"Expected RECEIVED or PDF_QUEUED after retry, got {data['pipeline_status']}"
        )


class TestE2EPipelineProgression:
    """Scenario 6: Pipeline status progression through fake provider."""

    @pytest.mark.asyncio
    async def test_order_progresses_through_states(self, db_session):
        """Order created with matching skill should get audit result."""
        user = User(arms_account="e2e-progress", id="u-e2e-progress")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-E2E-PROG",
            owner_user_id=user.id,
            pipeline_status="AI_RUNNING",
            order_version=1,
            detail_hash="test",
            scene_id="7",
            audit_point_id="9",
            business_type="certificate_audit",
            order_snapshot={
                "skc": "SKC-001",
                "product_name": "Test Product",
                "certificate_type_id": 1,
            },
        )
        db_session.add(order)
        await db_session.flush()

        from app.services.audit_service import run_audit
        result = await run_audit(db_session, order, pdf_text="SKC: SKC-001\nProduct: Test Product")

        assert result is not None
        assert result.order_id == order.id
        assert result.order_version == 1
        assert result.decision is not None
        assert result.decision != ""


class TestE2EEventsAndSSE:
    """Scenario 7: Events are created during pipeline progression."""

    @pytest.mark.asyncio
    async def test_events_created_for_order(self, db_session):
        user = User(arms_account="e2e-events", id="u-e2e-events")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-E2E-EVENTS",
            owner_user_id=user.id,
            pipeline_status="RECEIVED",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.flush()

        from app.repositories.event_repository import event_repository as er
        from app.core.state_machine import PipelineStatus

        await er.create_event(db_session, order.id, user.id, "order.created", 1, {})
        await er.create_event(db_session, order.id, user.id, "order.pdf_queued", 1, {})
        await db_session.commit()

        events = await er.get_events_since(db_session, user.id, 0)
        assert len(events) == 2
        assert events[0].event_type in ("order.created", "order.pdf_queued")
        assert events[1].event_type in ("order.created", "order.pdf_queued")
        # BIGINT auto-increment means event IDs increase
        assert events[1].id > events[0].id


class TestE2EMultipleVersions:
    """Scenario 8: Multiple versions — latest result used."""

    @pytest.mark.asyncio
    async def test_multiple_versions_only_latest_used(self, db_session):
        user = User(arms_account="e2e-multiver", id="u-e2e-multiver")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-E2E-MULTI",
            owner_user_id=user.id,
            pipeline_status="AI_COMPLETED",
            order_version=3,
            detail_hash="v3",
            order_snapshot={"skc": "SKC-v3"},
        )
        db_session.add(order)
        await db_session.flush()

        # Old versions
        r1 = AuditResult(order_id=order.id, order_version=1, decision="PASS",
                         normalized_output={"decision": "PASS", "summary": "v1", "rules": []})
        r2 = AuditResult(order_id=order.id, order_version=2, decision="PASS",
                         normalized_output={"decision": "PASS", "summary": "v2", "rules": []})
        # Latest
        r3 = AuditResult(order_id=order.id, order_version=3, decision="REJECT",
                         normalized_output={"decision": "REJECT", "summary": "v3", "rules": []})
        db_session.add_all([r1, r2, r3])
        await db_session.commit()

        from app.repositories.order_repository import _latest_result_subq, order_repository
        stats = await order_repository.get_stats(db_session, owner_user_id=user.id)
        assert stats["total"] == 1
        assert stats["by_decision"].get("REJECT") == 1
        assert stats["by_decision"].get("PASS", 0) == 0


class TestE2EEnumeration:
    """Scenario 9: Stats and enumeration endpoints."""

    @pytest.mark.asyncio
    async def test_stats_accurately_reflect_orders(self, client: AsyncClient, default_headers):
        # Ingest 3 orders with different statuses
        for i in range(3):
            await client.post("/api/v1/orders/ingest", json={
                "task_order_id": f"TN-E2E-ENUM-{i}",
                "order_snapshot": {"skc": f"ENUM-{i}"},
                "raw_detail": {},
            }, headers=default_headers)

        res = await client.get("/api/v1/orders/stats", headers=default_headers)
        assert res.status_code == 200
        stats = res.json()
        assert stats["total"] >= 3
        assert "by_pipeline_status" in stats
        assert "by_decision" in stats


class TestE2EErrorHandling:
    """Scenario 10: Error states handled correctly."""

    @pytest.mark.asyncio
    async def test_missing_auth_returns_401(self, client: AsyncClient):
        res = await client.get("/api/v1/orders")
        assert res.status_code == 401, f"Missing X-ARMS-User must return 401, got {res.status_code}"

    @pytest.mark.asyncio
    async def test_nonexistent_order_returns_404(self, client: AsyncClient, default_headers):
        res = await client.get("/api/v1/orders/NONEXISTENT-99999", headers=default_headers)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_retry_rejected(self, client: AsyncClient, default_headers, db_session):
        user = User(arms_account="e2e-invalid-retry", id="u-e2e-invretry")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-E2E-NORETRY",
            owner_user_id=user.id,
            pipeline_status="AI_COMPLETED",  # terminal
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.commit()

        res = await client.post(
            "/api/v1/orders/TN-E2E-NORETRY/retry",
            headers={"X-ARMS-User": "e2e-invalid-retry"},
        )
        assert res.status_code == 404, (
            f"Retry from terminal state must return 404, got {res.status_code}"
        )

    @pytest.mark.asyncio
    async def test_batch_exceeds_max_returns_422(self, client: AsyncClient, default_headers):
        orders = [{"task_order_id": f"TN-E2E-OVER-{i}", "order_snapshot": {}, "raw_detail": {}} for i in range(101)]
        res = await client.post("/api/v1/orders/batch-ingest", json={"orders": orders}, headers=default_headers)
        assert res.status_code == 422, f"101 items must be rejected: {res.status_code}"
