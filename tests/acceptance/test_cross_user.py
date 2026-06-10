"""Acceptance tests: Cross-user overwrite protection (Section 3.4)

Verifies:
- User B uploading same task_order_id as User A gets CrossUserConflictError
- User A's data, version, status, and events remain unchanged
- task_order_id is globally unique, ownership cannot be silently transferred
"""

import pytest

from app.models.user import User
from app.repositories.event_repository import event_repository
from app.repositories.order_repository import order_repository
from app.schemas.order import OrderIngestRequest
from app.services.order_service import CrossUserConflictError, order_service


class TestCrossUserOverwrite:
    """Verify task_order_id ownership conflict is rejected, not silently transferred."""

    @pytest.mark.asyncio
    async def test_user_b_cannot_claim_user_a_task_order_id(self, db_session):
        """User B uploading same task_order_id must raise CrossUserConflictError."""
        user_a = User(arms_account="cross-a-1", id="u-cross-a-1")
        user_b = User(arms_account="cross-b-1", id="u-cross-b-1")
        db_session.add_all([user_a, user_b])
        await db_session.flush()

        req_a = OrderIngestRequest(
            task_order_id="TN-CROSS-001",
            order_snapshot={"skc": "A-SKC"},
            raw_detail={"owner": "A"},
        )
        order_a, created_a = await order_service.ingest(db_session, req_a, user_a)
        await db_session.commit()
        assert created_a is True
        assert order_a.owner_user_id == user_a.id

        req_b = OrderIngestRequest(
            task_order_id="TN-CROSS-001",
            order_snapshot={"skc": "B-SKC"},
            raw_detail={"owner": "B"},
        )
        with pytest.raises(CrossUserConflictError) as exc_info:
            await order_service.ingest(db_session, req_b, user_b)
        await db_session.rollback()

        assert "TN-CROSS-001" in str(exc_info.value)
        assert "cross-a-1" in exc_info.value.existing_owner

    @pytest.mark.asyncio
    async def test_user_a_data_unchanged_after_rejected_claim(self, db_session):
        """After User B's claim is rejected, User A's data is exactly as before."""
        user_a = User(arms_account="cross-a-2", id="u-cross-a-2")
        user_b = User(arms_account="cross-b-2", id="u-cross-b-2")
        db_session.add_all([user_a, user_b])
        await db_session.flush()

        req_a = OrderIngestRequest(
            task_order_id="TN-CROSS-002",
            order_snapshot={"skc": "ORIGINAL"},
            raw_detail={"data": "original"},
        )
        order_a, _ = await order_service.ingest(db_session, req_a, user_a)
        await db_session.commit()
        v_before = order_a.order_version
        status_before = order_a.pipeline_status
        owner_before = order_a.owner_user_id

        req_b = OrderIngestRequest(
            task_order_id="TN-CROSS-002",
            order_snapshot={"skc": "EVIL-OVERWRITE"},
            raw_detail={"data": "evil"},
        )
        try:
            await order_service.ingest(db_session, req_b, user_b)
            await db_session.commit()
        except CrossUserConflictError:
            # Don't explicitly rollback — the session fixture handles cleanup.
            # Explicit rollback can cause MissingGreenlet with aiosqlite.
            pass

        order_a_after = await order_repository.get_by_task_order_id_and_owner(
            db_session, "TN-CROSS-002", user_a.id
        )
        assert order_a_after is not None, "User A's order should still exist"
        assert order_a_after.owner_user_id == owner_before, "Owner must not change"
        assert order_a_after.order_version == v_before, "Version must not change"
        assert order_a_after.pipeline_status == status_before, "Status must not change"

    @pytest.mark.asyncio
    async def test_events_still_belong_to_user_a(self, db_session):
        """After rejection, all events still reference User A only."""
        user_a = User(arms_account="cross-a-3", id="u-cross-a-3")
        user_b = User(arms_account="cross-b-3", id="u-cross-b-3")
        db_session.add_all([user_a, user_b])
        await db_session.flush()

        req_a = OrderIngestRequest(
            task_order_id="TN-CROSS-003",
            order_snapshot={"skc": "EVENT-OWNER"},
            raw_detail={},
        )
        order_a, _ = await order_service.ingest(db_session, req_a, user_a)
        await db_session.commit()

        req_b = OrderIngestRequest(
            task_order_id="TN-CROSS-003",
            order_snapshot={"skc": "EVIL"},
            raw_detail={},
        )
        try:
            await order_service.ingest(db_session, req_b, user_b)
            await db_session.commit()
        except CrossUserConflictError:
            # Don't explicitly rollback — the session fixture handles cleanup.
            # Explicit rollback can cause MissingGreenlet with aiosqlite.
            pass

        events = await event_repository.get_events_since(db_session, user_a.id, 0)
        for evt in events:
            assert evt.owner_user_id == user_a.id, (
                f"Event {evt.id} ({evt.event_type}) has owner {evt.owner_user_id}, expected {user_a.id}"
            )

        events_b = await event_repository.get_events_since(db_session, user_b.id, 0)
        assert len(events_b) == 0, f"User B should have no events, got {len(events_b)}"
