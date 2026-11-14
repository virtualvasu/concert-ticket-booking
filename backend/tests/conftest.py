import os

import httpx
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://booking:booking@localhost:55432/booking_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")

# Point the app at the test services *before* it is imported, and override (not
# default) so a real DATABASE_URL in the environment can never be truncated.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL

from app import db as app_db  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.redis_client import get_redis  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    await app_db.init_db()
    yield app_db.engine
    await app_db.engine.dispose()


@pytest_asyncio.fixture
async def session(test_engine):
    """AsyncSession on the test DB; every table is truncated before the test."""
    async with test_engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        if tables:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def redis():
    """Redis client on db 15, flushed before each test."""
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await r.flushdb()
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def client(session, redis):
    """httpx client on the ASGI app, wired to the test session and redis."""

    async def _session():
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
