from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.dispatcher import dispatch_offers, run_tick
from app.domain.queue import QueueStore
from app.domain.sale import create_sale, seat_counts, start_sale, stop_sale
from app.domain.seats import confirm_hold
from app.domain.users import join_sale
from app.models import Event, Hold, Seat, User
from app.schemas import SaleConfig, SectionSpec

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _s(n: float) -> timedelta:
    return timedelta(seconds=n)


async def _started(session, **overrides):
    """A started 50-seat sale (one GA section of 2x25 at 1000)."""
    cfg = dict(
        sections=[SectionSpec(name="GA", rows=2, seats_per_row=25, price=1000)],
        seats_per_tick=10,
        tick_seconds=10,
        hold_seconds=30,
    )
    cfg.update(overrides)
    sale = await create_sale(session, SaleConfig(**cfg), T0)
    await session.commit()
    return await start_sale(session, sale.id, T0)


async def _join(session, redis, sale, n: int) -> list[str]:
    """Join users u0..u{n-1} in order; returns their ids."""
    ids = []
    for i in range(n):
        user = await join_sale(session, redis, sale, f"u{i}", T0)
        ids.append(user.id)
    return ids


async def _state(session, user_id: str) -> tuple[str, str | None]:
    user = (
        await session.execute(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    return user.state, user.reason


async def _active_holds(session, sale_id: int) -> list[Hold]:
    return list(
        (
            await session.execute(
                select(Hold)
                .where(Hold.sale_id == sale_id, Hold.status == "active")
                .execution_options(populate_existing=True)
                .order_by(Hold.id)
            )
        ).scalars()
    )


async def test_first_tick_releases_immediately(session, redis):
    await _started(session)

    assert (await run_tick(session, redis, T0))["released"] == 10
    assert (await run_tick(session, redis, T0 + _s(5)))["released"] == 0
    assert (await run_tick(session, redis, T0 + _s(10)))["released"] == 10


async def test_unused_seats_carry_over(session, redis):
    sale = await _started(session)

    await run_tick(session, redis, T0)
    await run_tick(session, redis, T0 + _s(10))

    assert (await seat_counts(session, sale.id))["AVAILABLE"] == 20


async def test_stalled_worker_catches_up_missed_ticks_only(session, redis):
    sale = await _started(session)
    await run_tick(session, redis, T0)

    result = await run_tick(session, redis, T0 + _s(35))  # ticks due at +10, +20, +30

    assert result["released"] == 30
    assert (await seat_counts(session, sale.id))["AVAILABLE"] == 40
    assert (await run_tick(session, redis, T0 + _s(39)))["released"] == 0
    assert (await run_tick(session, redis, T0 + _s(40)))["released"] == 10


async def test_offers_follow_queue_order(session, redis):
    sale = await _started(session)
    ids = await _join(session, redis, sale, 15)

    result = await run_tick(session, redis, T0)

    assert result["offers"] == 10
    holds = await _active_holds(session, sale.id)
    assert [h.user_id for h in holds] == ids[:10]
    ranks = [(await session.get(Seat, h.seat_id)).rank for h in holds]
    assert ranks == list(range(1, 11))
    assert await QueueStore(redis, sale.id).position(ids[10]) == 1


async def test_expired_hold_goes_to_next_user(session, redis):
    sale = await _started(session, tick_seconds=100)
    ids = await _join(session, redis, sale, 11)
    assert (await run_tick(session, redis, T0))["offers"] == 10

    result = await run_tick(session, redis, T0 + _s(31))

    assert result["expired"] == 10
    assert result["offers"] == 1
    (hold,) = await _active_holds(session, sale.id)
    assert hold.user_id == ids[10]
    assert (await session.get(Seat, hold.seat_id)).rank == 1
    assert (await seat_counts(session, sale.id))["AVAILABLE"] == 9
    for uid in ids[:10]:
        assert await _state(session, uid) == ("left", "hold_expired")


async def test_sold_out_rejects_waiting(session, redis):
    sale = await _started(session)
    await _join(session, redis, sale, 60)

    for i in range(5):
        now = T0 + _s(10 * i)
        assert (await run_tick(session, redis, now))["ended"] is False
        for hold in await _active_holds(session, sale.id):
            assert await confirm_hold(session, hold.id, payment_id=hold.id, now=now)

    result = await run_tick(session, redis, T0 + _s(50))

    assert result["ended"] is True
    await session.refresh(sale)
    assert sale.status == "ended"
    assert sale.end_reason == "sold_out"
    rejected = (
        (await session.execute(select(User).where(User.state == "rejected"))).scalars().all()
    )
    assert len(rejected) == 10
    assert {u.reason for u in rejected} == {"sold_out"}
    assert await QueueStore(redis, sale.id).length() == 0
    events = (
        (await session.execute(select(Event).where(Event.type == "user_rejected"))).scalars().all()
    )
    assert len(events) == 10


async def test_baseline_releases_all_and_never_dispatches(session, redis):
    sale = await _started(session, mode="baseline")

    assert (await seat_counts(session, sale.id))["AVAILABLE"] == 50
    await _join(session, redis, sale, 3)
    result = await run_tick(session, redis, T0 + _s(1))

    assert result == {"released": 0, "expired": 0, "offers": 0, "ended": False}


async def test_stop_sale_rejects_waiting_and_expires_holds(session, redis):
    sale = await _started(session)
    ids = await _join(session, redis, sale, 15)
    await run_tick(session, redis, T0)  # u0..u9 hold seats, u10..u14 wait

    stopped = await stop_sale(session, redis, sale.id, T0 + _s(5))

    assert stopped.status == "ended"
    assert stopped.end_reason == "stopped"
    assert await _active_holds(session, sale.id) == []
    counts = await seat_counts(session, sale.id)
    assert counts["SOLD"] == 0
    assert counts["HELD"] == 0
    for uid in ids[:10]:
        assert await _state(session, uid) == ("left", "sale_stopped")
    for uid in ids[10:]:
        assert await _state(session, uid) == ("rejected", "sale_stopped")
    assert await QueueStore(redis, sale.id).length() == 0
    types = set((await session.execute(select(Event.type))).scalars())
    assert {"sale_ended", "user_rejected"} <= types


async def test_start_second_running_sale_fails(session, redis):
    await _started(session)
    other = await create_sale(session, SaleConfig(), T0)
    await session.commit()

    with pytest.raises(ValueError):
        await start_sale(session, other.id, T0)
    await session.refresh(other)
    assert other.status == "idle"


async def test_start_sale_sets_running_state(session, redis):
    sale = await _started(session)

    assert sale.status == "running"
    assert sale.started_at == T0
    assert sale.next_tick_at == T0
    event = (await session.execute(select(Event).where(Event.type == "sale_started"))).scalar_one()
    assert event.ts == T0


async def test_dispatch_restores_head_when_no_seats(session, redis):
    sale = await _started(session)
    ids = await _join(session, redis, sale, 2)
    queue = QueueStore(redis, sale.id)

    assert await dispatch_offers(session, queue, sale, T0) == 0  # nothing released yet

    assert await queue.position(ids[0]) == 1
    assert await queue.position(ids[1]) == 2


async def test_dispatch_skips_users_who_are_not_waiting(session, redis):
    sale = await _started(session)
    ids = await _join(session, redis, sale, 2)
    user = await session.get(User, ids[0])
    user.state = "left"
    await session.commit()
    await run_tick(session, redis, T0)

    holds = await _active_holds(session, sale.id)

    assert [h.user_id for h in holds] == [ids[1]]
