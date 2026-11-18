"""Queries behind the admin dashboard: live stats, time series, consistency."""

from datetime import datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import Integer, cast, distinct, exists, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.buyer import sale_summary
from app.models import Booking, Event, Hold, Payment, Sale, Seat, User

_SERIES_EVENTS = {
    "user_joined": "joins",
    "offer_made": "offers",
    "booking_confirmed": "bookings",
    "payment_attempt": "payment_attempts",
    "hold_expired": "expired",
}


async def _group_counts(session: AsyncSession, column, where) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).where(*where).group_by(column))
    return {k: n for k, n in rows.all()}


async def compute_stats(
    session: AsyncSession, redis: aioredis.Redis, sale: Sale, window: int, now: datetime
) -> dict:
    by_state = await _group_counts(session, User.state, [User.sale_id == sale.id])
    counts = {
        "joined": sum(by_state.values()),
        **{s: by_state.get(s, 0) for s in ("waiting", "offered", "booked", "left", "rejected")},
    }

    by_hold = await _group_counts(session, Hold.status, [Hold.sale_id == sale.id])
    holds = {s: by_hold.get(s, 0) for s in ("active", "paid", "expired", "failed")}

    by_pay = await _group_counts(session, Payment.status, [Payment.sale_id == sale.id])
    payments = {s: by_pay.get(s, 0) for s in ("succeeded", "failed", "refunded")}

    waited = func.extract("epoch", Booking.created_at - User.joined_at)
    p50, p95, longest = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(waited),
                func.percentile_cont(0.95).within_group(waited),
                func.max(waited),
            )
            .select_from(Booking)
            .join(User, User.id == Booking.user_id)
            .where(Booking.sale_id == sale.id)
        )
    ).one()
    wait_seconds = {
        "p50": float(p50) if p50 is not None else None,
        "p95": float(p95) if p95 is not None else None,
        "max": float(longest) if longest is not None else None,
    }

    end = int(now.timestamp())
    start = end - window + 1
    since = datetime.fromtimestamp(start, tz=now.tzinfo)
    bucket = cast(func.floor(func.extract("epoch", Event.ts)), Integer)
    rows = await session.execute(
        select(bucket, Event.type, func.count())
        .where(
            Event.sale_id == sale.id,
            Event.ts >= since,
            Event.type.in_(list(_SERIES_EVENTS)),
        )
        .group_by(bucket, Event.type)
    )
    per_second: dict[int, dict[str, int]] = {}
    for t, etype, n in rows.all():
        per_second.setdefault(t, {})[_SERIES_EVENTS[etype]] = n

    seconds = list(range(start, end + 1))
    rps_raw = await redis.mget([f"rps:{t}" for t in seconds]) if seconds else []
    series = []
    for t, rps in zip(seconds, rps_raw):
        point = {"t": t, "rps": int(rps) if rps else 0}
        for name in _SERIES_EVENTS.values():
            point[name] = per_second.get(t, {}).get(name, 0)
        series.append(point)

    return {
        "sale": await sale_summary(session, redis, sale, now),
        "counts": counts,
        "holds": holds,
        "payments": payments,
        "wait_seconds": wait_seconds,
        "series": series,
    }


async def consistency(session: AsyncSession, sale_id: int) -> dict[str, int]:
    """The four invariants from the design; every value must be 0."""
    dup = (
        select(Booking.seat_id)
        .where(Booking.sale_id == sale_id)
        .group_by(Booking.seat_id)
        .having(func.count() > 1)
        .subquery()
    )
    double_bookings = await session.scalar(select(func.count()).select_from(dup))

    paid = exists().where(Payment.id == Booking.payment_id, Payment.status == "succeeded")
    without_payment = await session.scalar(
        select(func.count()).select_from(Booking).where(Booking.sale_id == sale_id, ~paid)
    )

    booked = exists().where(Booking.payment_id == Payment.id)
    charged_unbooked = await session.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.sale_id == sale_id, Payment.status == "succeeded", ~booked)
    )

    has_booking = exists().where(Booking.seat_id == Seat.id)
    sold_unbooked = await session.scalar(
        select(func.count())
        .select_from(Seat)
        .where(Seat.sale_id == sale_id, Seat.status == "SOLD", ~has_booking)
    )
    return {
        "double_bookings": double_bookings,
        "bookings_without_successful_payment": without_payment,
        "charged_without_booking_or_refund": charged_unbooked,
        "sold_seats_without_booking": sold_unbooked,
    }
