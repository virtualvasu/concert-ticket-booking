from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.sale import create_sale, get_current_sale, seat_counts
from app.models import Booking, Seat
from app.schemas import SaleConfig

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _seat_at_rank(session, sale_id: int, rank: int) -> tuple[str, int, int]:
    seat = (
        await session.execute(
            select(Seat).where(Seat.sale_id == sale_id, Seat.rank == rank)
        )
    ).scalar_one()
    return (seat.section, seat.row, seat.number)


async def test_create_sale_builds_default_seat_map(session):
    sale = await create_sale(session, SaleConfig(), NOW)
    await session.commit()

    counts = await seat_counts(session, sale.id)
    assert counts == {
        "LOCKED": 500,
        "AVAILABLE": 0,
        "HELD": 0,
        "SOLD": 0,
        "total": 500,
    }
    assert await _seat_at_rank(session, sale.id, 1) == ("VIP", 1, 1)
    assert await _seat_at_rank(session, sale.id, 51) == ("Gold", 1, 1)
    assert await _seat_at_rank(session, sale.id, 500) == ("Silver", 12, 25)
    assert sale.status == "idle"


def test_sale_config_rejects_zero_tick():
    with pytest.raises(ValidationError):
        SaleConfig(tick_seconds=0)


async def test_bookings_unique_per_seat(session):
    sale = await create_sale(session, SaleConfig(), NOW)
    await session.commit()

    def booking() -> Booking:
        return Booking(
            sale_id=sale.id,
            seat_id=1,
            user_id="u1",
            hold_id=1,
            payment_id=1,
            created_at=NOW,
        )

    session.add(booking())
    await session.commit()
    session.add(booking())
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_get_current_sale_returns_latest(session):
    assert await get_current_sale(session) is None
    await create_sale(session, SaleConfig(label="first"), NOW)
    second = await create_sale(session, SaleConfig(label="second"), NOW)
    await session.commit()

    current = await get_current_sale(session)
    assert current.id == second.id
    assert current.label == "second"
