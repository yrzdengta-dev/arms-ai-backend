from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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

    from app.adapters.storage.minio import minio_storage
    from app.services.dispatcher import dispatch_outbox_loop

    await minio_storage.ensure_bucket()

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
    allow_origin_regex=r"(chrome-extension://[a-z]{32}|http://localhost(:\d+)?|https://arms\.biz\.sheincorp\.cn)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Last-Event-ID"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    from app.core.database import is_db_healthy

    db_ok = await is_db_healthy()
    redis_ok = await _check_redis()
    minio_ok = await _check_minio()

    all_ok = db_ok and redis_ok and minio_ok
    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "not_ready",
            "database": "ok" if db_ok else "unavailable",
            "redis": "ok" if redis_ok else "unavailable",
            "minio": "ok" if minio_ok else "unavailable",
        },
    )


async def _check_redis() -> bool:
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        await r.ping()
        await r.close()
        return True
    except Exception:
        return False


async def _check_minio() -> bool:
    import asyncio as _asyncio
    try:
        from minio import Minio
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        await _asyncio.to_thread(client.list_buckets)
        return True
    except Exception:
        return False
