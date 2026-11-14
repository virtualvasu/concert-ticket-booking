import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.sale import create_sale, seat_counts
from app.domain.seats import (
    confirm_hold,
    expire_holds,
    fail_hold,
    hold_best_available,
    release_seats,
)
from app.models import Booking, Event, Hold, Seat, User
from app.schemas import SaleConfig

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _sale(session, **cfg):
    sale = await create_sale(session, SaleConfig(**cfg), NOW)
    await session.commit()
    return sale


async def _user(session, sale_id: int, uid: str, state: str = "waiting") -> str:
    session.add(User(id=uid, sale_id=sale_id, name=uid, joined_at=NOW, state=state))
    await session.commit()
    return uid


async def _seat(session, seat_id: int) -> Seat:
    await session.refresh(await session.get(Seat, seat_id))
    return await session.get(Seat, seat_id)


async def test_release_takes_lowest_rank_first(session):
    sale = await _sale(session)
    ids = await release_seats(session, sale.id, 10, NOW)

    ranks = sorted(
        (await session.execute(select(Seat.rank).where(Seat.id.in_(ids)))).scalars()
    )
    assert ranks == list(range(1, 11))
    counts = await seat_counts(session, sale.id)
    assert counts["AVAILABLE"] == 10
    assert counts["LOCKED"] == 490
    event = (
        await session.execute(select(Event).where(Event.type == "seats_released"))
    ).scalar_one()
    assert event.data["count"] == 10
    assert sorted(event.data["seat_ids"]) == sorted(ids)


async def test_release_more_than_locked_releases_remaining(session):
    sale = await _sale(session)
    await release_seats(session, sale.id, 495, NOW)
    ids = await release_seats(session, sale.id, 10, NOW)
    assert len(ids) == 5


async def test_hold_best_available_takes_rank_order(session):
    sale = await _sale(session)
    await release_seats(session, sale.id, 3, NOW)
    holds = []
    for i in range(4):
        uid = await _user(session, sale.id, f"u{i}")
        holds.append(await hold_best_available(session, sale, uid, NOW))

    assert holds[3] is None
    ranks = []
    for h in holds[:3]:
        seat = await session.get(Seat, h.seat_id)
        assert seat.status == "HELD"
        ranks.append(seat.rank)
    assert ranks == [1, 2, 3]
    assert holds[0].expires_at == NOW + timedelta(seconds=60)
    user = await session.get(User, "u0")
    await session.refresh(user)
    assert user.state == "offered"
    # a user who got no seat is untouched
    loser = await session.get(User, "u3")
    await session.refresh(loser)
    assert loser.state == "waiting"
    offers = (
        await session.execute(select(func.count()).where(Event.type == "offer_made"))
    ).scalar_one()
    assert offers == 3


async def test_confirm_after_expiry_fails(session):
    sale = await _sale(session)
    await release_seats(session, sale.id, 1, NOW)
    uid = await _user(session, sale.id, "u1")
    hold = await hold_best_available(session, sale, uid, NOW)

    booking = await confirm_hold(session, hold.id, 1, NOW + timedelta(seconds=61))

    assert booking is None
    seat = await _seat(session, hold.seat_id)
    assert seat.status == "HELD"
    assert (await session.execute(select(func.count()).select_from(Booking))).scalar_one() == 0


async def test_confirm_twice_books_once(session):
    sale = await _sale(session)
    await release_seats(session, sale.id, 1, NOW)
    uid = await _user(session, sale.id, "u1")
    hold = await hold_best_available(session, sale, uid, NOW)

    first = await confirm_hold(session, hold.id, 1, NOW + timedelta(seconds=5))
    second = await confirm_hold(session, hold.id, 1, NOW + timedelta(seconds=6))

    assert first is not None
    assert second is None
    assert (await session.execute(select(func.count()).select_from(Booking))).scalar_one() == 1
    seat = await _seat(session, hold.seat_id)
    assert seat.status == "SOLD"
    assert seat.sold_at == NOW + timedelta(seconds=5)
    user = await session.get(User, uid)
    await session.refresh(user)
    assert user.state == "booked"


async def test_fail_hold_returns_seat(session):
    sale = await _sale(session)
    await release_seats(session, sale.id, 1, NOW)
    uid = await _user(session, sale.id, "u1")
    hold = await hold_best_available(session, sale, uid, NOW)

    assert await fail_hold(session, hold.id, NOW, "payment_failed") is True
    assert await fail_hold(session, hold.id, NOW, "payment_failed") is False

    seat = await _seat(session, hold.seat_id)
    assert seat.status == "AVAILABLE"
    user = await session.get(User, uid)
    await session.refresh(user)
    assert user.state == "left"
    assert user.reason == "payment_failed"
    refreshed = await session.get(Hold, hold.id)
    await session.refresh(refreshed)
    assert refreshed.status == "failed"


async def test_expire_holds(session):
    sale = await _sale(session)
    await release_seats(session, sale.id, 2, NOW)
    u1 = await _user(session, sale.id, "u1")
    u2 = await _user(session, sale.id, "u2")
    old = await hold_best_available(session, sale, u1, NOW)
    fresh = await hold_best_available(session, sale, u2, NOW + timedelta(seconds=30))

    expired = await expire_holds(session, sale.id, NOW + timedelta(seconds=61))

    assert expired == [old.id]
    assert (await _seat(session, old.seat_id)).status == "AVAILABLE"
    assert (await _seat(session, fresh.seat_id)).status == "HELD"
    user = await session.get(User, u1)
    await session.refresh(user)
    assert (user.state, user.reason) == ("left", "hold_expired")
    other = await session.get(User, u2)
    await session.refresh(other)
    assert other.state == "offered"


async def test_concurrent_holds_never_share_a_seat(session, test_engine):
    sale = await _sale(session)
    await release_seats(session, sale.id, 10, NOW)
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as s:
        for i in range(200):
            s.add(User(id=f"c{i}", sale_id=sale.id, name="c", joined_at=NOW, state="waiting"))
        await s.commit()

    async def attempt(i: int):
        async with maker() as s:
            return await hold_best_available(s, sale, f"c{i}", NOW)

    results = await asyncio.gather(*(attempt(i) for i in range(200)))
    holds = [h for h in results if h is not None]
    assert len(holds) == 10
    assert len({h.seat_id for h in holds}) == 10


async def test_concurrent_confirms_book_once(session, test_engine):
    sale = await _sale(session)
    await release_seats(session, sale.id, 1, NOW)
    uid = await _user(session, sale.id, "u1")
    hold = await hold_best_available(session, sale, uid, NOW)
    maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async def attempt():
        async with maker() as s:
            return await confirm_hold(s, hold.id, 1, NOW + timedelta(seconds=1))

    results = await asyncio.gather(*(attempt() for _ in range(20)))
    assert sum(1 for r in results if r is not None) == 1
    assert (await session.execute(select(func.count()).select_from(Booking))).scalar_one() == 1
