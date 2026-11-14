"""Leaky-bucket dispatcher: seats drip out each tick and go to the queue head."""

from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.queue import QueueStore
from app.domain.sale import end_if_sold_out, get_current_sale
from app.domain.seats import expire_holds, hold_best_available, release_seats
from app.models import Sale, Seat, User


async def dispatch_offers(
    session: AsyncSession, queue: QueueStore, sale: Sale, now: datetime
) -> int:
    """Offer AVAILABLE seats to waiting users strictly in queue order.

    Returns the number of offers made. A user popped while no seat is left goes
    back to their original place in the queue.
    """
    available = await session.scalar(
        select(func.count())
        .select_from(Seat)
        .where(Seat.sale_id == sale.id, Seat.status == "AVAILABLE")
    )
    if not available:
        return 0

    offers = 0
    while (head := await queue.pop_head()) is not None:
        user_id, seq = head
        state = await session.scalar(select(User.state).where(User.id == user_id))
        if state != "waiting":
            await session.commit()  # end the read txn; the stale entry is dropped
            continue
        if await hold_best_available(session, sale, user_id, now) is None:
            await queue.restore(user_id, seq)
            break
        offers += 1
    return offers


async def run_tick(session: AsyncSession, redis: Redis, now: datetime) -> dict:
    """One worker pass over the current running sale."""
    result = {"released": 0, "expired": 0, "offers": 0, "ended": False}
    sale = await get_current_sale(session)
    if sale is None or sale.status != "running":
        await session.commit()
        return result

    if sale.mode == "bucket":
        due = 0
        next_tick = sale.next_tick_at or now
        step = timedelta(seconds=sale.tick_seconds)
        while now >= next_tick:  # a stalled worker catches up, one batch per missed tick
            due += 1
            next_tick += step
        if due:
            released = await release_seats(session, sale.id, due * sale.seats_per_tick, now)
            result["released"] = len(released)
            sale.next_tick_at = next_tick
            await session.commit()

    result["expired"] = len(await expire_holds(session, sale.id, now))

    if sale.mode == "bucket":
        result["offers"] = await dispatch_offers(session, QueueStore(redis, sale.id), sale, now)

    result["ended"] = await end_if_sold_out(session, redis, sale, now)
    return result
