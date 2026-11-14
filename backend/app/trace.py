from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event


async def record(
    session: AsyncSession,
    sale_id: int,
    type: str,
    ts: datetime,
    user_id: str | None = None,
    seat_id: int | None = None,
    **data: Any,
) -> None:
    """Append an event to the trace. Adds to the session; the caller commits."""
    session.add(
        Event(
            sale_id=sale_id,
            ts=ts,
            type=type,
            user_id=user_id,
            seat_id=seat_id,
            data=data,
        )
    )
