"""Paying for a hold.

Rules enforced here (see the plan's Global Constraints):
- a booking exists only after a successful charge *and* a live hold at confirm time;
- a charge that succeeds after the hold is gone is refunded, never booked;
- the idempotency key makes a repeated request return the stored outcome
  instead of charging again.
"""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.seats import confirm_hold, fail_hold
from app.models import Booking, Hold, Payment, Sale, Seat
from app.payments.gateway import GatewayClient
from app.schemas import PayResult
from app.trace import record


async def pay(
    session: AsyncSession,
    gateway: GatewayClient,
    user_id: str,
    hold_id: int,
    idempotency_key: str,
    force: str | None,
    now_fn: Callable[[], datetime],
) -> PayResult:
    payment = await _by_key(session, idempotency_key)
    if payment is not None:
        # 1. Known key: never charge twice for a finished payment.
        if payment.user_id != user_id or payment.hold_id != hold_id:
            raise PermissionError("idempotency key belongs to another payment")
        if payment.status != "pending":
            return await _outcome(session, payment.id)
        # Still pending (client retried after a network error): the gateway
        # de-duplicates on the key, so charging again is safe.
    else:
        # 2 + 3. Validate the hold, then record the attempt.
        hold = await session.get(Hold, hold_id, populate_existing=True)
        if hold is None or hold.user_id != user_id:
            await session.rollback()
            raise PermissionError("not your hold")
        sale = await session.get(Sale, hold.sale_id)
        now = now_fn()
        if hold.status != "active" or hold.expires_at <= now:
            await session.rollback()
            return PayResult(status="expired")
        if hold.attempts >= sale.max_payment_attempts:
            await session.rollback()
            return PayResult(status="failed_final")

        price = await session.scalar(select(Seat.price).where(Seat.id == hold.seat_id))
        payment = Payment(
            sale_id=hold.sale_id,
            hold_id=hold.id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            amount=price,
            status="pending",
            created_at=now,
        )
        session.add(payment)
        try:
            await session.flush()
        except IntegrityError:
            # A concurrent request with the same key won the insert.
            await session.rollback()
            other = await _by_key(session, idempotency_key)
            return await _outcome(session, other.id)

        attempt = await session.scalar(
            update(Hold)
            .where(
                Hold.id == hold_id,
                Hold.status == "active",
                Hold.attempts < sale.max_payment_attempts,
            )
            .values(attempts=Hold.attempts + 1)
            .returning(Hold.attempts)
        )
        if attempt is None:  # the hold changed under us
            await session.rollback()
            return PayResult(status="expired")
        await record(
            session,
            hold.sale_id,
            "payment_attempt",
            now,
            user_id=user_id,
            seat_id=hold.seat_id,
            hold_id=hold.id,
            payment_id=payment.id,
            attempt=attempt,
        )
        await session.commit()

    # 4 + 5. Charge, then settle.
    payment_id, amount, sale_id = payment.id, payment.amount, payment.sale_id
    charge = await gateway.charge(idempotency_key, amount, force)

    won = await _settle(session, payment_id, charge.status, charge.ref, now_fn())
    if won:
        if charge.status == "failed":
            await _after_failure(session, payment_id, now_fn)
        else:
            await _after_success(session, gateway, payment_id, charge.ref, now_fn)
    return await _outcome(session, payment_id)


async def _by_key(session: AsyncSession, key: str) -> Payment | None:
    return (
        await session.execute(
            select(Payment)
            .where(Payment.idempotency_key == key)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _settle(
    session: AsyncSession, payment_id: int, status: str, ref: str, now: datetime
) -> bool:
    """pending -> succeeded|failed, exactly once. False if someone else settled it."""
    row = (
        await session.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == "pending")
            .values(status=status, gateway_ref=ref, finished_at=now)
            .returning(Payment.sale_id, Payment.user_id, Payment.hold_id)
        )
    ).first()
    if row is None:
        await session.commit()
        return False
    await record(
        session,
        row.sale_id,
        f"payment_{status}",
        now,
        user_id=row.user_id,
        hold_id=row.hold_id,
        payment_id=payment_id,
        ref=ref,
    )
    await session.commit()
    return True


async def _after_failure(
    session: AsyncSession, payment_id: int, now_fn: Callable[[], datetime]
) -> None:
    payment = await session.get(Payment, payment_id, populate_existing=True)
    hold = await session.get(Hold, payment.hold_id, populate_existing=True)
    sale = await session.get(Sale, hold.sale_id)
    if hold.attempts >= sale.max_payment_attempts:
        await fail_hold(session, hold.id, now_fn(), "payment_failed")


async def _after_success(
    session: AsyncSession,
    gateway: GatewayClient,
    payment_id: int,
    ref: str,
    now_fn: Callable[[], datetime],
) -> None:
    payment = await session.get(Payment, payment_id, populate_existing=True)
    booking = await confirm_hold(session, payment.hold_id, payment_id, now_fn())
    if booking is not None:
        return
    # The money was taken but the hold is gone: give it back, never book.
    await gateway.refund(ref)
    now = now_fn()
    await session.execute(
        update(Payment).where(Payment.id == payment_id).values(status="refunded", finished_at=now)
    )
    await record(
        session,
        payment.sale_id,
        "refund_issued",
        now,
        user_id=payment.user_id,
        hold_id=payment.hold_id,
        payment_id=payment_id,
        ref=ref,
        amount=payment.amount,
    )
    await session.commit()


async def _outcome(session: AsyncSession, payment_id: int) -> PayResult:
    """The result implied by the stored state of a payment."""
    payment = await session.get(Payment, payment_id, populate_existing=True)
    hold = await session.get(Hold, payment.hold_id, populate_existing=True)
    sale = await session.get(Sale, hold.sale_id)
    left = max(0, sale.max_payment_attempts - hold.attempts)
    if payment.status == "refunded":
        return PayResult(status="expired_refunded")
    if payment.status == "succeeded":
        booking_id = await session.scalar(
            select(Booking.id).where(Booking.payment_id == payment_id)
        )
        if booking_id is not None:
            return PayResult(status="confirmed", booking_id=booking_id, attempts_left=left)
        return PayResult(status="expired")
    # failed (or still pending): decided by what happened to the hold
    if hold.status == "failed":
        return PayResult(status="failed_final")
    if hold.status == "active":
        return PayResult(status="failed_retry", attempts_left=left)
    return PayResult(status="expired")
