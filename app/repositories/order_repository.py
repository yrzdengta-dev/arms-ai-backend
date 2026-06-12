import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order


Scope = Literal["all", "own"]


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
        scope: Scope = "own",
        cert_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        arms_audit_status: str | None = None,
        arms_audit_result: str | None = None,
    ) -> Sequence[Order]:
        conditions: list = []
        if scope == "own":
            conditions.append(Order.owner_user_id == owner_user_id)

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
                or_(
                    Order.task_order_id.ilike(f"%{search}%"),
                    Order.order_snapshot["skc"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["product_name"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["supplier_name"].as_string().ilike(f"%{search}%"),
                )
            )
        if cert_type:
            cert_values = [v.strip() for v in cert_type.split(",") if v.strip()]
            if cert_values:
                conditions.append(
                    Order.order_snapshot["certificate_type_name"].as_string().in_(cert_values)
                )
        if created_after is not None:
            conditions.append(Order.created_at >= created_after)
        if created_before is not None:
            conditions.append(Order.created_at <= created_before)
        if arms_audit_status:
            conditions.append(Order.arms_audit_status == arms_audit_status)
        if arms_audit_result:
            conditions.append(Order.arms_audit_result == arms_audit_result)
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
        scope: Scope = "own",
        cert_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        arms_audit_status: str | None = None,
        arms_audit_result: str | None = None,
    ) -> int:
        conditions: list = []
        if scope == "own":
            conditions.append(Order.owner_user_id == owner_user_id)

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
                or_(
                    Order.task_order_id.ilike(f"%{search}%"),
                    Order.order_snapshot["skc"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["product_name"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["supplier_name"].as_string().ilike(f"%{search}%"),
                )
            )
        if cert_type:
            cert_values = [v.strip() for v in cert_type.split(",") if v.strip()]
            if cert_values:
                conditions.append(
                    Order.order_snapshot["certificate_type_name"].as_string().in_(cert_values)
                )
        if created_after is not None:
            conditions.append(Order.created_at >= created_after)
        if created_before is not None:
            conditions.append(Order.created_at <= created_before)
        if arms_audit_status:
            conditions.append(Order.arms_audit_status == arms_audit_status)
        if arms_audit_result:
            conditions.append(Order.arms_audit_result == arms_audit_result)
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
        self, db: AsyncSession, owner_user_id: str, scope: Scope = "own"
    ) -> dict[str, Any]:
        base_conditions = []
        if scope == "own":
            base_conditions.append(Order.owner_user_id == owner_user_id)
        total_stmt = select(func.count()).select_from(Order)
        if base_conditions:
            total_stmt = total_stmt.where(and_(*base_conditions))
        total_result = await db.execute(total_stmt)
        total = total_result.scalar() or 0

        pipeline_stmt = (
            select(Order.pipeline_status, func.count())
        )
        if base_conditions:
            pipeline_stmt = pipeline_stmt.where(and_(*base_conditions))
        pipeline_stmt = pipeline_stmt.group_by(Order.pipeline_status)
        pipeline_result = await db.execute(pipeline_stmt)
        by_pipeline = {row[0]: row[1] for row in pipeline_result}

        from app.models.audit_result import AuditResult

        decision_stmt = (
            select(AuditResult.decision, func.count())
            .select_from(Order)
            .join(AuditResult, Order.id == AuditResult.order_id, isouter=True)
        )
        if base_conditions:
            decision_stmt = decision_stmt.where(and_(*base_conditions))
        decision_stmt = decision_stmt.group_by(AuditResult.decision)
        decision_result = await db.execute(decision_stmt)
        by_decision = {row[0] or "PENDING": row[1] for row in decision_result}

        return {"total": total, "by_pipeline_status": by_pipeline, "by_decision": by_decision}


order_repository = OrderRepository()
