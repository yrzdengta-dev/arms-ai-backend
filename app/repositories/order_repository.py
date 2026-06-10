import hashlib
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order


def compute_detail_hash(order_snapshot: dict[str, Any], raw_detail: dict[str, Any]) -> str:
    payload = json.dumps({"snapshot": order_snapshot, "detail": raw_detail}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class OrderRepository:
    model = Order

    async def get_by_id(self, db: AsyncSession, order_id: str) -> Order | None:
        result = await db.execute(select(Order).where(Order.id == order_id))
        return result.scalars().first()

    async def get_by_task_order_id(
        self, db: AsyncSession, task_order_id: str
    ) -> Order | None:
        result = await db.execute(
            select(Order).where(Order.task_order_id == task_order_id)
        )
        return result.scalars().first()

    async def get_by_task_order_id_and_owner(
        self, db: AsyncSession, task_order_id: str, owner_user_id: str
    ) -> Order | None:
        result = await db.execute(
            select(Order).where(
                and_(Order.task_order_id == task_order_id, Order.owner_user_id == owner_user_id)
            )
        )
        return result.scalars().first()

    async def list_orders(
        self,
        db: AsyncSession,
        owner_user_id: str,
        skip: int = 0,
        limit: int = 50,
        pipeline_status: str | None = None,
        decision: str | None = None,
        business_type: str | None = None,
        scene_id: str | None = None,
        audit_point_id: str | None = None,
        search: str | None = None,
    ) -> Sequence[Order]:
        conditions = [Order.owner_user_id == owner_user_id]

        if pipeline_status:
            conditions.append(Order.pipeline_status == pipeline_status)
        if business_type:
            conditions.append(Order.business_type == business_type)
        if scene_id:
            conditions.append(Order.scene_id == scene_id)
        if audit_point_id:
            conditions.append(Order.audit_point_id == audit_point_id)
        if search:
            conditions.append(
                Order.task_order_id.ilike(f"%{search}%")
            )
        if decision:
            from app.models.audit_result import AuditResult

            subq = (
                select(AuditResult.order_id)
                .where(AuditResult.decision == decision)
                .subquery()
            )
            conditions.append(Order.id.in_(select(subq.c.order_id)))

        stmt = (
            select(Order)
            .where(and_(*conditions))
            .order_by(Order.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def count_orders(
        self,
        db: AsyncSession,
        owner_user_id: str,
        pipeline_status: str | None = None,
        decision: str | None = None,
        business_type: str | None = None,
        scene_id: str | None = None,
        audit_point_id: str | None = None,
        search: str | None = None,
    ) -> int:
        conditions = [Order.owner_user_id == owner_user_id]

        if pipeline_status:
            conditions.append(Order.pipeline_status == pipeline_status)
        if business_type:
            conditions.append(Order.business_type == business_type)
        if scene_id:
            conditions.append(Order.scene_id == scene_id)
        if audit_point_id:
            conditions.append(Order.audit_point_id == audit_point_id)
        if search:
            conditions.append(
                Order.task_order_id.ilike(f"%{search}%")
            )
        if decision:
            from app.models.audit_result import AuditResult

            subq = (
                select(AuditResult.order_id)
                .where(AuditResult.decision == decision)
                .subquery()
            )
            conditions.append(Order.id.in_(select(subq.c.order_id)))

        stmt = select(func.count()).select_from(Order).where(and_(*conditions))
        result = await db.execute(stmt)
        return result.scalar() or 0

    async def get_stats(
        self, db: AsyncSession, owner_user_id: str
    ) -> dict[str, Any]:
        total_stmt = select(func.count()).select_from(Order).where(
            Order.owner_user_id == owner_user_id
        )
        total_result = await db.execute(total_stmt)
        total = total_result.scalar() or 0

        pipeline_stmt = (
            select(Order.pipeline_status, func.count())
            .where(Order.owner_user_id == owner_user_id)
            .group_by(Order.pipeline_status)
        )
        pipeline_result = await db.execute(pipeline_stmt)
        by_pipeline = {row[0]: row[1] for row in pipeline_result}

        from app.models.audit_result import AuditResult

        decision_stmt = (
            select(AuditResult.decision, func.count())
            .select_from(Order)
            .join(AuditResult, Order.id == AuditResult.order_id, isouter=True)
            .where(Order.owner_user_id == owner_user_id)
            .group_by(AuditResult.decision)
        )
        decision_result = await db.execute(decision_stmt)
        by_decision = {row[0] or "PENDING": row[1] for row in decision_result}

        return {"total": total, "by_pipeline_status": by_pipeline, "by_decision": by_decision}


order_repository = OrderRepository()
