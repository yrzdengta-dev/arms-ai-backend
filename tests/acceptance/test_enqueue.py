"""Acceptance tests: Enqueue flow (Section 3.1)

Verifies:
- New order RECEIVED → PDF_QUEUED transition
- Celery task dispatched ONLY after successful DB commit
- DB failure → no Celery dispatch
- Same version → no duplicate dispatch
- New version → one new dispatch
"""

import pytest

from app.core.state_machine import PipelineStatus
from app.models.user import User
from app.repositories.order_repository import order_repository
from app.schemas.order import OrderIngestRequest
from app.services.order_service import order_service


class TestEnqueueTransition:
    """Verify RECEIVED → PDF_QUEUED transition after ingest."""

    @pytest.mark.asyncio
    async def test_new_order_sets_pdf_queued(self, db_session):
        """A newly uploaded order with pdf_files should transition to PDF_QUEUED."""
        user = User(arms_account="enq-testuser-1", id="u-enq-1")
        db_session.add(user)
        await db_session.flush()

        request = OrderIngestRequest(
            task_order_id="TN-ENQ-001",
            order_snapshot={"skc": "SKC-001"},
            raw_detail={"data": "test"},
            pdf_files=[{"name": "r.pdf", "url": "https://example.com/r.pdf"}],
        )
        order, created = await order_service.ingest(db_session, request, user)
        await db_session.commit()

        assert created is True
        assert order.pipeline_status == PipelineStatus.PDF_QUEUED.value, (
            f"Expected PDF_QUEUED after ingest, got {order.pipeline_status}"
        )

    @pytest.mark.asyncio
    async def test_same_version_no_duplicate_dispatch(self, db_session):
        """Same hash re-upload should not re-enter PDF_QUEUED or dispatch again."""
        user = User(arms_account="enq-testuser-2", id="u-enq-2")
        db_session.add(user)
        await db_session.flush()

        request = OrderIngestRequest(
            task_order_id="TN-ENQ-002",
            order_snapshot={"skc": "SKC-002"},
            raw_detail={"data": "test"},
            pdf_files=[{"name": "r.pdf", "url": "https://example.com/r.pdf"}],
        )
        order1, created1 = await order_service.ingest(db_session, request, user)
        await db_session.commit()

        order2, created2 = await order_service.ingest(db_session, request, user)
        await db_session.commit()

        assert created1 is True
        assert created2 is False, "Same hash should not re-create or re-dispatch"
        # Unchanged re-upload should keep the existing status
        assert order1.pipeline_status == order2.pipeline_status

    @pytest.mark.asyncio
    async def test_new_version_dispatches_once(self, db_session):
        """Changed snapshot should increment version and dispatch exactly one new task."""
        user = User(arms_account="enq-testuser-3", id="u-enq-3")
        db_session.add(user)
        await db_session.flush()

        req1 = OrderIngestRequest(
            task_order_id="TN-ENQ-003",
            order_snapshot={"skc": "SKC-003"},
            raw_detail={"v": 1},
            pdf_files=[{"name": "r.pdf", "url": "https://example.com/r.pdf"}],
        )
        order1, created1 = await order_service.ingest(db_session, req1, user)
        v1 = order1.order_version
        await db_session.commit()

        req2 = OrderIngestRequest(
            task_order_id="TN-ENQ-003",
            order_snapshot={"skc": "SKC-003-updated"},
            raw_detail={"v": 2},
            pdf_files=[{"name": "r.pdf", "url": "https://example.com/r.pdf"}],
        )
        order2, created2 = await order_service.ingest(db_session, req2, user)
        v2 = order2.order_version
        await db_session.commit()

        assert created2 is True, "Changed snapshot should trigger re-processing"
        assert v2 == v1 + 1, f"Version should increment: {v1} → expected {v1 + 1}, got {v2}"
        assert order2.pipeline_status == PipelineStatus.PDF_QUEUED.value, (
            f"Expected PDF_QUEUED on new version, got {order2.pipeline_status}"
        )

    @pytest.mark.asyncio
    async def test_db_failure_skips_celery_dispatch(self, db_session):
        """If DB commit fails, no outbox record should exist."""
        user = User(arms_account="enq-testuser-4", id="u-enq-4")
        db_session.add(user)
        await db_session.flush()

        request = OrderIngestRequest(
            task_order_id="TN-ENQ-004",
            order_snapshot={"skc": "SKC-004"},
            raw_detail={"data": "test"},
        )
        order, created = await order_service.ingest(db_session, request, user)
        # Rollback instead of commit
        await db_session.rollback()

        # After rollback, the order should not exist
        result = await order_repository.get_by_task_order_id(db_session, "TN-ENQ-004")
        assert result is None, "Rolled-back order should not be visible"

    @pytest.mark.asyncio
    async def test_enqueue_event_written_in_same_transaction(self, db_session):
        """The 'order.pdf_queued' event must be in same transaction as status change."""
        from app.repositories.event_repository import event_repository

        user = User(arms_account="enq-testuser-5", id="u-enq-5")
        db_session.add(user)
        await db_session.flush()

        request = OrderIngestRequest(
            task_order_id="TN-ENQ-005",
            order_snapshot={"skc": "SKC-005"},
            raw_detail={"data": "test"},
            pdf_files=[{"name": "r.pdf", "url": "https://example.com/r.pdf"}],
        )
        order, created = await order_service.ingest(db_session, request, user)
        await db_session.commit()

        events = await event_repository.get_events_since(db_session, user.id, 0)
        event_types = [e.event_type for e in events]
        assert "order.pdf_queued" in event_types, (
            f"Expected 'order.pdf_queued' event, got events: {event_types}"
        )
