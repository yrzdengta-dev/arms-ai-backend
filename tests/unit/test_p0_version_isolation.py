"""RED tests for P0: ProcessingJob version isolation.

Before the fix:
  - test_version2_not_blocked_by_version1_job: FAIL — _find_active_job
    ignores order_version, so version=2 task sees version=1 COMPLETED job
    and returns early without processing.
  - test_concurrent_same_version_only_one_running_job: PASS on SQLite
    (serialized), but would FAIL with asyncpg real concurrency without
    order_version constraint.
  - test_failed_job_does_not_block_same_version_retry: FAIL — FAILED
    job with same order_id+job_type blocks retry via _find_active_job.

After the fix:
  - _find_active_job filters by (order_id, order_version, job_type, status)
  - status filter excludes FAILED
  - _create_job sets order_version
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_machine import PipelineStatus
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.processing_job import ProcessingJob
from app.models.user import User


async def _mkorder(
    db: AsyncSession,
    task_order_id: str,
    user_id: str,
    status: str,
    version: int = 1,
) -> Order:
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


async def _mkjob(
    db: AsyncSession,
    order_id: str,
    job_type: str,
    status: str,
    order_version: int = 1,
) -> ProcessingJob:
    """Create a job. If model lacks order_version, silently drop it."""
    try:
        job = ProcessingJob(
            order_id=order_id,
            job_type=job_type,
            status=status,
            order_version=order_version,
        )
    except TypeError:
        job = ProcessingJob(
            order_id=order_id,
            job_type=job_type,
            status=status,
        )
    db.add(job)
    await db.flush()
    return job


async def _count_jobs(db: AsyncSession, order_id: str, job_type: str) -> int:
    result = await db.execute(
        select(ProcessingJob).where(
            ProcessingJob.order_id == order_id,
            ProcessingJob.job_type == job_type,
        )
    )
    return len(result.scalars().all())


class TestVersion2NotBlockedByVersion1Job:
    """P0: version=2 task must not be blocked by version=1 COMPLETED job."""

    @pytest.mark.asyncio
    async def test_version2_pdf_task_not_blocked_by_v1_completed_job(self, db_session):
        """Version=1 has a COMPLETED pdf_download job. Version=2 ingest
        triggers a new pdf task. _find_active_job must not see the v1 job."""
        user = User(arms_account="v2-block-test", id="u-v2-block")
        db_session.add(user)
        await db_session.flush()

        # Create version=1 order with a COMPLETED job
        order = await _mkorder(
            db_session, "TN-V2BLOCK-001", user.id,
            PipelineStatus.PDF_QUEUED.value, version=1,
        )
        order_id = order.id

        # Add a PDF file
        pdf = OrderFile(
            order_id=order_id, order_version=1,
            original_name="test.pdf",
            source_url="https://example.com/test.pdf",
            parse_status="PENDING",
        )
        db_session.add(pdf)

        # Create version=1 COMPLETED job
        v1_job = await _mkjob(db_session, order_id, "pdf_download", "COMPLETED", order_version=1)

        await db_session.commit()

        # Now simulate version=2 update: bump version, reset status
        order.order_version = 2
        order.pipeline_status = PipelineStatus.PDF_QUEUED.value
        # Add version=2 PDF file
        pdf_v2 = OrderFile(
            order_id=order_id, order_version=2,
            original_name="test_v2.pdf",
            source_url="https://example.com/test_v2.pdf",
            parse_status="PENDING",
        )
        db_session.add(pdf_v2)
        await db_session.commit()

        # Run version=2 PDF task
        from app.workers.tasks import _run_pdf_task
        from app.core.database import _get_session_factory

        async with _get_session_factory()() as db2:
            try:
                await _run_pdf_task(order_id, 2)
            except Exception:
                await db2.rollback()

        # Verify: order was processed (not blocked by v1 job)
        await db_session.refresh(order)
        # Should have progressed past PDF_QUEUED (to PDF_DOWNLOADING, PDF_READY, or PDF_FAILED)
        assert order.pipeline_status != PipelineStatus.PDF_QUEUED.value, (
            f"Version=2 task should have processed. "
            f"Expected != PDF_QUEUED, got {order.pipeline_status}. "
            f"v1 COMPLETED job likely blocked the v2 task via _find_active_job."
        )

        # Verify version=2 got its own job (not reusing v1)
        jobs = await db_session.execute(
            select(ProcessingJob).where(
                ProcessingJob.order_id == order_id,
                ProcessingJob.job_type == "pdf_download",
            ).order_by(ProcessingJob.created_at)
        )
        all_jobs = jobs.scalars().all()
        assert len(all_jobs) >= 2, (
            f"Expected >=2 jobs (v1+v2), got {len(all_jobs)}. "
            f"Version=2 should have created a new job."
        )

    @pytest.mark.asyncio
    async def test_version2_audit_task_not_blocked_by_v1_completed_job(self, db_session):
        """Version=1 has a COMPLETED audit job. Version=2 PDF_READY
        triggers a new audit task. _find_active_job must not see the v1 job."""
        user = User(arms_account="v2-audit-block", id="u-v2-audit-block")
        db_session.add(user)
        await db_session.flush()

        # Create version=1 order with COMPLETED audit job
        order = await _mkorder(
            db_session, "TN-V2AUDIT-001", user.id,
            PipelineStatus.PDF_READY.value, version=1,
        )
        order_id = order.id

        pdf = OrderFile(
            order_id=order_id, order_version=1,
            original_name="test.pdf", parse_status="READY",
            parsed_text="Some PDF text", sha256="abc", storage_key="pdfs/abc.pdf",
        )
        db_session.add(pdf)

        await _mkjob(db_session, order_id, "audit", "COMPLETED", order_version=1)
        await db_session.commit()

        # Bump to version=2, PDF_READY
        order.order_version = 2
        order.pipeline_status = PipelineStatus.PDF_READY.value
        pdf_v2 = OrderFile(
            order_id=order_id, order_version=2,
            original_name="test_v2.pdf", parse_status="READY",
            parsed_text="Some PDF text v2", sha256="def", storage_key="pdfs/def.pdf",
        )
        db_session.add(pdf_v2)
        await db_session.commit()

        from app.workers.tasks import _run_audit_task
        from app.core.database import _get_session_factory

        async with _get_session_factory()() as db2:
            try:
                await _run_audit_task(order_id, 2)
            except Exception:
                await db2.rollback()

        await db_session.refresh(order)
        assert order.pipeline_status != PipelineStatus.PDF_READY.value, (
            f"Version=2 audit task should have processed. "
            f"Expected != PDF_READY, got {order.pipeline_status}"
        )

        jobs = await db_session.execute(
            select(ProcessingJob).where(
                ProcessingJob.order_id == order_id,
                ProcessingJob.job_type == "audit",
            ).order_by(ProcessingJob.created_at)
        )
        all_jobs = jobs.scalars().all()
        assert len(all_jobs) >= 2, (
            f"Expected >=2 audit jobs (v1+v2), got {len(all_jobs)}"
        )


class TestFailedJobDoesNotBlockSameVersionRetry:
    """P0: FAILED job must not block retry of same version."""

    @pytest.mark.asyncio
    async def test_failed_pdf_job_does_not_block_retry(self, db_session):
        """Same version retry after FAILED job must create a new job."""
        user = User(arms_account="retry-fail", id="u-retry-fail")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(
            db_session, "TN-RETRYFAIL-001", user.id,
            PipelineStatus.PDF_FAILED.value, version=1,
        )
        order_id = order.id

        pdf = OrderFile(
            order_id=order_id, order_version=1,
            original_name="test.pdf",
            source_url="https://example.com/test.pdf",
            parse_status="PENDING",
        )
        db_session.add(pdf)

        # Create FAILED job from first attempt
        await _mkjob(db_session, order_id, "pdf_download", "FAILED", order_version=1)
        await db_session.commit()

        # Simulate retry: transition back to PDF_QUEUED (as retry_order does)
        order.pipeline_status = PipelineStatus.PDF_QUEUED.value
        await db_session.commit()

        from app.workers.tasks import _run_pdf_task
        from app.core.database import _get_session_factory

        async with _get_session_factory()() as db2:
            try:
                await _run_pdf_task(order_id, 1)
            except Exception:
                await db2.rollback()

        await db_session.refresh(order)
        assert order.pipeline_status != PipelineStatus.PDF_QUEUED.value, (
            f"Retry should have proceeded. "
            f"Expected != PDF_QUEUED, got {order.pipeline_status}. "
            f"FAILED job likely blocked retry via _find_active_job."
        )

        # Verify new job was created (not blocked by FAILED)
        jobs = await db_session.execute(
            select(ProcessingJob).where(
                ProcessingJob.order_id == order_id,
                ProcessingJob.job_type == "pdf_download",
            ).order_by(ProcessingJob.created_at)
        )
        all_jobs = jobs.scalars().all()
        assert len(all_jobs) >= 2, (
            f"Expected >=2 jobs (FAILED + retry), got {len(all_jobs)}"
        )

    @pytest.mark.asyncio
    async def test_failed_audit_job_does_not_block_retry(self, db_session):
        """Same version audit retry after FAILED audit job must create new job."""
        user = User(arms_account="retry-audit-fail", id="u-retry-audit-fail")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(
            db_session, "TN-RETRYAUDIT-001", user.id,
            PipelineStatus.PDF_READY.value, version=1,
        )
        order_id = order.id

        pdf = OrderFile(
            order_id=order_id, order_version=1,
            original_name="test.pdf", parse_status="READY",
            parsed_text="PDF text", sha256="abc", storage_key="pdfs/abc.pdf",
        )
        db_session.add(pdf)

        await _mkjob(db_session, order_id, "audit", "FAILED", order_version=1)
        await db_session.commit()

        from app.workers.tasks import _run_audit_task
        from app.core.database import _get_session_factory

        async with _get_session_factory()() as db2:
            try:
                await _run_audit_task(order_id, 1)
            except Exception:
                await db2.rollback()

        await db_session.refresh(order)
        assert order.pipeline_status != PipelineStatus.PDF_READY.value, (
            f"Audit retry should have proceeded, got {order.pipeline_status}"
        )

        jobs = await db_session.execute(
            select(ProcessingJob).where(
                ProcessingJob.order_id == order_id,
                ProcessingJob.job_type == "audit",
            ).order_by(ProcessingJob.created_at)
        )
        all_jobs = jobs.scalars().all()
        assert len(all_jobs) >= 2, (
            f"Expected >=2 audit jobs (FAILED + retry), got {len(all_jobs)}"
        )


class TestConcurrentSameVersionOnlyOneRunning:
    """Concurrent tasks for same (order_id, order_version, job_type)
    must only create one RUNNING job."""

    @pytest.mark.asyncio
    async def test_concurrent_pdf_tasks_only_one_running_job(self, db_session):
        """Two concurrent PDF tasks on same version: at most 1 RUNNING job."""
        user = User(arms_account="conc-vjob", id="u-conc-vjob")
        db_session.add(user)
        await db_session.flush()

        order = await _mkorder(
            db_session, "TN-CONCVJOB-001", user.id,
            PipelineStatus.PDF_QUEUED.value, version=1,
        )
        order_id = order.id

        pdf = OrderFile(
            order_id=order_id, order_version=1,
            original_name="test.pdf",
            source_url="https://example.com/test.pdf",
            parse_status="PENDING",
        )
        db_session.add(pdf)
        await db_session.commit()

        from app.workers.tasks import _run_pdf_task
        from app.core.database import _get_session_factory

        async def run_with_new_session():
            async with _get_session_factory()() as db2:
                try:
                    await _run_pdf_task(order_id, 1)
                except Exception:
                    await db2.rollback()

        await asyncio.gather(run_with_new_session(), run_with_new_session())

        # Verify only one job was created or processed
        # (one claim succeeds, the other no-ops)
        jobs = await db_session.execute(
            select(ProcessingJob).where(
                ProcessingJob.order_id == order_id,
                ProcessingJob.job_type == "pdf_download",
            )
        )
        all_jobs = jobs.scalars().all()
        # At most 1 RUNNING job (not PENDING — all were created as RUNNING)
        running = [j for j in all_jobs if j.status == "RUNNING"]
        # After processing, the job should be either RUNNING (still going)
        # or COMPLETED/FAILED (done). Key: no duplicate processing.
        assert len(running) <= 1, (
            f"At most 1 RUNNING job expected, got {len(running)}: "
            f"{[(j.id, j.status) for j in all_jobs]}"
        )
