import asyncio
import logging
from collections.abc import Coroutine
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

from celery.signals import worker_process_init, worker_process_shutdown
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import _get_session_factory
from app.core.state_machine import TERMINAL_STATUSES, PipelineStatus, validate_transition
from app.core.time import utc_now
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.processing_job import ProcessingJob
from app.repositories.event_repository import event_repository
from app.services.audit_service import run_audit
from app.services.pdf_service import process_pdf_for_order
from app.workers.celery_app import celery_app

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker event-loop lifecycle (P0-1 fix)
#
# Celery prefork workers fork from the parent process. The parent's
# @lru_cache'd AsyncEngine carries asyncpg connections bound to the
# parent's event loop. Each child MUST create its own event loop and
# its own AsyncEngine so that all connections stay within one loop.
#
# worker_process_init  → create loop + engine
# process_pdf / run_audit_task → loop.run_until_complete(...)
# worker_process_shutdown → dispose engine + close loop
# ---------------------------------------------------------------------------

_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_engine: AsyncEngine | None = None
_worker_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    global _worker_engine, _worker_session_factory
    if _worker_session_factory is not None:
        return _worker_session_factory
    # Fallback for test / non-worker contexts
    return _get_session_factory()


def _setup_worker_engine() -> None:
    global _worker_loop, _worker_engine, _worker_session_factory
    _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)
    settings = get_settings()
    _worker_engine = create_async_engine(
        settings.DATABASE_URL, echo=settings.DEBUG, future=True,
    )
    _worker_session_factory = async_sessionmaker(
        _worker_engine, class_=AsyncSession, expire_on_commit=False,
    )


def _teardown_worker_engine() -> None:
    global _worker_loop, _worker_engine, _worker_session_factory
    if _worker_loop is not None:
        pending = asyncio.all_tasks(_worker_loop)
        if pending:
            _worker_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        from sqlalchemy.ext.asyncio import close_all_sessions
        with suppress(Exception):
            _worker_loop.run_until_complete(close_all_sessions())
        with suppress(Exception):
            if _worker_engine is not None:
                _worker_loop.run_until_complete(_worker_engine.dispose())
        _worker_loop.close()
        _worker_loop = None
        _worker_engine = None
        _worker_session_factory = None


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
    """Process PDF download + parsing for an order.

    Uses an atomic UPDATE (compare-and-swap on pipeline_status) to claim
    the order. Works on both PostgreSQL (row-level locking) and SQLite
    (serialized via single connection). Only one concurrent task can
    transition from PDF_QUEUED → PDF_DOWNLOADING.
    """
    factory = _get_worker_session_factory()
    async with factory() as db:
        try:
            # Atomic claim: version + state + transition in one UPDATE.
            # Only one concurrent task will see rowcount=1; others get 0.
            from sqlalchemy import update as _sql_update

            claim = cast("CursorResult[Any]", await db.execute(
                _sql_update(Order)
                .where(
                    Order.id == order_id,
                    Order.order_version == order_version,
                    Order.pipeline_status == PipelineStatus.PDF_QUEUED.value,
                )
                .values(pipeline_status=PipelineStatus.PDF_DOWNLOADING.value)
            ))
            if claim.rowcount == 0:
                # Claim failed — diagnose reason
                order = await _get_order(db, order_id)
                if order is None:
                    logger.error("Order not found order_id=%s", order_id)
                elif order.order_version != order_version:
                    logger.warning(
                        "Order version mismatch order_id=%s task=%s db=%s — skipping",
                        order_id, order_version, order.order_version,
                    )
                else:
                    logger.warning(
                        "PDF task failed to claim order_id=%s status=%s — noop",
                        order_id, order.pipeline_status,
                    )
                return

            # Claim succeeded — reload order & emit event
            order = await _get_order(db, order_id)
            if order is None:
                raise RuntimeError(f"Claimed order disappeared order_id={order_id}")
            await event_repository.create_event(
                db, order.id, order.owner_user_id, "order.pdf_downloading",
                order_version, {"from_status": "PDF_QUEUED", "to_status": "PDF_DOWNLOADING"},
            )

            # Check for existing running/completed job (duplicate prevention)
            existing_job = await _find_active_job(db, order_id, order_version, "pdf_download")
            if existing_job is not None:
                logger.warning(
                    "PDF job already exists order_id=%s version=%s job_id=%s status=%s — noop",
                    order_id, order_version, existing_job.id, existing_job.status,
                )
                return

            job = await _create_job(db, order_id, order_version, "pdf_download")

            pdf_files = await _get_pending_files(db, order_id, order_version)
            if not pdf_files:
                pdf_files = await _get_order_pdf_files(db, order_id, order_version)

            if not pdf_files:
                await _transition_order(db, order, PipelineStatus.PDF_FAILED, "order.pdf_failed")
                await _finish_job(db, job, "FAILED", error_code="NO_PDF", error_message="No PDF files found")
                await db.commit()
                return

            any_failed = False
            failed_code = None
            failed_msg = None
            processed_count = 0
            skipped_or_ocr = False
            for pdf_info in pdf_files:
                url = pdf_info.get("url") or pdf_info.get("internal_url", "")
                name = pdf_info.get("name", "document.pdf")
                pending_id = pdf_info.get("id")
                if not url:
                    any_failed = True
                    failed_code = "NO_PDF_URL"
                    failed_msg = f"No usable URL for {name}"
                    continue
                try:
                    file_record = await process_pdf_for_order(db, order_id, url, name, order_version)
                    if pending_id and file_record.id != pending_id:
                        old = await db.get(OrderFile, pending_id)
                        if old is not None:
                            await db.delete(old)
                    if file_record.parse_status == "READY":
                        processed_count += 1
                    elif file_record.parse_status in ("SKIPPED", "OCR_REQUIRED"):
                        skipped_or_ocr = True
                    else:
                        # FAILED and other error parse_status values
                        any_failed = True
                        failed_code = file_record.error_code or f"PDF_{file_record.parse_status}"
                        failed_msg = file_record.error_message or f"PDF parse status: {file_record.parse_status}"
                except Exception as e:
                    logger.exception("PDF processing failed order_id=%s url=%s", order_id, url[:120])
                    err_code = _extract_error_code(e)
                    any_failed = True
                    failed_code = err_code
                    failed_msg = str(e)
                    continue

            if any_failed:
                await _transition_order(db, order, PipelineStatus.PDF_FAILED, "order.pdf_failed")
                await _finish_job(
                    db,
                    job,
                    "FAILED",
                    error_code=failed_code or "NO_PDF",
                    error_message=failed_msg or "No PDF was processed",
                )
                await db.commit()
                return

            if processed_count > 0:
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
                return

            if skipped_or_ocr:
                await _transition_order(
                    db, order, PipelineStatus.MANUAL_REQUIRED, "order.manual_required"
                )
                await _finish_job(db, job, "COMPLETED")
                await db.commit()
                return

            # No files were processed (empty list or all had no URL)
            await _transition_order(db, order, PipelineStatus.PDF_FAILED, "order.pdf_failed")
            await _finish_job(
                db,
                job,
                "FAILED",
                error_code=failed_code or "NO_PDF",
                error_message=failed_msg or "No PDF was processed",
            )
            await db.commit()
            return

        except Exception:
            await db.rollback()
            raise


async def _run_audit_task(order_id: str, order_version: int) -> None:
    """Run audit routing + AI audit for an order.

    Uses an atomic UPDATE (compare-and-swap on pipeline_status) to claim
    the order. Only one concurrent task can transition from PDF_READY → ROUTING.
    """
    factory = _get_worker_session_factory()
    async with factory() as db:
        try:
            # Atomic claim: version + state + transition in one UPDATE
            from sqlalchemy import update as _sql_update

            claim = cast("CursorResult[Any]", await db.execute(
                _sql_update(Order)
                .where(
                    Order.id == order_id,
                    Order.order_version == order_version,
                    Order.pipeline_status == PipelineStatus.PDF_READY.value,
                )
                .values(pipeline_status=PipelineStatus.ROUTING.value)
            ))
            if claim.rowcount == 0:
                # Claim failed — diagnose reason
                order = await _get_order(db, order_id)
                if order is None:
                    logger.error("Order not found order_id=%s", order_id)
                elif order.order_version != order_version:
                    logger.warning(
                        "Order version mismatch order_id=%s task=%s db=%s — skipping",
                        order_id, order_version, order.order_version,
                    )
                else:
                    logger.warning(
                        "Audit task failed to claim order_id=%s status=%s — noop",
                        order_id, order.pipeline_status,
                    )
                return

            # Claim succeeded — reload order & emit event
            order = await _get_order(db, order_id)
            if order is None:
                raise RuntimeError(f"Claimed order disappeared order_id={order_id}")
            await event_repository.create_event(
                db, order.id, order.owner_user_id, "order.routing",
                order_version, {"from_status": "PDF_READY", "to_status": "ROUTING"},
            )

            # Check for existing running/completed job (duplicate prevention)
            existing_job = await _find_active_job(db, order_id, order_version, "audit")
            if existing_job is not None:
                logger.warning(
                    "Audit job already exists order_id=%s version=%s job_id=%s status=%s — noop",
                    order_id, order_version, existing_job.id, existing_job.status,
                )
                return

            job = await _create_job(db, order_id, order_version, "audit")

            from app.services.routing_service import route_order
            skill = await route_order(order.order_snapshot or {}, order.business_type)

            if skill is None:
                await _transition_order(db, order, PipelineStatus.MANUAL_REQUIRED, "order.manual_required")
                await _finish_job(db, job, "COMPLETED")
                await db.commit()
                return

            await _transition_order(db, order, PipelineStatus.AI_QUEUED, "order.ai_queued")
            await _transition_order(db, order, PipelineStatus.AI_RUNNING, "order.ai_running")

            pdf_text = await _get_parsed_text(db, order_id, order_version)

            result = await run_audit(db, order, pdf_text)

            if result.decision == "MANUAL_REVIEW":
                await _transition_order(db, order, PipelineStatus.MANUAL_REQUIRED, "order.manual_required")
            else:
                await _transition_order(db, order, PipelineStatus.AI_COMPLETED, "order.ai_completed")
            await _finish_job(db, job, "COMPLETED")
            await db.commit()

        except Exception:
            await db.rollback()
            raise


async def _get_pending_files(
    db: AsyncSession,
    order_id: str,
    order_version: int,
) -> list[dict[str, str]]:
    result = await db.execute(
        select(OrderFile).where(
            OrderFile.order_id == order_id,
            OrderFile.order_version == order_version,
            OrderFile.parse_status == "PENDING",
        )
    )
    files = result.scalars().all()
    return [
        {"id": f.id, "url": f.source_url or "", "internal_url": f.internal_url or "", "name": f.original_name}
        for f in files
    ]


async def _get_order_pdf_files(
    db: AsyncSession,
    order_id: str,
    order_version: int,
) -> list[dict[str, str]]:
    result = await db.execute(
        select(OrderFile).where(
            OrderFile.order_id == order_id,
            OrderFile.order_version == order_version,
        )
    )
    files = result.scalars().all()
    if files:
        return [
            {"id": f.id, "url": f.source_url or "", "internal_url": f.internal_url or "", "name": f.original_name}
            for f in files
        ]
    return []


async def _get_parsed_text(db: AsyncSession, order_id: str, order_version: int) -> str:
    result = await db.execute(
        select(OrderFile).where(
            OrderFile.order_id == order_id,
            OrderFile.order_version == order_version,
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


async def _find_active_job(
    db: AsyncSession, order_id: str, order_version: int, job_type: str,
) -> ProcessingJob | None:
    """Find an existing non-failed job for the same (order, version, type).

    Filters by (order_id, order_version, job_type). Only RUNNING/COMPLETED
    statuses block processing. FAILED jobs are excluded so same-version
    retries can create new attempts.
    """
    result = await db.execute(
        select(ProcessingJob).where(
            ProcessingJob.order_id == order_id,
            ProcessingJob.order_version == order_version,
            ProcessingJob.job_type == job_type,
            ProcessingJob.status.in_(["RUNNING", "COMPLETED"]),
        )
    )
    return result.scalars().first()


async def _create_job(
    db: AsyncSession, order_id: str, order_version: int, job_type: str,
) -> ProcessingJob:
    job = ProcessingJob(
        order_id=order_id,
        order_version=order_version,
        job_type=job_type,
        status="RUNNING",
        attempt_count=0,
        started_at=utc_now(),
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
    job.finished_at = utc_now()
    if error_code:
        job.error_code = error_code
    if error_message:
        job.error_message = error_message


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)  # type: ignore[untyped-decorator]
def process_pdf(self, order_id: str, order_version: int = 1) -> None:
    """Celery task: download + parse PDFs with DNS pinning.

    Uses the worker process's dedicated event loop so that all
    asyncpg connections stay within one loop per child process.
    """
    try:
        _run_in_worker(_run_pdf_task(order_id, order_version))
    except Exception as e:
        logger.exception("PDF task failed order_id=%s attempt=%s", order_id, self.request.retries)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        _mark_failed_async(order_id, order_version, str(e))


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)  # type: ignore[untyped-decorator]
def run_audit_task(self, order_id: str, order_version: int = 1) -> None:
    """Celery task: route + audit with AI.

    Uses the worker process's dedicated event loop so that all
    asyncpg connections stay within one loop per child process.
    """
    try:
        _run_in_worker(_run_audit_task(order_id, order_version))
    except Exception as e:
        logger.exception("Audit task failed order_id=%s attempt=%s", order_id, self.request.retries)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e) from e
        _mark_failed_async(order_id, order_version, str(e))


async def _mark_failed(order_id: str, order_version: int, error: str) -> None:
    """Mark an order as FAILED_FINAL if safe to do so.

    Rules:
    - Never overwrite terminal states (AI_COMPLETED, MANUAL_REQUIRED, FAILED_FINAL)
    - No-op on version mismatch
    - Only transition if state machine allows current -> FAILED_FINAL
    - Idempotent: repeat calls do not create duplicate events
    """
    factory = _get_worker_session_factory()
    async with factory() as db:
        try:
            order = await _get_order(db, order_id)
            if order is None:
                return

            if order.order_version != order_version:
                return

            current = PipelineStatus(order.pipeline_status)

            # Never overwrite terminal states
            if current in TERMINAL_STATUSES:
                logger.warning(
                    "Not overwriting terminal state order_id=%s current=%s",
                    order_id, current.value,
                )
                return

            # Only transition if valid according to state machine
            if not validate_transition(current, PipelineStatus.FAILED_FINAL, order.id):
                logger.warning(
                    "Cannot transition to FAILED_FINAL from %s for order_id=%s",
                    current.value, order_id,
                )
                return

            order.pipeline_status = PipelineStatus.FAILED_FINAL.value
            await event_repository.create_event(
                db, order.id, order.owner_user_id, "order.failed",
                order.order_version, {"error": error[:500]},
            )
            await db.commit()

        except Exception:
            await db.rollback()
            raise


def _run_in_worker[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on the worker process's dedicated event loop.

    If no worker loop is active (e.g. during tests), falls back to
    asyncio.run() which creates a temporary loop.
    """
    global _worker_loop
    if _worker_loop is not None:
        return _worker_loop.run_until_complete(coro)
    # Fallback: test / non-worker context
    return asyncio.run(coro)


def _mark_failed_async(order_id: str, order_version: int, error: str) -> None:
    """Sync wrapper to call _mark_failed from a non-async context (Celery task).

    Prefers the worker loop when available; falls back to asyncio.run().
    """
    global _worker_loop
    if _worker_loop is not None:
        _worker_loop.run_until_complete(_mark_failed(order_id, order_version, error))
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_mark_failed(order_id, order_version, error))
        return

    try:
        asyncio.ensure_future(
            _mark_failed(order_id, order_version, error), loop=loop,
        )
    except RuntimeError:
        logger.error(
            "Cannot mark order as failed: event loop conflict order_id=%s",
            order_id,
        )


# ---------------------------------------------------------------------------
# Celery signals — worker process lifecycle
# ---------------------------------------------------------------------------

@worker_process_init.connect  # type: ignore[untyped-decorator]
def _on_worker_process_init(**kwargs) -> None:
    """Create a dedicated event loop + engine for this worker child process."""
    _setup_worker_engine()
    logger.info(
        "Worker process init: engine=%s loop=%s",
        id(_worker_engine), id(_worker_loop),
    )


@worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def _on_worker_process_shutdown(**kwargs) -> None:
    """Dispose the worker engine and close the event loop."""
    _teardown_worker_engine()
    logger.info("Worker process shutdown: engine disposed, loop closed")
