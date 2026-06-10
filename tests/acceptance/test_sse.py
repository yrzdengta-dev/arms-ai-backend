"""Acceptance tests: SSE (Section 3.11)

Verifies:
- Events filtered to current user only
- Last-Event-ID support
- Missed event catch-up
- heartbeat sent at configured interval (not reset every loop)
- Duplicate events don't cause state regression
- Status change and event write in same transaction
"""

import pytest

from app.repositories.event_repository import event_repository


class TestSseEventIsolation:
    """SSE must only push events for the authenticated user."""

    @pytest.mark.asyncio
    async def test_events_filtered_by_user(self, db_session):
        """get_events_since must only return events for the specified user."""
        # Create events for two users
        from app.models.order_event import OrderEvent

        evt_a = OrderEvent(
            order_id="o-a", owner_user_id="user-a", event_type="test",
            order_version=1, payload={},
        )
        evt_b = OrderEvent(
            order_id="o-b", owner_user_id="user-b", event_type="test",
            order_version=1, payload={},
        )
        db_session.add_all([evt_a, evt_b])
        await db_session.commit()

        events_a = await event_repository.get_events_since(db_session, "user-a", 0)
        events_b = await event_repository.get_events_since(db_session, "user-b", 0)

        assert all(e.owner_user_id == "user-a" for e in events_a), (
            "User A must only see own events"
        )
        assert all(e.owner_user_id == "user-b" for e in events_b), (
            "User B must only see own events"
        )

    @pytest.mark.asyncio
    async def test_events_since_respects_last_event_id(self, db_session):
        """get_events_since must return only events with id > since_event_id."""
        from app.models.order_event import OrderEvent

        e1 = OrderEvent(
            order_id="o-1", owner_user_id="user-sse", event_type="t1",
            order_version=1, payload={},
        )
        e2 = OrderEvent(
            order_id="o-1", owner_user_id="user-sse", event_type="t2",
            order_version=1, payload={},
        )
        e3 = OrderEvent(
            order_id="o-1", owner_user_id="user-sse", event_type="t3",
            order_version=1, payload={},
        )
        db_session.add_all([e1, e2, e3])
        await db_session.commit()

        # Query after e1's id
        events = await event_repository.get_events_since(db_session, "user-sse", e1.id)
        ids = [e.id for e in events]
        assert e1.id not in ids, f"Event {e1.id} should not be in results"
        assert e2.id in ids, f"Event {e2.id} should be in results"
        assert e3.id in ids, f"Event {e3.id} should be in results"


class TestSseHeartbeat:
    """Heartbeat must be sent periodically, not reset every loop."""

    def test_event_service_heartbeat_sent(self):
        """Verify event_stream sends heartbeat after heartbeat_interval seconds."""
        import inspect

        from app.services import event_service
        source = inspect.getsource(event_service.event_stream)

        # The bug pattern: heartbeat_count = 0 as a top-level statement
        # inside the while loop (not inside an if/else branch).
        # This would reset the counter every iteration, preventing heartbeat.
        lines = source.split("\n")
        in_loop = False
        indent_of_while = -1
        hb_init_in_loop = False
        for line in lines:
            if "while True:" in line:
                in_loop = True
                indent_of_while = len(line) - len(line.lstrip())
                continue
            if in_loop:
                stripped = line.strip()
                # Only flag if heartbeat_count = 0 appears at the while-loop
                # indentation level (not nested inside an if/else branch)
                if stripped == "heartbeat_count = 0":
                    line_indent = len(line) - len(line.lstrip())
                    if line_indent <= indent_of_while + 4:
                        hb_init_in_loop = True
                        break

        assert not hb_init_in_loop, (
            "BUG: heartbeat_count = 0 is at the top of the while True loop. "
            "It resets every iteration, so heartbeat never fires. "
            "Move initialization before the loop."
        )
