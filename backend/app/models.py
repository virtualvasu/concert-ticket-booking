from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Cross-table references (sale_id, seat_id, user_id, ...) are plain columns, not
# foreign keys: the domain layer owns referential consistency and the
# consistency checks in the admin API verify it.


def _ts() -> DateTime:
    return DateTime(timezone=True)


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16))  # bucket | baseline
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|running|ended
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seats_per_tick: Mapped[int] = mapped_column(Integer)
    tick_seconds: Mapped[float] = mapped_column(Float)
    hold_seconds: Mapped[float] = mapped_column(Float)
    max_payment_attempts: Mapped[int] = mapped_column(Integer)
    tickets_per_user: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(_ts())
    started_at: Mapped[datetime | None] = mapped_column(_ts(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(_ts(), nullable=True)
    next_tick_at: Mapped[datetime | None] = mapped_column(_ts(), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # sold_out|stopped


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (Index("ix_seats_sale_status_rank", "sale_id", "status", "rank"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(Integer, index=True)
    section: Mapped[str] = mapped_column(String(50))
    row: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    rank: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="LOCKED")
    released_at: Mapped[datetime | None] = mapped_column(_ts(), nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(_ts(), nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    sale_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    joined_at: Mapped[datetime] = mapped_column(_ts())
    queue_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # waiting|offered|booked|left|rejected|browsing
    state: Mapped[str] = mapped_column(String(16))
    # hold_expired|payment_failed|sold_out|sale_stopped
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Hold(Base):
    __tablename__ = "holds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(Integer, index=True)
    seat_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(_ts())
    expires_at: Mapped[datetime] = mapped_column(_ts())
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paid|expired|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(Integer, index=True)
    hold_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    amount: Mapped[int] = mapped_column(Integer)
    # pending|succeeded|failed|refunded
    status: Mapped[str] = mapped_column(String(16), default="pending")
    gateway_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_ts())
    finished_at: Mapped[datetime | None] = mapped_column(_ts(), nullable=True)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("sale_id", "seat_id", name="uq_bookings_sale_seat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(Integer)
    seat_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(36))
    hold_id: Mapped[int] = mapped_column(Integer)
    payment_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_ts())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_sale_ts", "sale_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(_ts())
    type: Mapped[str] = mapped_column(String(50))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
