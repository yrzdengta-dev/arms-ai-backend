import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from app.repositories.event_repository import event_repository
from app.repositories.order_repository import Scope

logger = logging.getLogger(__name__)


async def event_stream(
    db_factory,
    owner_user_id: str,
    last_event_id: int = 0,
    heartbeat_interval: int = 15,
    scope: Scope = "own",
) -> AsyncGenerator[str, None]:
    since_id = last_event_id
    heartbeat_count = 0  # Initialize OUTSIDE the loop

    # Catch-up: send missed events
    async with db_factory() as db:
        events = await event_repository.get_events_since(db, owner_user_id, since_id, scope=scope)
        for event in events:
            yield _format_sse(event.event_type, event)
            since_id = max(since_id, event.id)

    # Poll loop
    while True:
        await asyncio.sleep(1)
        async with db_factory() as db:
            events = await event_repository.get_events_since(db, owner_user_id, since_id, scope=scope)
            sent_any = False
            for event in events:
                yield _format_sse(event.event_type, event)
                since_id = max(since_id, event.id)
                sent_any = True

            if not sent_any:
                heartbeat_count += 1
                if heartbeat_count >= heartbeat_interval:
                    yield ": heartbeat\n\n"
                    heartbeat_count = 0
            else:
                heartbeat_count = 0


def _format_sse(event_type: str, event) -> str:
    data = json.dumps(
        {
            "id": event.id,
            "order_id": event.order_id,
            "event_type": event.event_type,
            "order_version": event.order_version,
            "payload": event.payload,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        },
        default=str,
    )
    return f"id: {event.id}\nevent: {event_type}\ndata: {data}\n\n"
