"""Seat state machine.

    LOCKED -> AVAILABLE -> HELD -> SOLD
                  ^          |
                  +----------+   (failed payment or hold expiry)

Every transition is a conditional ``UPDATE ... WHERE status = <expected>
RETURNING``, so two concurrent callers can never both win the same seat or hold.
Seat picking uses ``FOR UPDATE SKIP LOCKED`` so concurrent callers take
different seats instead of queueing on the same row.

Each public function runs and commits its own transaction.
"""

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, Hold, Sale, Seat, User
from app.trace import record


async def release_seats(
    session: AsyncSession, sale_id: int, n: int, now: datetime
) -> list[int]:
    """Move the ``n`` lowest-rank LOCKED seats to AVAILABLE; returns their ids."""
    picked = (
        await session.execute(
            select(Seat.id)
            .where(Seat.sale_id == sale_id, Seat.status == "LOCKED")
            .order_by(Seat.rank)
            .limit(n)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    if not picked:
        await session.commit()  # nothing changed; ends the txn, releases locks
        return []

    released = (
        await session.execute(
            update(Seat)
            .where(Seat.id.in_(picked), Seat.status == "LOCKED")
            .values(status="AVAILABLE", released_at=now)
            .returning(Seat.id, Seat.rank)
        )
    ).all()
    ids = [seat_id for seat_id, _ in sorted(released, key=lambda r: r.rank)]
    await record(session, sale_id, "seats_released", now, seat_ids=ids, count=len(ids))
    await session.commit()
    return ids


async def hold_best_available(
    session: AsyncSession, sale: Sale, user_id: str, now: datetime
) -> Hold | None:
    """Give ``user_id`` the best AVAILABLE seat as an active hold, or None."""
    seat = (
        await session.execute(
            select(Seat)
            .where(Seat.sale_id == sale.id, Seat.status == "AVAILABLE")
            .order_by(Seat.rank)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if seat is None:
        await session.commit()  # nothing changed; ends the txn, releases locks
        return None

    seat.status = "HELD"
    hold = Hold(
        sale_id=sale.id,
        seat_id=seat.id,
        user_id=user_id,
        created_at=now,
        expires_at=now + timedelta(seconds=sale.hold_seconds),
        status="active",
        attempts=0,
    )
    session.add(hold)
    await session.flush()
    await session.execute(update(User).where(User.id == user_id).values(state="offered"))
    await record(
        session,
        sale.id,
        "offer_made",
        now,
        user_id=user_id,
        seat_id=seat.id,
        hold_id=hold.id,
        expires_at=hold.expires_at.isoformat(),
    )
    await session.commit()
    return hold


async def confirm_hold(
    session: AsyncSession, hold_id: int, payment_id: int, now: datetime
) -> Booking | None:
    """Turn a live hold into a booking. None (and no change) if it is not live."""
    claimed = (
        await session.execute(
            update(Hold)
            .where(Hold.id == hold_id, Hold.status == "active", Hold.expires_at > now)
            .values(status="paid")
            .returning(Hold.sale_id, Hold.seat_id, Hold.user_id)
        )
    ).first()
    if claimed is None:
        await session.commit()  # nothing changed; ends the txn, releases locks
        return None
    sale_id, seat_id, user_id = claimed

    sold = (
        await session.execute(
            update(Seat)
            .where(Seat.id == seat_id, Seat.status == "HELD")
            .values(status="SOLD", sold_at=now)
            .returning(Seat.id)
        )
    ).first()
    if sold is None:  # an active hold always has a HELD seat; anything else is corruption
        await session.rollback()
        raise RuntimeError(f"hold {hold_id} is active but seat {seat_id} is not HELD")

    booking = Booking(
        sale_id=sale_id,
        seat_id=seat_id,
        user_id=user_id,
        hold_id=hold_id,
        payment_id=payment_id,
        created_at=now,
    )
    session.add(booking)
    await session.execute(update(User).where(User.id == user_id).values(state="booked"))
    await session.flush()
    await record(
        session,
        sale_id,
        "booking_confirmed",
        now,
        user_id=user_id,
        seat_id=seat_id,
        hold_id=hold_id,
        payment_id=payment_id,
        booking_id=booking.id,
    )
    await session.commit()
    return booking


async def fail_hold(
    session: AsyncSession, hold_id: int, now: datetime, reason: str
) -> bool:
    """Abandon an active hold: seat back to AVAILABLE, user to ``left``."""
    failed = (
        await session.execute(
            update(Hold)
            .where(Hold.id == hold_id, Hold.status == "active")
            .values(status="failed")
            .returning(Hold.sale_id, Hold.seat_id, Hold.user_id)
        )
    ).first()
    if failed is None:
        await session.commit()  # nothing changed; ends the txn, releases locks
        return False
    sale_id, seat_id, user_id = failed

    await session.execute(
        update(Seat).where(Seat.id == seat_id, Seat.status == "HELD").values(status="AVAILABLE")
    )
    await session.execute(
        update(User).where(User.id == user_id).values(state="left", reason=reason)
    )
    await record(
        session,
        sale_id,
        "hold_failed",
        now,
        user_id=user_id,
        seat_id=seat_id,
        hold_id=hold_id,
        reason=reason,
    )
    await session.commit()
    return True


async def expire_holds(session: AsyncSession, sale_id: int, now: datetime) -> list[int]:
    """Expire every active hold whose time is up; returns the expired hold ids."""
    expired = (
        await session.execute(
            update(Hold)
            .where(Hold.sale_id == sale_id, Hold.status == "active", Hold.expires_at <= now)
            .values(status="expired")
            .returning(Hold.id, Hold.seat_id, Hold.user_id)
        )
    ).all()
    if not expired:
        await session.commit()  # nothing changed; ends the txn, releases locks
        return []

    await session.execute(
        update(Seat)
        .where(Seat.id.in_([r.seat_id for r in expired]), Seat.status == "HELD")
        .values(status="AVAILABLE")
    )
    await session.execute(
        update(User)
        .where(User.id.in_([r.user_id for r in expired]))
        .values(state="left", reason="hold_expired")
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
        )
    await session.commit()
    return sorted(r.id for r in expired)
