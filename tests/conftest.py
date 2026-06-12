import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(
        TEST_DATABASE_URL, echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.fixture(autouse=True)
async def _isolate_test_data(engine):
    """Ensure committed rows from one test cannot affect later tests."""
    async with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(delete(table))
    yield


@pytest.fixture(autouse=True)
def _patch_task_db(monkeypatch, engine):
    """Route _get_session_factory through the test SQLite engine.

    Tasks (like _run_pdf_task) create their own sessions via
    _get_worker_session_factory(), which by default connects to PostgreSQL.
    This fixture redirects all callers to the shared in-memory SQLite
    database so tests can exercise task internals without Docker.
    """
    def _patched_factory():
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(
        "app.core.database._get_session_factory",
        _patched_factory,
    )
    monkeypatch.setattr(
        "app.workers.tasks._get_session_factory",
        _patched_factory,
    )
    monkeypatch.setattr(
        "app.workers.tasks._get_worker_session_factory",
        _patched_factory,
    )


@pytest.fixture
async def client(engine, db_session: AsyncSession, monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    from app.core.config import Settings

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Override settings to include test admin accounts.
    test_settings = Settings(admin_account_set=["SHEINsgs-5zs"])
    monkeypatch.setattr("app.core.config.get_settings", lambda: test_settings)
    monkeypatch.setattr("app.api.v1.endpoints.orders.get_settings", lambda: test_settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def default_headers():
    return {"X-ARMS-User": "auditor-a"}


@pytest.fixture
def sample_ingest_payload():
    return {
        "task_order_id": "TN202606100001",
        "task_uuid": "uuid-12345",
        "scene_id": "7",
        "audit_point_id": "9",
        "audit_node": "UserAudit_xxx",
        "business_type": None,
        "order_snapshot": {
            "task_order_id": "TN202606100001",
            "skc": "SKC-001",
            "product_name": "Test Product",
            "certificate_type_id": 1,
            "certificate_type_name": "CPC",
            "industry_id_list": [1],
            "industry_name_list": ["Toys"],
            "category_id": 10,
            "category_name": "Children Products",
        },
        "raw_detail": {"aca_task_field_dto": {"skc": "SKC-001"}},
        "pdf_files": [
            {"name": "report.pdf", "url": "https://example.com/report.pdf"}
        ],
    }
