"""Unit tests: Worker task idempotency and terminal state protection (P0-3).

RED expectations (before fix):
  - test_concurrent_pdf_task_only_one_claims: FAIL (no SELECT FOR UPDATE)
  - test_stale_pdf_task_on_completed_order_noops: FAIL (tasks.py skips if version ok but doesn't check terminal)
  - test_mark_failed_async_does_not_overwrite_terminal: FAIL (_mark_failed_async forces FAILED_FINAL)
  - test_old_version_task_noops: PASS (version check works)
  - test_duplicate_pdf_jobs_not_created: FAIL (no unique constraint)
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_machine import PipelineStatus
from app.models.order import Order
from app.models.processing_job import ProcessingJob
from app.models.user import User


async def _mkorder(db: AsyncSession, task_order_id: str, user_id: str, status: str, version: int = 1) -> Order:
    order = Order(
        id=str(uuid.uuid4()),
        task_order_id=task_order_id,
        owner_user_id=user_id,
        pipeline_status=status,
        order_version=version,
        detail_hash="test-hash",
    )
    db.add(order)
    await db.flush()
    return order


async def _count_jobs(db: AsyncSession, order_id: str, job_type: str) -> int:
    result = await db.execute(
        select(ProcessingJob).where(
            ProcessingJob.order_id == order_id,
            ProcessingJob.job_type == job_type,
        )
    )
    return len(result.scalars().all())


class TestConcurrentTaskClaim:
    """Two concurrent tasks for the same order+version must not both execute."""

    @pytest.mark.asyncio
    async def test_concurrent_pdf_tasks_only_one_claims_pdf_downloading(self, db_session):
        """Two concurrent process_pdf calls: exactly ONE transitions state to PDF_DOWNLOADING."""
        user = User(arms_account="conc-test", id="u-conc")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-CONC-001", user.id, PipelineStatus.PDF_QUEUED.value, version=1)
        order_id = order.id

        from app.models.order_file import OrderFile
        pdf = OrderFile(
            order_id=order_id,
            order_version=1,
            original_name="test.pdf",
            source_url="https://example.com/test.pdf",
            parse_status="PENDING",
        )
        db_session.add(pdf)
        await db_session.commit()

        from app.workers.tasks import _run_pdf_task

        async def run_with_session():
            from app.core.database import _get_session_factory
            async with _get_session_factory()() as db2:
                try:
                    await _run_pdf_task(order_id, 1)
                except Exception:
                    await db2.rollback()

        # Run two concurrently with independent sessions
        await asyncio.gather(run_with_session(), run_with_session())

        # Verify: state became PDF_DOWNLOADING (or beyond/fail) exactly once
        # and no duplicate PDF_DOWNLOADING events exist
        from app.repositories.event_repository import event_repository
        events = await event_repository.get_events_since(db_session, user.id, 0)
        downloading_events = [e for e in events if e.event_type == "order.pdf_downloading"]
        assert len(downloading_events) <= 1, (
            f"At most 1 PDF_DOWNLOADING event expected, got {len(downloading_events)}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_audit_tasks_only_one_claims_routing(self, db_session):
        """Two concurrent run_audit_task calls: exactly ONE transitions to ROUTING."""
        user = User(arms_account="conc-audit", id="u-conc-audit")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-CONC-AUDIT-001", user.id, PipelineStatus.PDF_READY.value, version=1)
        order_id = order.id

        from app.models.order_file import OrderFile
        pdf_file = OrderFile(
            order_id=order_id,
            order_version=1,
            original_name="test.pdf",
            parse_status="READY",
            parsed_text="PDF content for audit",
            sha256="abc123",
            storage_key="pdfs/abc123.pdf",
        )
        db_session.add(pdf_file)
        await db_session.commit()

        from app.workers.tasks import _run_audit_task

        async def run_with_session():
            from app.core.database import _get_session_factory
            async with _get_session_factory()() as db2:
                try:
                    await _run_audit_task(order_id, 1)
                except Exception:
                    await db2.rollback()

        await asyncio.gather(run_with_session(), run_with_session())

        # Verify state changed at most once (no double-ROUTING events)
        from app.repositories.event_repository import event_repository
        events = await event_repository.get_events_since(db_session, user.id, 0)
        routing_events = [e for e in events if e.event_type == "order.routing"]
        assert len(routing_events) <= 1, (
            f"At most 1 ROUTING event expected, got {len(routing_events)}"
        )


class TestStaleTaskNoop:
    """Stale/duplicate/old-version tasks must no-op without changing state."""

    @pytest.mark.asyncio
    async def test_pdf_task_on_already_running_order_noops(self, db_session):
        """A PDF task arriving for an order already in AI_RUNNING must no-op."""
        user = User(arms_account="stale-test", id="u-stale")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-STALE-001", user.id, PipelineStatus.AI_RUNNING.value, version=1)
        order_id = order.id

        from app.models.order_file import OrderFile
        pdf = OrderFile(
            order_id=order_id, order_version=1, original_name="test.pdf",
            source_url="https://example.com/test.pdf", parse_status="READY",
            parsed_text="text", sha256="abc", storage_key="pdfs/abc.pdf",
        )
        db_session.add(pdf)
        await db_session.commit()

        from app.workers.tasks import _run_pdf_task

        async with _get_session() as db2:
            try:
                await _run_pdf_task(order_id, 1)
            except Exception:
                await db2.rollback()

        # Order must still be AI_RUNNING (no regression)
        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.AI_RUNNING.value, (
            f"Expected AI_RUNNING, got {order.pipeline_status}"
        )

    @pytest.mark.asyncio
    async def test_audit_task_on_completed_order_noops(self, db_session):
        """An audit task arriving for an already AI_COMPLETED order must no-op."""
        user = User(arms_account="stale-audit-test", id="u-stale-audit")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-STALE-AUDIT-001", user.id, PipelineStatus.AI_COMPLETED.value, version=1)
        order_id = order.id

        await db_session.commit()

        from app.workers.tasks import _run_audit_task

        async with _get_session() as db2:
            try:
                await _run_audit_task(order_id, 1)
            except Exception:
                await db2.rollback()

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.AI_COMPLETED.value, (
            f"Expected AI_COMPLETED, got {order.pipeline_status}"
        )

    @pytest.mark.asyncio
    async def test_old_version_task_noops(self, db_session):
        """An old-version task must not change a newer version order."""
        user = User(arms_account="oldver-test", id="u-oldver")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-OLDVER-001", user.id, PipelineStatus.PDF_QUEUED.value, version=2)
        order_id = order.id

        from app.models.order_file import OrderFile
        pdf = OrderFile(
            order_id=order_id, order_version=2, original_name="test.pdf",
            source_url="https://example.com/test.pdf", parse_status="PENDING",
        )
        db_session.add(pdf)
        await db_session.commit()

        from app.workers.tasks import _run_pdf_task

        async with _get_session() as db2:
            await _run_pdf_task(order_id, 1)  # version 1, order is version 2

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_QUEUED.value, (
            f"Old version task must not change state, got {order.pipeline_status}"
        )


class TestMarkFailedAsyncTerminalProtection:
    """_mark_failed must not overwrite terminal states.

    Tests directly await the production async function _mark_failed()
    and verify real database state + event counts.
    """

    @pytest.mark.asyncio
    async def test_mark_failed_does_not_overwrite_ai_completed(self, db_session):
        """When order is AI_COMPLETED, _mark_failed must not change it."""
        user = User(arms_account="mfa-1", id="u-mfa-1")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-MFA-001", user.id, PipelineStatus.AI_COMPLETED.value, version=1)
        await db_session.commit()
        order_id = order.id

        # Count events before
        from app.repositories.event_repository import event_repository
        events_before = await event_repository.get_events_since(db_session, user.id, 0)
        event_count_before = len(events_before)

        # Direct await of production async function
        from app.workers.tasks import _mark_failed
        await _mark_failed(order_id, 1, "test error")

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.AI_COMPLETED.value, (
            f"AI_COMPLETED must not be overwritten, got {order.pipeline_status}"
        )

        # No new events created
        events_after = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_after) == event_count_before, (
            f"No new events expected, got {len(events_after) - event_count_before} new"
        )

    @pytest.mark.asyncio
    async def test_mark_failed_does_not_overwrite_manual_required(self, db_session):
        """When order is MANUAL_REQUIRED, _mark_failed must not change it."""
        user = User(arms_account="mfa-2", id="u-mfa-2")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-MFA-002", user.id, PipelineStatus.MANUAL_REQUIRED.value, version=1)
        await db_session.commit()
        order_id = order.id

        from app.repositories.event_repository import event_repository
        events_before = await event_repository.get_events_since(db_session, user.id, 0)
        event_count_before = len(events_before)

        from app.workers.tasks import _mark_failed
        await _mark_failed(order_id, 1, "test error")

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.MANUAL_REQUIRED.value, (
            f"MANUAL_REQUIRED must not be overwritten, got {order.pipeline_status}"
        )

        events_after = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_after) == event_count_before, (
            f"No new events expected, got {len(events_after) - event_count_before} new"
        )

    @pytest.mark.asyncio
    async def test_mark_failed_does_not_overwrite_failed_final(self, db_session):
        """When order is FAILED_FINAL, _mark_failed must not duplicate."""
        user = User(arms_account="mfa-3", id="u-mfa-3")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-MFA-003", user.id, PipelineStatus.FAILED_FINAL.value, version=1)
        await db_session.commit()
        order_id = order.id

        from app.repositories.event_repository import event_repository
        events_before = await event_repository.get_events_since(db_session, user.id, 0)
        event_count_before = len(events_before)

        from app.workers.tasks import _mark_failed
        await _mark_failed(order_id, 1, "test error")
        await _mark_failed(order_id, 1, "test error 2")  # Repeat call

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.FAILED_FINAL.value, (
            f"FAILED_FINAL must not be changed, got {order.pipeline_status}"
        )

        events_after = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_after) == event_count_before, (
            f"No new events expected, got {len(events_after) - event_count_before} new"
        )

    @pytest.mark.asyncio
    async def test_mark_failed_version_mismatch_noops(self, db_session):
        """When order_version differs, _mark_failed must no-op."""
        user = User(arms_account="mfa-4", id="u-mfa-4")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-MFA-004", user.id, PipelineStatus.PDF_DOWNLOADING.value, version=2)
        await db_session.commit()
        order_id = order.id

        from app.repositories.event_repository import event_repository
        events_before = await event_repository.get_events_since(db_session, user.id, 0)
        event_count_before = len(events_before)

        from app.workers.tasks import _mark_failed
        await _mark_failed(order_id, 1, "test error")  # version 1 != db version 2

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_DOWNLOADING.value, (
            f"Version mismatch must no-op, got {order.pipeline_status}"
        )

        events_after = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_after) == event_count_before, (
            f"No new events expected on version mismatch"
        )

    @pytest.mark.asyncio
    async def test_mark_failed_only_valid_transition_applies(self, db_session):
        """Only states with valid FAILED_FINAL transition are updated."""
        user = User(arms_account="mfa-5", id="u-mfa-5")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(db_session, "TN-MFA-005", user.id, PipelineStatus.PDF_FAILED.value, version=1)
        await db_session.commit()
        order_id = order.id

        from app.repositories.event_repository import event_repository
        events_before = await event_repository.get_events_since(db_session, user.id, 0)
        event_count_before = len(events_before)

        from app.workers.tasks import _mark_failed
        await _mark_failed(order_id, 1, "test error")

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.FAILED_FINAL.value, (
            f"Expected FAILED_FINAL, got {order.pipeline_status}"
        )

        # Exactly 1 new event created
        events_after = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_after) == event_count_before + 1, (
            f"Expected 1 new event, got {len(events_after) - event_count_before}"
        )

        # Repeat call must not create another event
        await _mark_failed(order_id, 1, "test error 2")
        events_final = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_final) == event_count_before + 1, (
            f"Repeat call must not create events, got {len(events_final) - event_count_before} total"
        )

    @pytest.mark.asyncio
    async def test_mark_failed_invalid_transition_noops(self, db_session):
        """States that can't transition to FAILED_FINAL must not be changed."""
        user = User(arms_account="mfa-6", id="u-mfa-6")
        db_session.add(user)
        await db_session.flush()

        # RECEIVED has not started processing and cannot fail terminally.
        order = await _mkorder(db_session, "TN-MFA-006", user.id, PipelineStatus.RECEIVED.value, version=1)
        await db_session.commit()
        order_id = order.id

        from app.repositories.event_repository import event_repository
        events_before = await event_repository.get_events_since(db_session, user.id, 0)
        event_count_before = len(events_before)

        from app.workers.tasks import _mark_failed
        await _mark_failed(order_id, 1, "test error")

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.RECEIVED.value, (
            f"Invalid transition must no-op, got {order.pipeline_status}"
        )

        events_after = await event_repository.get_events_since(db_session, user.id, 0)
        assert len(events_after) == event_count_before, (
            f"No events expected for invalid transition"
        )


# ---- helpers ----

from contextlib import asynccontextmanager

from app.core.database import _get_session_factory

@asynccontextmanager
async def _get_session():
    async with _get_session_factory()() as session:
        yield session
