"""Shared FastAPI dependencies for the API routers."""

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.payments.gateway import get_gateway
from app.redis_client import get_redis

__all__ = ["get_session", "get_redis", "get_gateway", "get_current_user", "utcnow"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_current_user(
    x_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    """The user named by the ``X-User-Id`` header; 401 if missing or unknown."""
    if not x_user_id:
        raise HTTPException(401, "missing X-User-Id")
    user = await session.get(User, x_user_id, populate_existing=True)
    if user is None:
        raise HTTPException(401, "unknown user")
    return user
