"""Waiting queue for a bucket-mode sale, kept in a Redis sorted set.

``queue:{sale_id}`` holds user ids scored by a join sequence taken from
``INCR queue_seq:{sale_id}``, so the lowest score is the head of the line and
a user restored with their original score gets their old place back.
"""

from redis.asyncio import Redis


class AlreadyQueued(Exception):
    """The user is already in this sale's queue."""


class AlreadyJoined(Exception):
    """The user has already joined this sale."""


class QueueStore:
    def __init__(self, redis: Redis, sale_id: int):
        self._redis = redis
        self._key = f"queue:{sale_id}"
        self._seq_key = f"queue_seq:{sale_id}"

    async def join(self, user_id: str) -> int:
        """Append the user to the queue and return their sequence number."""
        seq = await self._redis.incr(self._seq_key)
        # NX makes the check-and-add atomic; a rejected join only burns a seq.
        if not await self._redis.zadd(self._key, {user_id: seq}, nx=True):
            raise AlreadyQueued(user_id)
        return seq

    async def position(self, user_id: str) -> int | None:
        """1-based place in line, or None if not queued."""
        rank = await self._redis.zrank(self._key, user_id)
        return None if rank is None else rank + 1

    async def length(self) -> int:
        return await self._redis.zcard(self._key)

    async def pop_head(self) -> tuple[str, int] | None:
        """Remove and return ``(user_id, seq)`` of the first user, if any."""
        popped = await self._redis.zpopmin(self._key)
        if not popped:
            return None
        user_id, score = popped[0]
        return user_id, int(score)

    async def restore(self, user_id: str, seq: int) -> None:
        """Put a popped user back at their original place."""
        await self._redis.zadd(self._key, {user_id: seq})

    async def remove(self, user_id: str) -> None:
        await self._redis.zrem(self._key, user_id)

    async def drain(self) -> list[str]:
        """Atomically remove and return every queued user, in queue order."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zrange(self._key, 0, -1)
            pipe.delete(self._key)
            members, _ = await pipe.execute()
        return list(members)
