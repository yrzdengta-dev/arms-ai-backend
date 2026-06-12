"""Acceptance tests: SSE runtime (P1-1).

Verifies the SSE event_stream generator works with the correct
session factory type — no coroutine context-manager errors.
"""

import asyncio
import inspect

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_event import OrderEvent
from app.models.user import User
from app.services.event_service import event_stream


class TestSseRuntime:
    """SSE endpoint must not produce coroutine context-manager errors."""

    @pytest.mark.asyncio
    async def test_event_stream_with_sync_factory(self, db_session, monkeypatch):
        """event_stream with a sync db_factory iterates events without error."""
        from app.core.database import _get_session_factory

        user = User(arms_account="sse-test", id="u-sse-rt")
        order_event = OrderEvent(
            order_id="ord-sse-1",
            owner_user_id=user.id,
            event_type="test.event",
            order_version=1,
            payload={"msg": "hello"},
        )
        db_session.add_all([user, order_event])
        await db_session.commit()

        # Use same pattern as endpoint: sync factory returning AsyncSession
        def _db_factory():
            factory = _get_session_factory()
            return factory()

        gen = event_stream(
            db_factory=_db_factory,
            owner_user_id=user.id,
            last_event_id=0,
            heartbeat_interval=1,
            scope="own",
        )

        # Iterate catch-up: should yield at least one SSE event
        events = []
        try:
            async for chunk in gen:
                events.append(chunk)
                if len(events) >= 3:
                    break
        except TypeError as e:
            pytest.fail(f"Sync db_factory raised TypeError: {e}")

        # At least one non-heartbeat event
        data_events = [e for e in events if e.startswith("id:")]
        assert len(data_events) >= 1, f"Expected at least one SSE event, got: {events}"

    @pytest.mark.asyncio
    async def test_async_factory_causes_type_error(self, db_session, monkeypatch):
        """An async db_factory must fail — confirms the bug (P1-1).

        After the fix, the endpoint uses a sync factory. This test
        documents the bug pattern and serves as a regression guard.
        """
        from app.core.database import _get_session_factory

        user = User(arms_account="sse-bug", id="u-sse-bug")
        db_session.add(user)
        await db_session.commit()

        # The bug pattern: async def returning factory()
        async def _bad_db_factory():
            factory = _get_session_factory()
            return factory()

        gen = event_stream(
            db_factory=_bad_db_factory,
            owner_user_id=user.id,
            last_event_id=0,
            heartbeat_interval=99,
            scope="own",
        )

        with pytest.raises(TypeError) as exc:
            async for _ in gen:
                pass

        assert "coroutine" in str(exc.value).lower() or "async" in str(exc.value).lower(), (
            f"Expected TypeError about coroutine, got: {exc.value}"
        )


class TestSseEndpointFactoryType:
    """The endpoint's _db_factory must be a plain function, not async."""

    def test_db_factory_is_not_async(self):
        """P1-1: stream_events() must NOT contain 'async def _db_factory'."""
        import inspect as _inspect
        from app.api.v1.endpoints import events as events_module

        source = _inspect.getsource(events_module.stream_events)
        assert "async def _db_factory" not in source, (
            "BUG: _db_factory is async def inside stream_events(). "
            "Must be plain def. Fix P1-1."
        )
