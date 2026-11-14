from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale, Seat
from app.schemas import SaleConfig

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
