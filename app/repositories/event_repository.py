from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_event import OrderEvent
from app.repositories.order_repository import Scope


class EventRepository:
    model = OrderEvent

    async def create_event(
        self,
        db: AsyncSession,
        order_id: str,
        owner_user_id: str,
        event_type: str,
        order_version: int,
        payload: dict[str, Any] | None = None,
    ) -> OrderEvent:
        event = OrderEvent(
            order_id=order_id,
            owner_user_id=owner_user_id,
            event_type=event_type,
            order_version=order_version,
            payload=payload or {},
        )
        db.add(event)
        await db.flush()
        return event

    async def get_events_since(
        self,
        db: AsyncSession,
        owner_user_id: str,
        since_event_id: int = 0,
        limit: int = 100,
        scope: Scope = "own",
    ) -> Sequence[OrderEvent]:
        conditions = [OrderEvent.id > since_event_id]
        if scope == "own":
            conditions.append(OrderEvent.owner_user_id == owner_user_id)
        stmt = (
            select(OrderEvent)
            .where(and_(*conditions) if conditions else __import__("sqlalchemy").true())
            .order_by(OrderEvent.id.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_latest_event_id(
        self, db: AsyncSession, owner_user_id: str, scope: Scope = "own",
    ) -> int:
        conditions = []
        if scope == "own":
            conditions.append(OrderEvent.owner_user_id == owner_user_id)
        stmt = select(OrderEvent.id)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(OrderEvent.id.desc()).limit(1)
        result = await db.execute(stmt)
        row = result.scalar()
        return row or 0


event_repository = EventRepository()
