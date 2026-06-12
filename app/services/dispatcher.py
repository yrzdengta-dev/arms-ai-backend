import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import _get_session_factory
from app.core.time import utc_now
from app.models.order import Order
from app.models.processing_job import ProcessingJob
from app.models.task_outbox import TaskOutbox

logger = logging.getLogger(__name__)
settings = get_settings()


async def dispatch_outbox_loop() -> None:
    """Background loop that reads pending outbox records and dispatches Celery tasks."""
    reconcile_elapsed = settings.OUTBOX_RECONCILE_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(2)
        try:
            async with _get_session_factory()() as db:
                recovered = 0
                reconcile_elapsed += 2
                if reconcile_elapsed >= settings.OUTBOX_RECONCILE_INTERVAL_SECONDS:
                    recovered = await reconcile_stale_outbox(
                        db,
                        stale_after_seconds=settings.OUTBOX_STALE_AFTER_SECONDS,
                    )
                    reconcile_elapsed = 0
                    if recovered:
                        logger.warning("Recovered %s stale outbox task(s)", recovered)

                dispatched = await dispatch_pending_once(db)
                if recovered or dispatched:
                    await db.commit()
                else:
                    await db.rollback()
        except Exception:
            logger.exception("Outbox dispatcher error")


async def dispatch_pending_once(db: AsyncSession, limit: int = 10) -> int:
    result = await db.execute(
        select(TaskOutbox)
        .where(TaskOutbox.dispatched == False)  # noqa: E712
        .order_by(TaskOutbox.created_at.asc(), TaskOutbox.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    pending = result.scalars().all()
    dispatched = 0
    for record in pending:
        try:
            _dispatch(record)
        except Exception as e:
            logger.error(
                "Failed to dispatch outbox id=%s type=%s: %s",
                record.id,
                record.task_type,
                e,
            )
            continue
        record.dispatched = True
        dispatched += 1
        logger.debug("Dispatched outbox id=%s type=%s", record.id, record.task_type)
    return dispatched


async def reconcile_stale_outbox(
    db: AsyncSession,
    *,
    stale_after_seconds: int,
    limit: int = 100,
) -> int:
    cutoff = utc_now() - timedelta(seconds=max(0, stale_after_seconds))
    result = await db.execute(
        select(Order)
        .where(
            Order.pipeline_status.in_(["PDF_QUEUED", "PDF_READY"]),
            Order.updated_at <= cutoff,
        )
        .order_by(Order.updated_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    recovered = 0
    for order in result.scalars().all():
        task_type = "process_pdf" if order.pipeline_status == "PDF_QUEUED" else "run_audit"
        job_type = "pdf_download" if task_type == "process_pdf" else "audit"

        active_job = await db.execute(
            select(ProcessingJob.id).where(
                ProcessingJob.order_id == order.id,
                ProcessingJob.order_version == order.order_version,
                ProcessingJob.job_type == job_type,
                ProcessingJob.status == "RUNNING",
            ).limit(1)
        )
        if active_job.scalar_one_or_none() is not None:
            continue

        outbox_result = await db.execute(
            select(TaskOutbox)
            .where(
                TaskOutbox.order_id == order.id,
                TaskOutbox.order_version == order.order_version,
                TaskOutbox.task_type == task_type,
            )
            .order_by(TaskOutbox.created_at.desc())
            .limit(1)
        )
        record = outbox_result.scalars().first()
        if record is None:
            db.add(TaskOutbox(
                order_id=order.id,
                order_version=order.order_version,
                task_type=task_type,
                task_payload={
                    "order_id": order.id,
                    "order_version": order.order_version,
                },
                dispatched=False,
            ))
            recovered += 1
        elif record.dispatched:
            record.dispatched = False
            recovered += 1

    if recovered:
        await db.flush()
    return recovered


def _dispatch(record: TaskOutbox) -> None:
    if record.task_type == "process_pdf":
        from app.workers.tasks import process_pdf
        process_pdf.delay(record.task_payload["order_id"], record.task_payload["order_version"])
    elif record.task_type == "run_audit":
        from app.workers.tasks import run_audit_task
        run_audit_task.delay(record.task_payload["order_id"], record.task_payload["order_version"])
    else:
        raise ValueError(f"Unknown outbox task_type={record.task_type}")
