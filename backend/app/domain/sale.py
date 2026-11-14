from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.queue import QueueStore
from app.domain.seats import release_seats
from app.models import Hold, Sale, Seat, User
from app.schemas import SaleConfig
from app.trace import record

SEAT_STATES = ("LOCKED", "AVAILABLE", "HELD", "SOLD")


async def create_sale(session: AsyncSession, cfg: SaleConfig, now: datetime) -> Sale:
    """Create an idle sale with every seat LOCKED.

    Ranks run 1..total in section order, then row, then seat number, so rank 1
    is the best seat. Flushes but does not commit.
    """
    sale = Sale(
        mode=cfg.mode,
        status="idle",
        config=cfg.model_dump(mode="json"),
        seats_per_tick=cfg.seats_per_tick,
        tick_seconds=cfg.tick_seconds,
        hold_seconds=cfg.hold_seconds,
        max_payment_attempts=cfg.max_payment_attempts,
        tickets_per_user=cfg.tickets_per_user,
        label=cfg.label,
        created_at=now,
    )
    session.add(sale)
    await session.flush()

    rank = 0
    seats: list[Seat] = []
    for section in cfg.sections:
        for row in range(1, section.rows + 1):
            for number in range(1, section.seats_per_row + 1):
                rank += 1
                seats.append(
                    Seat(
                        sale_id=sale.id,
                        section=section.name,
                        row=row,
                        number=number,
                        price=section.price,
                        rank=rank,
                        status="LOCKED",
                    )
                )
    session.add_all(seats)
    await session.flush()
    return sale


async def get_current_sale(session: AsyncSession) -> Sale | None:
    """The most recently created sale, or None."""
    result = await session.execute(select(Sale).order_by(Sale.id.desc()).limit(1))
    return result.scalar_one_or_none()


async def seat_counts(session: AsyncSession, sale_id: int) -> dict[str, int]:
    """Seats per state plus the total, e.g. {"LOCKED": 500, ..., "total": 500}."""
    rows = await session.execute(
        select(Seat.status, func.count()).where(Seat.sale_id == sale_id).group_by(Seat.status)
    )
    counts = {state: 0 for state in SEAT_STATES}
    counts.update({status: n for status, n in rows.all()})
    counts["total"] = sum(counts[s] for s in SEAT_STATES)
    return counts


async def start_sale(session: AsyncSession, sale_id: int, now: datetime) -> Sale:
    """Open an idle sale. Bucket mode schedules its first release for ``now``;
    baseline mode makes every seat AVAILABLE immediately. Commits.
    """
    sale = await session.get(Sale, sale_id, populate_existing=True)
    if sale is None:
        raise ValueError("sale not found")
    if sale.status != "idle":
        raise ValueError("sale is not idle")
    other = (
        await session.execute(
            select(Sale.id).where(Sale.status == "running", Sale.id != sale_id).limit(1)
        )
    ).first()
    if other is not None:
        raise ValueError("another sale is already running")

    if sale.mode == "baseline":
        # Seats of an idle sale are unreachable, so doing this first is harmless
        # and a failure here leaves the sale cleanly idle.
        await release_seats(session, sale_id, await _seat_total(session, sale_id), now)

    sale.status = "running"
    sale.started_at = now
    sale.next_tick_at = now if sale.mode == "bucket" else None
    await record(session, sale_id, "sale_started", now, mode=sale.mode)
    await session.commit()
    return sale


async def stop_sale(session: AsyncSession, redis: Redis, sale_id: int, now: datetime) -> Sale:
    """Admin stop: expire active holds, reject everyone waiting, end the sale."""
    sale = await session.get(Sale, sale_id, populate_existing=True)
    if sale is None:
        raise ValueError("sale not found")
    if sale.status != "running":
        raise ValueError("sale not running")

    expired = (
        await session.execute(
            update(Hold)
            .where(Hold.sale_id == sale_id, Hold.status == "active")
            .values(status="expired")
            .returning(Hold.id, Hold.seat_id, Hold.user_id)
        )
    ).all()
    if expired:
        await session.execute(
            update(Seat)
            .where(Seat.id.in_([r.seat_id for r in expired]), Seat.status == "HELD")
            .values(status="AVAILABLE")
        )
        await session.execute(
            update(User)
            .where(User.id.in_([r.user_id for r in expired]))
            .values(state="left", reason="sale_stopped")
        )
        for r in expired:
            await record(
                session,
                sale_id,
                "hold_expired",
                now,
                user_id=r.user_id,
                seat_id=r.seat_id,
                hold_id=r.id,
                reason="sale_stopped",
            )
    await _end_sale(session, redis, sale, now, "stopped", "sale_stopped")
    return sale


async def end_if_sold_out(
    session: AsyncSession, redis: Redis, sale: Sale, now: datetime
) -> bool:
    """End the sale and reject whoever is still waiting once every seat is SOLD."""
    counts = await seat_counts(session, sale.id)
    if counts["total"] == 0 or counts["SOLD"] != counts["total"]:
        return False
    await _end_sale(session, redis, sale, now, "sold_out", "sold_out")
    return True


async def _seat_total(session: AsyncSession, sale_id: int) -> int:
    return (await seat_counts(session, sale_id))["total"]


async def _end_sale(
    session: AsyncSession,
    redis: Redis,
    sale: Sale,
    now: datetime,
    end_reason: str,
    user_reason: str,
) -> None:
    """Reject every waiting user, mark the sale ended, commit, then empty the queue.

    The queue is drained after the commit: if we crash in between, the leftover
    queue entries belong to an ended sale and nothing dispatches from it.
    """
    waiting = (
        await session.execute(
            update(User)
            .where(User.sale_id == sale.id, User.state == "waiting")
            .values(state="rejected", reason=user_reason)
            .returning(User.id, User.queue_seq)
        )
    ).all()
    for user_id, _seq in sorted(waiting, key=lambda r: (r.queue_seq is None, r.queue_seq)):
        await record(session, sale.id, "user_rejected", now, user_id=user_id, reason=user_reason)

    sale.status = "ended"
    sale.ended_at = now
    sale.end_reason = end_reason
    sale.next_tick_at = None
    await record(session, sale.id, "sale_ended", now, end_reason=end_reason)
    await session.commit()
    await QueueStore(redis, sale.id).drain()
