"""Buyer-facing routes: sale info, queue, me, pay, baseline grab."""

from datetime import datetime
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, utcnow
from app.db import get_session
from app.domain.queue import QueueStore
from app.domain.sale import get_current_sale, seat_counts
from app.domain.seats import hold_best_available
from app.domain.users import join_sale
from app.models import Booking, Hold, Sale, Seat, User
from app.payments.gateway import GatewayClient, get_gateway
from app.payments.service import pay
from app.redis_client import get_redis
from app.schemas import PayResult
from app.trace import record

router = APIRouter(prefix="/api")

_ACTIVE_STATES = ("waiting", "offered", "booked", "browsing")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def sale_summary(
    session: AsyncSession, redis: aioredis.Redis, sale: Sale, now: datetime
) -> dict:
    """The public view of a sale (also embedded in the admin stats)."""
    queue_length = await QueueStore(redis, sale.id).length() if sale.mode == "bucket" else 0
    return {
        "sale_id": sale.id,
        "mode": sale.mode,
        "status": sale.status,
        "end_reason": sale.end_reason,
        "seats": await seat_counts(session, sale.id),
        "seats_per_tick": sale.seats_per_tick,
        "tick_seconds": sale.tick_seconds,
        "hold_seconds": sale.hold_seconds,
        "next_tick_at": _iso(sale.next_tick_at),
        "queue_length": queue_length,
        "server_time": _iso(now),
    }


def _seat_dict(seat: Seat) -> dict:
    return {"section": seat.section, "row": seat.row, "number": seat.number, "price": seat.price}


async def _offer_dict(session: AsyncSession, hold: Hold, sale: Sale) -> dict:
    seat = await session.get(Seat, hold.seat_id, populate_existing=True)
    return {
        "hold_id": hold.id,
        "seat": _seat_dict(seat),
        "expires_at": _iso(hold.expires_at),
        "attempts_left": max(0, sale.max_payment_attempts - hold.attempts),
    }


async def _live_hold(session: AsyncSession, user_id: str, now: datetime) -> Hold | None:
    return (
        await session.execute(
            select(Hold)
            .where(Hold.user_id == user_id, Hold.status == "active", Hold.expires_at > now)
            .order_by(Hold.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


@router.get("/sale")
async def get_sale(
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    sale = await get_current_sale(session)
    if sale is None:
        raise HTTPException(404, "no sale")
    await session.refresh(sale)
    return await sale_summary(session, redis, sale, utcnow())


class JoinRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/queue/join")
async def join_queue(
    body: JoinRequest,
    x_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    sale = await get_current_sale(session)
    if sale is None:
        raise HTTPException(409, "no sale")
    await session.refresh(sale)
    if sale.status != "running":
        raise HTTPException(409, "sale not running")

    if x_user_id:
        existing = await session.get(User, x_user_id, populate_existing=True)
        if existing is not None and existing.sale_id == sale.id and existing.state in _ACTIVE_STATES:
            raise HTTPException(409, "already joined")

    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name required")
    try:
        user = await join_sale(session, redis, sale, name, utcnow())
    except ValueError:
        raise HTTPException(409, "sale not running")
    position = await QueueStore(redis, sale.id).position(user.id) if sale.mode == "bucket" else None
    return {"user_id": user.id, "position": position}


@router.get("/me")
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    now = utcnow()
    sale = await session.get(Sale, user.sale_id, populate_existing=True)

    position = None
    if user.state == "waiting":
        position = await QueueStore(redis, user.sale_id).position(user.id)

    offer = None
    if user.state == "offered":
        hold = await _live_hold(session, user.id, now)
        if hold is not None:
            offer = await _offer_dict(session, hold, sale)

    booking = None
    if user.state == "booked":
        row = (
            await session.execute(
                select(Booking).where(Booking.user_id == user.id).execution_options(populate_existing=True)
            )
        ).scalars().first()
        if row is not None:
            seat = await session.get(Seat, row.seat_id, populate_existing=True)
            booking = {
                "booking_id": row.id,
                "seat": _seat_dict(seat),
                "created_at": _iso(row.created_at),
            }

    return {
        "user_id": user.id,
        "name": user.name,
        "state": user.state,
        "reason": user.reason,
        "position": position,
        "ahead": position - 1 if position else None,
        "offer": offer,
        "booking": booking,
        "server_time": _iso(now),
    }


class PayRequest(BaseModel):
    hold_id: int
    idempotency_key: str = Field(min_length=1, max_length=100)
    force: Literal["success", "fail"] | None = None


@router.post("/pay")
async def pay_route(
    body: PayRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    gateway: GatewayClient = Depends(get_gateway),
) -> PayResult:
    try:
        return await pay(
            session, gateway, user.id, body.hold_id, body.idempotency_key, body.force, utcnow
        )
    except PermissionError:
        raise HTTPException(403, "not your hold")


@router.post("/baseline/grab")
async def baseline_grab(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    sale = await session.get(Sale, user.sale_id, populate_existing=True)
    if sale.mode != "baseline" or sale.status != "running":
        raise HTTPException(409, "not a running baseline sale")

    # One in-flight grab per user, so a double click can't take two seats.
    lock = f"grab_lock:{user.id}"
    if not await redis.set(lock, "1", nx=True, ex=10):
        raise HTTPException(409, "grab in progress")
    try:
        await session.refresh(user)
        if user.state != "browsing":
            raise HTTPException(409, "already holds or booked a seat")
        now = utcnow()
        hold = await hold_best_available(session, sale, user.id, now)
        if hold is None:
            await record(session, sale.id, "grab_failed", now, user_id=user.id)
            await session.commit()
            raise HTTPException(409, "no_seats")
        return await _offer_dict(session, hold, sale)
    finally:
        await redis.delete(lock)
