import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_machine import PipelineStatus
from app.models.order import Order
from app.models.user import User
from app.repositories.event_repository import event_repository
from app.repositories.order_repository import Scope, compute_detail_hash, order_repository
from app.schemas.order import OrderIngestRequest, PdfFileItem

logger = logging.getLogger(__name__)


class CrossUserConflictError(Exception):
    """Raised when a task_order_id already belongs to a different user."""
    def __init__(self, task_order_id: str, existing_owner: str):
        self.task_order_id = task_order_id
        self.existing_owner = existing_owner
        super().__init__(
            f"task_order_id={task_order_id} already belongs to user={existing_owner}"
        )


class OrderService:
    def __init__(self) -> None:
        self.repo = order_repository

    async def ingest(
        self,
        db: AsyncSession,
        request: OrderIngestRequest,
        owner: User,
    ) -> tuple[Order, bool]:
        new_hash = compute_detail_hash(
            request.order_snapshot,
            request.raw_detail,
            request.pdf_files,
        )
        existing = await self.repo.get_by_task_order_id(db, request.task_order_id)

        if existing is not None and existing.owner_user_id != owner.id:
            raise CrossUserConflictError(
                request.task_order_id, existing.owner_user_id
            )

        if existing is None:
            order = Order(
                task_order_id=request.task_order_id,
                task_uuid=request.task_uuid,
                owner_user_id=owner.id,
                scene_id=request.scene_id,
                audit_point_id=request.audit_point_id,
                audit_node=request.audit_node,
                business_type=request.business_type,
                business_status=None,
                pipeline_status=PipelineStatus.RECEIVED.value,
                order_version=1,
                detail_hash=new_hash,
                order_snapshot=request.order_snapshot,
                raw_detail=request.raw_detail,
            )
            db.add(order)
            await db.flush()
            await db.refresh(order)

            await event_repository.create_event(
                db, order.id, owner.id, "order.created", order.order_version,
                {"task_order_id": order.task_order_id},
            )

            await _enqueue_order(db, order, owner, request.pdf_files)

            logger.info(
                "Order created task_order_id=%s order_id=%s user=%s",
                order.task_order_id, order.id, owner.arms_account,
            )
            return order, True

        if existing.detail_hash == new_hash:
            logger.debug(
                "Order unchanged task_order_id=%s order_id=%s",
                existing.task_order_id, existing.id,
            )
            return existing, False

        existing.order_version += 1
        existing.detail_hash = new_hash
        existing.task_uuid = request.task_uuid
        existing.scene_id = request.scene_id
        existing.audit_point_id = request.audit_point_id
        existing.audit_node = request.audit_node
        existing.business_type = request.business_type
        existing.order_snapshot = request.order_snapshot
        existing.raw_detail = request.raw_detail
        existing.pipeline_status = PipelineStatus.RECEIVED.value
        await db.flush()
        await db.refresh(existing)

        await event_repository.create_event(
            db, existing.id, owner.id, "order.updated", existing.order_version,
            {"task_order_id": existing.task_order_id},
        )

        await _enqueue_order(db, existing, owner, request.pdf_files)

        logger.info(
            "Order updated task_order_id=%s order_id=%s version=%s",
            existing.task_order_id, existing.id, existing.order_version,
        )
        return existing, True

    async def get_order_for_user(
        self, db: AsyncSession, task_order_id: str, owner_user_id: str,
        scope: Scope = "own",
    ) -> Order | None:
        if scope == "all":
            return await self.repo.get_by_task_order_id(db, task_order_id)
        return await self.repo.get_by_task_order_id_and_owner(
            db, task_order_id, owner_user_id
        )

    async def retry_order(
        self, db: AsyncSession, task_order_id: str, owner_user_id: str
    ) -> Order | None:
        order = await self.repo.get_by_task_order_id_and_owner(
            db, task_order_id, owner_user_id
        )
        if order is None:
            return None

        from app.core.state_machine import PipelineStatus, can_transition

        current = PipelineStatus(order.pipeline_status)
        if not can_transition(current, PipelineStatus.RECEIVED):
            logger.warning(
                "Retry not allowed task_order_id=%s status=%s",
                task_order_id, order.pipeline_status,
            )
            return None

        order.pipeline_status = PipelineStatus.RECEIVED.value
        await db.flush()
        await db.refresh(order)

        await event_repository.create_event(
            db, order.id, owner_user_id, "order.retry_requested", order.order_version,
            {"task_order_id": order.task_order_id},
        )
        user = await _get_user_by_id(db, owner_user_id)
        if user is None:
            return None
        await _enqueue_order(db, order, user)
        return order


async def _get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()


async def _enqueue_order(
    db: AsyncSession,
    order: Order,
    owner: User,
    pdf_files: Sequence[PdfFileItem | dict[str, Any]] | None = None,
) -> None:
    from app.core.state_machine import validate_transition
    from app.models.order_file import OrderFile

    current = PipelineStatus(order.pipeline_status)
    target = PipelineStatus.PDF_QUEUED
    if not validate_transition(current, target, order.id):
        logger.error(
            "Cannot enqueue order_id=%s from %s", order.id, current.value
        )
        return
    order.pipeline_status = target.value
    await event_repository.create_event(
        db, order.id, owner.id, "order.pdf_queued", order.order_version,
        {"task_order_id": order.task_order_id},
    )

    # Save PDF source records
    if pdf_files:
        for pf in pdf_files:
            if isinstance(pf, dict):
                file_record = OrderFile(
                    order_id=order.id,
                    order_version=order.order_version,
                    original_name=pf.get("name", pf.get("original_name", "document.pdf")),
                    source_url=pf.get("url", pf.get("source_url", "")),
                    internal_url=pf.get("internal_url", ""),
                    parse_status="PENDING",
                )
            else:
                file_record = OrderFile(
                    order_id=order.id,
                    order_version=order.order_version,
                    original_name=getattr(pf, "name", "document.pdf"),
                    source_url=getattr(pf, "url", ""),
                    internal_url=getattr(pf, "internal_url", ""),
                    parse_status="PENDING",
                )
            db.add(file_record)

    from app.models.task_outbox import TaskOutbox
    outbox = TaskOutbox(
        order_id=order.id,
        order_version=order.order_version,
        task_type="process_pdf",
        task_payload={"order_id": order.id, "order_version": order.order_version},
    )
    db.add(outbox)
    await db.flush()


order_service = OrderService()
