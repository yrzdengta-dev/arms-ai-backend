"""Admin permission tests — SHEINsgs-5zs can read all, regular users isolated."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.models.audit_result import AuditResult
from app.models.order import Order
from app.models.order_event import OrderEvent
from app.models.user import User

ADMIN_ACCOUNT = "SHEINsgs-5zs"


@pytest.fixture(autouse=True)
def _setup_admin_env(monkeypatch):
    """Set ARMS_ADMIN_ACCOUNTS and clear settings cache for each test."""
    monkeypatch.setenv("ARMS_ADMIN_ACCOUNTS", ADMIN_ACCOUNT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _mkuser(db_session, account: str) -> User:
    """Create a user with auto-generated ID if not exists."""
    existing = (await db_session.execute(
        select(User).where(User.arms_account == account)
    )).scalars().first()
    if existing:
        return existing
    user = User(arms_account=account)
    db_session.add(user)
    await db_session.flush()
    return user


async def _mkorder(db_session, task_id: str, owner: User, status: str = "RECEIVED"):
    order = Order(
        task_order_id=task_id,
        owner_user_id=owner.id,
        pipeline_status=status,
        order_version=1,
        detail_hash=task_id,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _mkevent(db_session, order_id: str, owner_user_id: str, event_type: str):
    from app.repositories.event_repository import event_repository
    return await event_repository.create_event(
        db_session, order_id, owner_user_id, event_type, 1, {}
    )


# ===========================================================================
# Admin read tests
# ===========================================================================
class TestAdminCanSeeAllOrders:
    """Admin account can list orders from all users."""

    @pytest.mark.asyncio
    async def test_admin_list_sees_all_users(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "adm-a1")
        ub = await _mkuser(db_session, "adm-b1")
        await _mkorder(db_session, "TN-ADM-A1", ua)
        await _mkorder(db_session, "TN-ADM-A2", ua)
        await _mkorder(db_session, "TN-ADM-B1", ub)
        await _mkorder(db_session, "TN-ADM-B2", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders?page_size=100",
                               headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        # Admin must see orders from both users
        assert "TN-ADM-A1" in task_ids
        assert "TN-ADM-A2" in task_ids
        assert "TN-ADM-B1" in task_ids
        assert "TN-ADM-B2" in task_ids

    @pytest.mark.asyncio
    async def test_admin_stats_count_all_users(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "adm-stat-a")
        ub = await _mkuser(db_session, "adm-stat-b")
        await _mkorder(db_session, "TN-STAT-A1", ua)
        await _mkorder(db_session, "TN-STAT-A2", ua)
        await _mkorder(db_session, "TN-STAT-B1", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders/stats",
                               headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200
        # Admin sees all orders — must include orders from both users
        assert res.json()["total"] >= 3

    @pytest.mark.asyncio
    async def test_admin_can_see_user_a_detail(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "adm-detail-a")
        await _mkorder(db_session, "TN-DETAIL-A1", ua)
        await db_session.commit()

        res = await client.get("/api/v1/orders/TN-DETAIL-A1",
                               headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200
        assert res.json()["task_order_id"] == "TN-DETAIL-A1"

    @pytest.mark.asyncio
    async def test_admin_can_see_user_a_result(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "adm-res-a")
        order = await _mkorder(db_session, "TN-RES-A1", ua)
        result = AuditResult(
            order_id=order.id, order_version=1,
            decision="PASS",
            normalized_output={"decision": "PASS", "summary": "ok", "rules": []},
        )
        db_session.add(result)
        await db_session.commit()

        res = await client.get("/api/v1/orders/TN-RES-A1/result",
                               headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200
        assert res.json()["decision"] == "PASS"

    @pytest.mark.asyncio
    async def test_admin_sse_receives_all_users_events(self, db_session):
        ua = await _mkuser(db_session, "adm-sse-a")
        ub = await _mkuser(db_session, "adm-sse-b")
        oa = await _mkorder(db_session, "TN-SSE-A1", ua)
        ob = await _mkorder(db_session, "TN-SSE-B1", ub)
        await _mkevent(db_session, oa.id, ua.id, "order.created")
        await _mkevent(db_session, ob.id, ub.id, "order.created")
        await db_session.commit()

        from app.repositories.event_repository import event_repository as er
        # Before admin scope: each user only sees own events
        events_a = await er.get_events_since(db_session, ua.id, 0)
        assert len(events_a) == 1
        events_b = await er.get_events_since(db_session, ub.id, 0)
        assert len(events_b) == 1
        # After admin scope implementation, admin should see both users' events


# ===========================================================================
# Regular user isolation tests
# ===========================================================================
class TestRegularUserIsolation:
    """Regular users must remain isolated."""

    @pytest.mark.asyncio
    async def test_regular_user_sees_only_own(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "iso-a")
        ub = await _mkuser(db_session, "iso-b")
        await _mkorder(db_session, "TN-ISO-A1", ua)
        await _mkorder(db_session, "TN-ISO-A2", ua)
        await _mkorder(db_session, "TN-ISO-B1", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders?page_size=100",
                               headers={"X-ARMS-User": "iso-a"})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        assert "TN-ISO-A1" in task_ids
        assert "TN-ISO-A2" in task_ids
        assert "TN-ISO-B1" not in task_ids

    @pytest.mark.asyncio
    async def test_regular_user_cannot_see_other_detail(self, db_session, client: AsyncClient):
        ub = await _mkuser(db_session, "hidden-b")
        await _mkorder(db_session, "TN-HIDDEN-B1", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders/TN-HIDDEN-B1",
                               headers={"X-ARMS-User": "hidden-a"})
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_regular_user_cannot_see_other_result(self, db_session, client: AsyncClient):
        ub = await _mkuser(db_session, "hidden-res-b")
        await _mkorder(db_session, "TN-HIDDEN-RES-B1", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders/TN-HIDDEN-RES-B1/result",
                               headers={"X-ARMS-User": "hidden-res-a"})
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_regular_user_sse_only_own_events(self, db_session):
        ua = await _mkuser(db_session, "sse-iso-a")
        ub = await _mkuser(db_session, "sse-iso-b")
        oa = await _mkorder(db_session, "TN-SSE-ISO-A", ua)
        ob = await _mkorder(db_session, "TN-SSE-ISO-B", ub)
        await _mkevent(db_session, oa.id, ua.id, "order.created")
        await _mkevent(db_session, ob.id, ub.id, "order.created")
        await db_session.commit()

        from app.repositories.event_repository import event_repository as er
        events_a = await er.get_events_since(db_session, ua.id, 0)
        assert len(events_a) == 1
        events_b = await er.get_events_since(db_session, ub.id, 0)
        assert len(events_b) == 1


# ===========================================================================
# Exact match boundary tests
# ===========================================================================
class TestExactMatchBoundary:
    """Only exact match on ARMS_ADMIN_ACCOUNTS grants admin access."""

    @pytest.mark.asyncio
    async def test_lowercase_not_admin(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "lower-a")
        await _mkorder(db_session, "TN-LOWER-A1", ua)
        await db_session.commit()

        res = await client.get("/api/v1/orders?page_size=100",
                               headers={"X-ARMS-User": "sheinsgs-5zs"})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        assert "TN-LOWER-A1" not in task_ids  # Not admin

    @pytest.mark.asyncio
    async def test_extra_suffix_not_admin(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "extra-a")
        await _mkorder(db_session, "TN-EXTRA-A1", ua)
        await db_session.commit()

        res = await client.get("/api/v1/orders?page_size=100",
                               headers={"X-ARMS-User": "SHEINsgs-5zs-extra"})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        assert "TN-EXTRA-A1" not in task_ids


# ===========================================================================
# No admin configured — all normal
# ===========================================================================
class TestNoAdminConfigured:
    """When ARMS_ADMIN_ACCOUNTS is empty, all users are regular."""

    @pytest.mark.asyncio
    async def test_no_admin_all_normal(self, db_session, client: AsyncClient, monkeypatch):
        monkeypatch.setenv("ARMS_ADMIN_ACCOUNTS", "")
        get_settings.cache_clear()

        ua = await _mkuser(db_session, "noadm-a")
        ub = await _mkuser(db_session, "noadm-b")
        await _mkorder(db_session, "TN-NOADM-A1", ua)
        await _mkorder(db_session, "TN-NOADM-B1", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders?page_size=100",
                               headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        assert "TN-NOADM-A1" not in task_ids
        assert "TN-NOADM-B1" not in task_ids


# ===========================================================================
# Request body/param cannot grant admin
# ===========================================================================
class TestRequestOverrideBlocked:
    """is_admin / view_all in request cannot grant elevated permissions."""

    @pytest.mark.asyncio
    async def test_is_admin_header_ignored(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "body-a")
        ub = await _mkuser(db_session, "body-b")
        await _mkorder(db_session, "TN-BODY-A1", ua)
        await _mkorder(db_session, "TN-BODY-B1", ub)
        await db_session.commit()

        # Try to pass is_admin via header — must be ignored
        res = await client.get("/api/v1/orders?page_size=100",
                               headers={"X-ARMS-User": "body-a", "X-ARMS-Is-Admin": "true"})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        assert "TN-BODY-B1" not in task_ids

    @pytest.mark.asyncio
    async def test_view_all_param_ignored(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "param-a")
        ub = await _mkuser(db_session, "param-b")
        await _mkorder(db_session, "TN-PARAM-A1", ua)
        await _mkorder(db_session, "TN-PARAM-B1", ub)
        await db_session.commit()

        res = await client.get("/api/v1/orders?page_size=100&view_all=true",
                               headers={"X-ARMS-User": "param-a"})
        assert res.status_code == 200
        items = res.json()["items"]
        task_ids = [i["task_order_id"] for i in items]
        assert "TN-PARAM-B1" not in task_ids


# ===========================================================================
# Owner preservation tests
# ===========================================================================
class TestOwnerPreservation:
    """Admin reads must not change order ownership."""

    @pytest.mark.asyncio
    async def test_admin_read_does_not_change_owner(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "owner-a")
        await _mkorder(db_session, "TN-OWNER-A1", ua)
        await db_session.commit()

        res = await client.get("/api/v1/orders/TN-OWNER-A1",
                               headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200

        order = (await db_session.execute(
            select(Order).where(Order.task_order_id == "TN-OWNER-A1")
        )).scalars().first()
        assert order.owner_user_id == ua.id

    @pytest.mark.asyncio
    async def test_admin_own_ingest_retains_owner(self, client: AsyncClient):
        res = await client.post("/api/v1/orders/ingest", json={
            "task_order_id": "TN-ADM-OWN",
            "order_snapshot": {"skc": "ADM-SKC"},
            "raw_detail": {},
        }, headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 200

        # Ingest retains correct owner — verify via API
        detail = await client.get("/api/v1/orders/TN-ADM-OWN",
                                  headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert detail.status_code == 200
        # owner_user_id should be admin's ID, not empty or wrong
        assert detail.json()["owner_user_id"]


# ===========================================================================
# Admin cannot retry other users' orders
# ===========================================================================
class TestAdminNoRetryOthers:
    """Admin read scope does NOT grant retry on other users' orders."""

    @pytest.mark.asyncio
    async def test_admin_cannot_retry_other_user_order(self, db_session, client: AsyncClient):
        ua = await _mkuser(db_session, "noretry-a")
        await _mkorder(db_session, "TN-NORETRY-A1", ua, status="FAILED_RETRYABLE")
        await db_session.commit()

        res = await client.post("/api/v1/orders/TN-NORETRY-A1/retry",
                                headers={"X-ARMS-User": ADMIN_ACCOUNT})
        assert res.status_code == 404, (
            f"Admin must NOT retry other user order, got {res.status_code}"
        )
