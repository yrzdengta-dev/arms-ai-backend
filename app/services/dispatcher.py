import asyncio
import logging

from sqlalchemy import select

from app.core.database import _get_session_factory
from app.models.task_outbox import TaskOutbox

logger = logging.getLogger(__name__)


async def dispatch_outbox_loop() -> None:
    """Background loop that reads pending outbox records and dispatches Celery tasks."""
    while True:
        await asyncio.sleep(2)
        try:
            async with _get_session_factory()() as db:
                result = await db.execute(
                    select(TaskOutbox)
                    .where(TaskOutbox.dispatched == False)  # noqa: E712
                    .limit(10)
                )
                pending = result.scalars().all()
                for record in pending:
                    try:
                        _dispatch(record)
                        record.dispatched = True
                        logger.debug("Dispatched outbox id=%s type=%s", record.id, record.task_type)
                    except Exception as e:
                        logger.error(
                            "Failed to dispatch outbox id=%s: %s", record.id, e
                        )
                if pending:
                    await db.commit()
        except Exception:
            logger.exception("Outbox dispatcher error")


def _dispatch(record: TaskOutbox) -> None:
    if record.task_type == "process_pdf":
        from app.workers.tasks import process_pdf
        process_pdf.delay(record.task_payload["order_id"], record.task_payload["order_version"])
    elif record.task_type == "run_audit":
        from app.workers.tasks import run_audit_task
        run_audit_task.delay(record.task_payload["order_id"], record.task_payload["order_version"])
    else:
        logger.warning("Unknown outbox task_type=%s id=%s", record.task_type, record.id)
