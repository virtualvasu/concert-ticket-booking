import time
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.buyer import router as buyer_router
from app.db import get_session, init_db
from app.redis_client import get_redis


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Concert booking", lifespan=lifespan)
app.include_router(buyer_router)


@app.middleware("http")
async def count_requests(request: Request, call_next):
    """INCR rps:{epoch_second} (300 s expiry) for every /api/ call except admin."""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/admin/"):
        try:
            # Honour test overrides: middleware does not go through Depends().
            redis = request.app.dependency_overrides.get(get_redis, get_redis)()
            key = f"rps:{int(time.time())}"
            async with redis.pipeline(transaction=False) as pipe:
                pipe.incr(key)
                pipe.expire(key, 300)
                await pipe.execute()
        except Exception:
            pass  # metrics must never break a request
    return await call_next(request)


@app.get("/api/health")
async def health(
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    await session.execute(text("SELECT 1"))
    await redis.ping()
    return {"status": "ok", "db": True, "redis": True}
