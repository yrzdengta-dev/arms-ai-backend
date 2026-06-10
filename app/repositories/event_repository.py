from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_event import OrderEvent


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
    ) -> Sequence[OrderEvent]:
        stmt = (
            select(OrderEvent)
            .where(
                and_(
                    OrderEvent.owner_user_id == owner_user_id,
                    OrderEvent.id > since_event_id,
                )
            )
            .order_by(OrderEvent.id.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_latest_event_id(self, db: AsyncSession, owner_user_id: str) -> int:
        stmt = (
            select(OrderEvent.id)
            .where(OrderEvent.owner_user_id == owner_user_id)
            .order_by(OrderEvent.id.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalar()
        return row or 0


event_repository = EventRepository()
