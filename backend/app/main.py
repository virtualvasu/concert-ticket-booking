from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, init_db
from app.redis_client import get_redis


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Concert booking", lifespan=lifespan)


@app.get("/api/health")
async def health(
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    await session.execute(text("SELECT 1"))
    await redis.ping()
    return {"status": "ok", "db": True, "redis": True}
