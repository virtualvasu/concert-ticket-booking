from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.domain.queue import AlreadyQueued, QueueStore
from app.domain.sale import create_sale
from app.domain.users import join_sale
from app.models import Event, User
from app.schemas import SaleConfig

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _running_sale(session, mode: str = "bucket"):
    sale = await create_sale(session, SaleConfig(mode=mode), NOW)
    sale.status = "running"
    await session.commit()
    return sale


async def test_queue_order_and_position(redis):
    q = QueueStore(redis, 1)
    for uid in ("a", "b", "c"):
        await q.join(uid)

    assert [await q.position(u) for u in ("a", "b", "c")] == [1, 2, 3]
    assert await q.length() == 3
    assert await q.pop_head() == ("a", 1)
    assert await q.position("b") == 1
    assert await q.position("a") is None


async def test_join_twice_raises(redis):
    q = QueueStore(redis, 1)
    await q.join("a")

    with pytest.raises(AlreadyQueued):
        await q.join("a")
    assert await q.length() == 1


async def test_restore_keeps_original_place(redis):
    q = QueueStore(redis, 1)
    await q.join("a")
    await q.join("b")

    head, seq = await q.pop_head()
    assert await q.position("b") == 1
    await q.restore(head, seq)

    assert await q.position("a") == 1
    assert await q.position("b") == 2


async def test_pop_head_on_empty_queue_is_none(redis):
    assert await QueueStore(redis, 1).pop_head() is None


async def test_remove_and_drain(redis):
    q = QueueStore(redis, 1)
    for uid in ("a", "b", "c"):
        await q.join(uid)

    await q.remove("b")
    assert await q.drain() == ["a", "c"]
    assert await q.length() == 0


async def test_queues_are_per_sale(redis):
    await QueueStore(redis, 1).join("a")
    other = QueueStore(redis, 2)

    assert await other.length() == 0
    assert await other.join("a") == 1  # own sequence per sale


async def test_join_sale_bucket_queues_user(session, redis):
    sale = await _running_sale(session, "bucket")

    user = await join_sale(session, redis, sale, "Asha", NOW)

    assert user.state == "waiting"
    assert user.queue_seq == 1
    assert await QueueStore(redis, sale.id).length() == 1
    assert await QueueStore(redis, sale.id).position(user.id) == 1
    event = (await session.execute(select(Event).where(Event.type == "user_joined"))).scalar_one()
    assert event.user_id == user.id
    assert event.ts == NOW
    assert event.data == {"name": "Asha", "seq": 1}


async def test_join_sale_baseline_does_not_queue(session, redis):
    sale = await _running_sale(session, "baseline")

    user = await join_sale(session, redis, sale, "Asha", NOW)

    assert user.state == "browsing"
    assert user.queue_seq is None
    assert await QueueStore(redis, sale.id).length() == 0
    events = (await session.execute(select(Event).where(Event.type == "user_joined"))).scalars().all()
    assert len(events) == 1
    assert events[0].data["name"] == "Asha"


async def test_join_sale_requires_running(session, redis):
    sale = await create_sale(session, SaleConfig(), NOW)  # idle
    await session.commit()

    with pytest.raises(ValueError, match="sale not running"):
        await join_sale(session, redis, sale, "Asha", NOW)
    assert (await session.execute(select(User))).first() is None
    assert await QueueStore(redis, sale.id).length() == 0
