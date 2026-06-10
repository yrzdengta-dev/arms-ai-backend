import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _get_session_factory
from app.core.state_machine import PipelineStatus, validate_transition
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.processing_job import ProcessingJob
from app.repositories.event_repository import event_repository
from app.services.audit_service import run_audit
from app.services.pdf_service import process_pdf_for_order
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _get_order(db: AsyncSession, order_id: str) -> Order | None:
    result = await db.execute(select(Order).where(Order.id == order_id))
    return result.scalars().first()


async def _transition_order(
    db: AsyncSession, order: Order, target: PipelineStatus, event_type: str,
) -> None:
    current = PipelineStatus(order.pipeline_status)
    if not validate_transition(current, target, order.id):
        raise ValueError(
            f"Invalid transition {current.value} -> {target.value} for {order.id}"
        )
    order.pipeline_status = target.value
    await event_repository.create_event(
        db, order.id, order.owner_user_id, event_type, order.order_version,
        {"from_status": current.value, "to_status": target.value},
    )


async def _run_pdf_task(order_id: str, order_version: int) -> None:
    async with _get_session_factory()() as db:
        try:
            order = await _get_order(db, order_id)
            if order is None:
                logger.error("Order not found order_id=%s", order_id)
                return

            if order.order_version != order_version:
                logger.warning(
                    "Order version mismatch order_id=%s task=%s db=%s — skipping",
                    order_id, order_version, order.order_version,
                )
                return

            job = await _create_job(db, order_id, "pdf_download")
            await _transition_order(db, order, PipelineStatus.PDF_DOWNLOADING, "order.pdf_downloading")

            pdf_files = await _get_pending_files(db, order_id)
            if not pdf_files:
                pdf_files = await _get_order_pdf_files(db, order_id)

            if not pdf_files:
                await _transition_order(db, order, PipelineStatus.PDF_FAILED, "order.pdf_failed")
                await _finish_job(db, job, "FAILED", error_code="NO_PDF", error_message="No PDF files found")
                await db.commit()
                return

            any_failed = False
            failed_code = None
            failed_msg = None
            for pdf_info in pdf_files:
                url = pdf_info.get("url") or pdf_info.get("internal_url", "")
                name = pdf_info.get("name", "document.pdf")
                if not url:
                    continue
                try:
                    await process_pdf_for_order(db, order_id, url, name)
                except Exception as e:
                    logger.exception("PDF processing failed order_id=%s url=%s", order_id, url[:120])
                    err_code = _extract_error_code(e)
                    any_failed = True
                    failed_code = err_code
                    failed_msg = str(e)
                    continue

            if any_failed:
                await _transition_order(db, order, PipelineStatus.PDF_FAILED, "order.pdf_failed")
                await _finish_job(db, job, "FAILED", error_code=failed_code or "PDF_ERROR", error_message=failed_msg)
                await db.commit()
                return

            await _transition_order(db, order, PipelineStatus.PDF_READY, "order.pdf_ready")
            await _finish_job(db, job, "COMPLETED")

            from app.models.task_outbox import TaskOutbox
            outbox = TaskOutbox(
                order_id=order.id,
                order_version=order.order_version,
                task_type="run_audit",
                task_payload={"order_id": order.id, "order_version": order.order_version},
            )
            db.add(outbox)

            await db.commit()

        except Exception:
            await db.rollback()
            raise


async def _run_audit_task(order_id: str, order_version: int) -> None:
    async with _get_session_factory()() as db:
        try:
            order = await _get_order(db, order_id)
            if order is None:
                logger.error("Order not found order_id=%s", order_id)
                return

            if order.order_version != order_version:
                logger.warning(
                    "Order version mismatch order_id=%s task=%s db=%s — skipping",
                    order_id, order_version, order.order_version,
                )
                return

            job = await _create_job(db, order_id, "audit")

            await _transition_order(db, order, PipelineStatus.ROUTING, "order.routing")

            from app.services.routing_service import route_order
            skill = await route_order(order.order_snapshot or {}, order.business_type)

            if skill is None:
                await _transition_order(db, order, PipelineStatus.MANUAL_REQUIRED, "order.manual_required")
                await _finish_job(db, job, "COMPLETED")
                await db.commit()
                return

            await _transition_order(db, order, PipelineStatus.AI_QUEUED, "order.ai_queued")
            await _transition_order(db, order, PipelineStatus.AI_RUNNING, "order.ai_running")

            pdf_text = await _get_parsed_text(db, order_id)

            await run_audit(db, order, pdf_text)

            await _transition_order(db, order, PipelineStatus.AI_COMPLETED, "order.ai_completed")
            await _finish_job(db, job, "COMPLETED")
            await db.commit()

        except Exception:
            await db.rollback()
            raise


async def _get_pending_files(db: AsyncSession, order_id: str) -> list[dict[str, str]]:
    result = await db.execute(
        select(OrderFile).where(
            OrderFile.order_id == order_id,
            OrderFile.parse_status == "PENDING",
        )
    )
    files = result.scalars().all()
    return [{"url": f.source_url or "", "name": f.original_name} for f in files]


async def _get_order_pdf_files(db: AsyncSession, order_id: str) -> list[dict[str, str]]:
    result = await db.execute(
        select(OrderFile).where(OrderFile.order_id == order_id)
    )
    files = result.scalars().all()
    if files:
        return [{"url": f.source_url or "", "name": f.original_name} for f in files]
    return []


async def _get_parsed_text(db: AsyncSession, order_id: str) -> str:
    result = await db.execute(
        select(OrderFile).where(
            OrderFile.order_id == order_id,
            OrderFile.parse_status == "READY",
        )
    )
    files = result.scalars().all()
    parts: list[str] = []
    for f in files:
        text = f.parsed_text or ""
        if text:
            parts.append(f"--- {f.original_name} ---\n{text}")
    return "\n\n".join(parts)


def _extract_error_code(exception: Exception) -> str:
    from app.adapters.pdf.downloader import PdfDownloadError
    if isinstance(exception, PdfDownloadError):
        return exception.code or "PDF_DOWNLOAD_ERROR"
    code = getattr(exception, "code", None)
    if code:
        return str(code)
    return "PDF_ERROR"


async def _create_job(db: AsyncSession, order_id: str, job_type: str) -> ProcessingJob:
    job = ProcessingJob(
        order_id=order_id,
        job_type=job_type,
        status="RUNNING",
        attempt_count=0,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def _finish_job(
    db: AsyncSession,
    job: ProcessingJob,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    job.status = status
    if error_code:
        job.error_code = error_code
    if error_message:
        job.error_message = error_message


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)  # type: ignore[untyped-decorator]
def process_pdf(self, order_id: str, order_version: int = 1) -> None:
    try:
        asyncio.run(_run_pdf_task(order_id, order_version))
    except Exception as e:
        logger.exception("PDF task failed order_id=%s attempt=%s", order_id, self.request.retries)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        _mark_failed_async(order_id, order_version, str(e))


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)  # type: ignore[untyped-decorator]
def run_audit_task(self, order_id: str, order_version: int = 1) -> None:
    try:
        asyncio.run(_run_audit_task(order_id, order_version))
    except Exception as e:
        logger.exception("Audit task failed order_id=%s attempt=%s", order_id, self.request.retries)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        _mark_failed_async(order_id, order_version, str(e))


def _mark_failed_async(order_id: str, order_version: int, error: str) -> None:
    async def _mark() -> None:
        async with _get_session_factory()() as db:
            order = await _get_order(db, order_id)
            if order and order.order_version == order_version:
                current = PipelineStatus(order.pipeline_status)
                if validate_transition(current, PipelineStatus.FAILED_FINAL, order.id):
                    order.pipeline_status = PipelineStatus.FAILED_FINAL.value
                else:
                    # No valid transition — force terminal state with warning
                    logger.warning(
                        "Forcing FAILED_FINAL from %s for order_id=%s (no valid transition)",
                        current.value, order_id,
                    )
                    order.pipeline_status = PipelineStatus.FAILED_FINAL.value
                await event_repository.create_event(
                    db, order.id, order.owner_user_id, "order.failed",
                    order.order_version, {"error": error[:500]},
                )
                await db.commit()

    try:
        asyncio.run(_mark())
    except Exception:
        logger.exception("Failed to mark order as failed order_id=%s", order_id)
