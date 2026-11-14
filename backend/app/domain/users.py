import uuid
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.queue import QueueStore
from app.models import Sale, User
from app.trace import record


async def join_sale(
    session: AsyncSession, redis: Redis, sale: Sale, name: str, now: datetime
) -> User:
    """Register a new user for a running sale and commit.

    Bucket mode puts the user in the Redis queue (state ``waiting``); baseline
    mode has no queue, so the user just starts ``browsing``.
    """
    if sale.status != "running":
        raise ValueError("sale not running")

    user = User(
        id=str(uuid.uuid4()),
        sale_id=sale.id,
        name=name,
        joined_at=now,
    )
    queue = QueueStore(redis, sale.id)
    queued = False
    try:
        if sale.mode == "bucket":
            user.queue_seq = await queue.join(user.id)
            queued = True
            user.state = "waiting"
        else:
            user.state = "browsing"
        session.add(user)
        await record(
            session, sale.id, "user_joined", now, user_id=user.id, name=name, seq=user.queue_seq
        )
        await session.commit()
    except BaseException:
        # Never leave a queue entry that has no committed user behind it.
        await session.rollback()
        if queued:
            await queue.remove(user.id)
        raise
    return user
