import asyncio
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.fixture
async def client(engine, db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

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
