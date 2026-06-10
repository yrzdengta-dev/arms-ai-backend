from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import _get_engine
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import contextlib

    from app.services.dispatcher import dispatch_outbox_loop

    dispatch_task = asyncio.create_task(dispatch_outbox_loop())
    yield
    dispatch_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await dispatch_task
    engine = _get_engine()
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    from app.core.database import is_db_healthy

    db_ok = await is_db_healthy()
    return {
        "status": "ready" if db_ok else "not_ready",
        "database": "ok" if db_ok else "unavailable",
    }
