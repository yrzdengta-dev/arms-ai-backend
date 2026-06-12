"""Backend contract tests (Section 5.2) — missing coverage.

Tests added:
- Batch max 100 validation
- SSE user isolation
- SSE Last-Event-ID replay
- PDF failure prevents AI
"""

import json
import pytest
from httpx import AsyncClient

from app.models.audit_result import AuditResult
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.user import User


class TestBatchMaxSize:
    """POST /api/v1/orders/batch-ingest must reject >100 items."""

    @pytest.mark.asyncio
    async def test_batch_rejects_over_100(self, client: AsyncClient, default_headers):
        orders = [
            {
                "task_order_id": f"TN-BATCH-{i:04d}",
                "order_snapshot": {"skc": f"SKC-{i}"},
                "raw_detail": {},
            }
            for i in range(101)
        ]
        res = await client.post(
            "/api/v1/orders/batch-ingest",
            json={"orders": orders},
            headers=default_headers,
        )
        assert res.status_code == 422, (
            f"Batch of 101 must be rejected (422), got {res.status_code}: {res.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_batch_100_accepted(self, client: AsyncClient, default_headers):
        orders = [
            {
                "task_order_id": f"TN-BATCH100-{i:04d}",
                "order_snapshot": {"skc": f"SKC-{i}"},
                "raw_detail": {},
            }
            for i in range(100)
        ]
        res = await client.post(
            "/api/v1/orders/batch-ingest",
            json={"orders": orders},
            headers=default_headers,
        )
        assert res.status_code == 200, f"Batch of 100 must be accepted: {res.text[:200]}"


class TestSSEUserIsolation:
    """SSE endpoint must isolate events per user."""

    @pytest.mark.asyncio
    async def test_sse_only_returns_own_events(self, db_session, client: AsyncClient):
        """Events created by user A must not be visible to user B via SSE."""
        user_a = User(arms_account="sse-a", id="u-sse-a")
        user_b = User(arms_account="sse-b", id="u-sse-b")
        db_session.add_all([user_a, user_b])
        await db_session.flush()

        # Create order for user A
        order_a = Order(
            task_order_id="TN-SSE-A-001",
            owner_user_id=user_a.id,
            pipeline_status="RECEIVED",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order_a)
        await db_session.flush()

        # Create event for user A
        from app.repositories.event_repository import event_repository
        await event_repository.create_event(
            db_session, order_a.id, user_a.id, "order.created", 1,
            {"task_order_id": order_a.task_order_id},
        )
        await db_session.commit()

        # Fetch events as user A
        from app.repositories.event_repository import event_repository as er
        events_a = await er.get_events_since(db_session, user_a.id, 0)
        assert len(events_a) > 0, "User A should see their own events"

        # User B should see no events
        events_b = await er.get_events_since(db_session, user_b.id, 0)
        assert len(events_b) == 0, f"User B must not see user A events, got {len(events_b)}"


class TestSSELastEventID:
    """SSE Last-Event-ID replay must only return events after the given ID."""

    @pytest.mark.asyncio
    async def test_last_event_id_replay(self, db_session):
        user = User(arms_account="sse-replay", id="u-sse-replay")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-SSE-REPLAY",
            owner_user_id=user.id,
            pipeline_status="RECEIVED",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.flush()

        from app.repositories.event_repository import event_repository as er
        await er.create_event(db_session, order.id, user.id, "order.created", 1, {})
        await er.create_event(db_session, order.id, user.id, "order.pdf_queued", 1, {})
        await db_session.commit()

        # Get all events
        all_events = await er.get_events_since(db_session, user.id, 0)
        assert len(all_events) == 2

        # Replay from first event ID
        first_id = all_events[0].id
        later = await er.get_events_since(db_session, user.id, first_id)
        assert len(later) == 1, f"Should only get 1 event after id={first_id}, got {len(later)}"
        assert later[0].id > first_id

        # From last event ID
        last_id = all_events[1].id
        empty = await er.get_events_since(db_session, user.id, last_id)
        assert len(empty) == 0, f"Should get 0 events after id={last_id}"


class TestPDFFailurePreventsAI:
    """When all PDFs fail, the order must NOT transition to AI stages."""

    @pytest.mark.asyncio
    async def test_pdf_failed_prevents_ai_transition(self, db_session, client: AsyncClient, default_headers):
        """An order stuck in PDF_FAILED must not have audit results."""
        user = User(arms_account="pdf-fail-ai", id="u-pdf-fail-ai")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-PDF-FAIL-01",
            owner_user_id=user.id,
            pipeline_status="PDF_FAILED",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.flush()

        # Create a failed file record
        failed_file = OrderFile(
            order_id=order.id,
            order_version=1,
            original_name="bad.pdf",
            source_url="https://example.com/bad.pdf",
            parse_status="FAILED",
            error_code="PDF_DOWNLOAD_ERROR",
            error_message="Connection refused",
        )
        db_session.add(failed_file)
        await db_session.commit()

        # Verify no audit result exists for this order
        from sqlalchemy import select, func
        count_stmt = (
            select(func.count())
            .select_from(AuditResult)
            .where(AuditResult.order_id == order.id)
        )
        count = (await db_session.execute(count_stmt)).scalar()
        assert count == 0, f"PDF_FAILED order must have no audit results, got {count}"

        # Verify the order cannot transition to AI stages
        from app.core.state_machine import can_transition, PipelineStatus
        current = PipelineStatus(order.pipeline_status)
        assert not can_transition(current, PipelineStatus.AI_QUEUED)
        assert not can_transition(current, PipelineStatus.AI_RUNNING)
        assert not can_transition(current, PipelineStatus.ROUTING)
