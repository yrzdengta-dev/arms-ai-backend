"""Service for human correction and batch confirmation (P0).

These are audit adjudication layer operations — they do NOT drive pipeline_status
transitions. Human decisions and confirmation state are stored on the Order model,
independent of pipeline_status.
"""

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import can_view_all_orders, get_current_user
from app.core.time import utc_now
from app.models.order import Order
from app.repositories.event_repository import event_repository
from app.repositories.order_repository import Scope, order_repository
from app.schemas.order import (
    BatchConfirmRequest,
    BatchConfirmResponse,
    BatchConfirmResultItem,
    BatchConfirmSummary,
    CorrectionHistoryEntry,
    CorrectionRequest,
    CorrectionResponse,
)

logger = logging.getLogger(__name__)


class CorrectionService:
    """Handles human correction and batch confirmation of AI audit results."""

    async def correct(
        self,
        db: AsyncSession,
        task_order_id: str,
        request: CorrectionRequest,
        user_id: str,
        scope: Scope,
    ) -> CorrectionResponse | None:
        """Apply a human correction to an order's AI decision.

        Returns None if the order is not found or not visible to the user.
        """
        order = await order_repository.get_by_task_order_id(db, task_order_id)
        if order is None:
            return None
        if scope == "own" and order.owner_user_id != user_id:
            return None

        # Get current AI decision from latest AuditResult
        from sqlalchemy import select
        from app.models.audit_result import AuditResult

        result_stmt = (
            select(AuditResult.decision)
            .where(
                AuditResult.order_id == order.id,
                AuditResult.order_version == order.order_version,
            )
            .limit(1)
        )
        ai_decision_row = (await db.execute(result_stmt)).scalars().first()

        ai_decision = ai_decision_row
        operator = request.operator or "unknown"

        # Determine from_decision: last human decision if exists, else AI decision
        current_human = order.human_decision
        from_decision = current_human if current_human else (ai_decision or "UNKNOWN")

        # Build correction entry
        entry: dict = {
            "operated_at": utc_now().isoformat(),
            "operator": operator,
            "from_decision": from_decision,
            "to_decision": request.decision,
            "reason": request.reason,
        }

        # Append to correction history
        history = list(order.correction_history or [])
        history.append(entry)

        # Update order
        order.human_decision = request.decision
        order.correction_history = history
        await db.flush()

        # Emit SSE event
        await event_repository.create_event(
            db,
            order.id,
            order.owner_user_id,
            "order.corrected",
            order.order_version,
            {
                "task_order_id": order.task_order_id,
                "from_decision": from_decision,
                "to_decision": request.decision,
                "operator": operator,
            },
        )

        logger.info(
            "Order corrected task_order_id=%s from=%s to=%s by=%s",
            task_order_id, from_decision, request.decision, operator,
        )

        return CorrectionResponse(
            task_order_id=order.task_order_id,
            ai_decision=ai_decision,
            human_result=request.decision,
            correction_history=[
                CorrectionHistoryEntry(
                    operated_at=datetime.fromisoformat(h["operated_at"]),
                    operator=h["operator"],
                    from_decision=h["from_decision"],
                    to_decision=h["to_decision"],
                    reason=h["reason"],
                )
                for h in history
            ],
            pipeline_status=order.pipeline_status,
        )

    async def batch_confirm(
        self,
        db: AsyncSession,
        request: BatchConfirmRequest,
        user_id: str,
        scope: Scope,
    ) -> BatchConfirmResponse:
        """Batch confirm orders. Idempotent — already confirmed orders are skipped."""
        results: list[BatchConfirmResultItem] = []
        summary = BatchConfirmSummary(total=len(request.task_order_ids))

        for task_order_id in request.task_order_ids:
            order = await order_repository.get_by_task_order_id(db, task_order_id)

            # Check visibility
            if order is None or (scope == "own" and order.owner_user_id != user_id):
                results.append(BatchConfirmResultItem(
                    task_order_id=task_order_id,
                    status="skipped",
                    reason="not_found_or_forbidden",
                ))
                summary.skipped += 1
                continue

            # Check idempotency
            if order.confirmed_at is not None:
                results.append(BatchConfirmResultItem(
                    task_order_id=task_order_id,
                    status="already_confirmed",
                    confirmed_at=order.confirmed_at,
                ))
                summary.already_confirmed += 1
                continue

            # Confirm
            now = utc_now()
            order.confirmed_by = user_id
            order.confirmed_at = now
            await db.flush()

            # Emit SSE event
            await event_repository.create_event(
                db,
                order.id,
                order.owner_user_id,
                "order.confirmed",
                order.order_version,
                {
                    "task_order_id": order.task_order_id,
                    "confirmed_by": user_id,
                },
            )

            results.append(BatchConfirmResultItem(
                task_order_id=task_order_id,
                status="confirmed",
                confirmed_at=now,
            ))
            summary.confirmed += 1

            logger.info(
                "Order confirmed task_order_id=%s by=%s",
                task_order_id, user_id,
            )

        return BatchConfirmResponse(results=results, summary=summary)


correction_service = CorrectionService()
