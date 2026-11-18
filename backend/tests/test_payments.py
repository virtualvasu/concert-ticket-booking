from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.domain.dispatcher import run_tick
from app.domain.sale import create_sale, seat_counts, start_sale
from app.domain.users import join_sale
from app.models import Booking, Payment, Seat, User
from app.payments.service import pay
from app.schemas import SaleConfig, SectionSpec
from fakes import FakeGateway

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, t: datetime = T0):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def gateway():
    return FakeGateway()


async def _offer(session, redis, **overrides):
    """A started 50-seat sale with one user holding seat rank 1."""
    cfg = dict(
        sections=[SectionSpec(name="GA", rows=2, seats_per_row=25, price=1000)],
        seats_per_tick=10,
        tick_seconds=10,
        hold_seconds=30,
    )
    cfg.update(overrides)
    sale = await create_sale(session, SaleConfig(**cfg), T0)
    await session.commit()
    sale = await start_sale(session, sale.id, T0)
    user = await join_sale(session, redis, sale, "alice", T0)
    await run_tick(session, redis, T0)
    from app.models import Hold

    hold = (
        await session.execute(select(Hold).where(Hold.user_id == user.id))
    ).scalar_one()
    return sale, user, hold


async def _user_state(session, user_id):
    u = (
        await session.execute(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    return u.state, u.reason


async def _seat_status(session, seat_id):
    return await session.scalar(
        select(Seat.status).where(Seat.id == seat_id).execution_options(populate_existing=True)
    )


async def test_success_confirms_booking(session, redis, gateway, clock):
    sale, user, hold = await _offer(session, redis)
    res = await pay(session, gateway, user.id, hold.id, "k1", "success", clock)
    assert res.status == "confirmed" and res.booking_id
    assert await _seat_status(session, hold.seat_id) == "SOLD"
    assert (await _user_state(session, user.id))[0] == "booked"
    pay_row = (await session.execute(select(Payment))).scalar_one()
    assert pay_row.status == "succeeded" and pay_row.amount == 1000


async def test_failure_allows_retries_then_releases(session, redis, gateway, clock):
    sale, user, hold = await _offer(session, redis)
    r1 = await pay(session, gateway, user.id, hold.id, "k1", "fail", clock)
    r2 = await pay(session, gateway, user.id, hold.id, "k2", "fail", clock)
    r3 = await pay(session, gateway, user.id, hold.id, "k3", "fail", clock)
    assert (r1.status, r1.attempts_left) == ("failed_retry", 2)
    assert (r2.status, r2.attempts_left) == ("failed_retry", 1)
    assert (r3.status, r3.attempts_left) == ("failed_final", 0)
    assert await _seat_status(session, hold.seat_id) == "AVAILABLE"
    assert await _user_state(session, user.id) == ("left", "payment_failed")
    assert await session.scalar(select(func.count()).select_from(Booking)) == 0


async def test_late_success_is_refunded(session, redis, gateway, clock):
    """Review Focus 1: charge succeeds right as the hold expires."""
    sale, user, hold = await _offer(session, redis)
    gateway.on_charge = lambda: clock.advance(31)  # hold_seconds is 30
    res = await pay(session, gateway, user.id, hold.id, "k1", "success", clock)
    assert res.status == "expired_refunded"
    pay_row = (await session.execute(select(Payment))).scalar_one()
    assert pay_row.status == "refunded"
    assert gateway.refunds == ["ref-1"]
    assert await session.scalar(select(func.count()).select_from(Booking)) == 0
    # the sweeper then frees the seat for the next user
    from app.domain.seats import expire_holds

    await expire_holds(session, sale.id, clock())
    assert await _seat_status(session, hold.seat_id) == "AVAILABLE"


async def test_pay_is_idempotent(session, redis, gateway, clock):
    """Review Focus 2: same key twice -> one charge, same result."""
    sale, user, hold = await _offer(session, redis)
    first = await pay(session, gateway, user.id, hold.id, "same", "success", clock)
    second = await pay(session, gateway, user.id, hold.id, "same", "success", clock)
    assert first == second and first.status == "confirmed"
    assert gateway.charges == 1
    assert await session.scalar(select(func.count()).select_from(Payment)) == 1


async def test_pay_someone_elses_hold_forbidden(session, redis, gateway, clock):
    sale, user, hold = await _offer(session, redis)
    with pytest.raises(PermissionError):
        await pay(session, gateway, "someone-else", hold.id, "k1", "success", clock)
    assert gateway.charges == 0
    assert await session.scalar(select(func.count()).select_from(Payment)) == 0


async def test_pay_after_expiry_is_expired_without_charging(session, redis, gateway, clock):
    sale, user, hold = await _offer(session, redis)
    clock.advance(31)
    res = await pay(session, gateway, user.id, hold.id, "k1", "success", clock)
    assert res.status == "expired"
    assert gateway.charges == 0


async def test_no_booking_without_payment_after_pending_retry(session, redis, gateway, clock):
    """A pending payment re-submitted with the same key is charged (deduplicated by the gateway) once more."""
    sale, user, hold = await _offer(session, redis)
    from app.models import Hold

    session.add(
        Payment(
            sale_id=sale.id, hold_id=hold.id, user_id=user.id, idempotency_key="net",
            amount=1000, status="pending", created_at=T0,
        )
    )
    await session.commit()
    res = await pay(session, gateway, user.id, hold.id, "net", "success", clock)
    assert res.status == "confirmed"
    assert await session.scalar(select(func.count()).select_from(Payment)) == 1
