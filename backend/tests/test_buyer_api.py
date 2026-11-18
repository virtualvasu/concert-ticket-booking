from datetime import datetime, timezone

from sqlalchemy import select

from app.api.deps import get_gateway
from app.domain.dispatcher import run_tick
from app.domain.sale import create_sale, start_sale
from app.main import app
from app.models import Event
from app.schemas import SaleConfig, SectionSpec
from fakes import FakeGateway


def _now():
    return datetime.now(timezone.utc)


async def _running_sale(session, mode="bucket", seats=20, **overrides):
    cfg = dict(
        mode=mode,
        sections=[SectionSpec(name="GA", rows=1, seats_per_row=seats, price=1000)],
        seats_per_tick=10,
        tick_seconds=1000,
        hold_seconds=30,
    )
    cfg.update(overrides)
    sale = await create_sale(session, SaleConfig(**cfg), _now())
    await session.commit()
    return await start_sale(session, sale.id, _now())


async def _join(client, name="alice", user_id=None):
    headers = {"X-User-Id": user_id} if user_id else {}
    return await client.post("/api/queue/join", json={"name": name}, headers=headers)


async def test_join_and_me_shows_position(client, session, redis):
    await _running_sale(session)
    a = (await _join(client, "a")).json()
    b = (await _join(client, "b")).json()
    assert (a["position"], b["position"]) == (1, 2)
    me = (await client.get("/api/me", headers={"X-User-Id": b["user_id"]})).json()
    assert me["state"] == "waiting" and me["position"] == 2 and me["ahead"] == 1
    assert me["offer"] is None and me["booking"] is None
    sale = (await client.get("/api/sale")).json()
    assert sale["queue_length"] == 2 and sale["status"] == "running"


async def test_sale_404_without_sale(client):
    assert (await client.get("/api/sale")).status_code == 404


async def test_unknown_user_401(client, session):
    await _running_sale(session)
    assert (await client.get("/api/me")).status_code == 401
    assert (await client.get("/api/me", headers={"X-User-Id": "nope"})).status_code == 401


async def test_join_when_not_running_conflicts(client, session):
    sale = await create_sale(session, SaleConfig(), _now())
    await session.commit()
    assert (await _join(client)).status_code == 409


async def test_join_twice_conflicts(client, session, redis):
    """Review Focus 4."""
    await _running_sale(session)
    uid = (await _join(client)).json()["user_id"]
    again = await _join(client, user_id=uid)
    assert again.status_code == 409
    assert (await client.get("/api/sale")).json()["queue_length"] == 1


async def test_me_restores_offer(client, session, redis):
    """Review Focus 3: the live offer survives a refresh."""
    await _running_sale(session)
    uid = (await _join(client)).json()["user_id"]
    await run_tick(session, redis, _now())
    h = {"X-User-Id": uid}
    first = (await client.get("/api/me", headers=h)).json()
    second = (await client.get("/api/me", headers=h)).json()
    assert first["state"] == "offered"
    assert first["offer"]["expires_at"] and first["offer"]["attempts_left"] == 3
    assert first["offer"]["seat"] == {"section": "GA", "row": 1, "number": 1, "price": 1000}
    assert second["offer"] == first["offer"]


async def test_pay_endpoint_confirms(client, session, redis):
    await _running_sale(session)
    gw = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: gw
    uid = (await _join(client)).json()["user_id"]
    await run_tick(session, redis, _now())
    h = {"X-User-Id": uid}
    offer = (await client.get("/api/me", headers=h)).json()["offer"]
    r = await client.post(
        "/api/pay",
        json={"hold_id": offer["hold_id"], "idempotency_key": "k1", "force": "success"},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    me = (await client.get("/api/me", headers=h)).json()
    assert me["state"] == "booked" and me["booking"]["seat"]["number"] == 1


async def test_pay_someone_elses_hold_is_403(client, session, redis):
    await _running_sale(session)
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    a = (await _join(client, "a")).json()["user_id"]
    b = (await _join(client, "b")).json()["user_id"]
    await run_tick(session, redis, _now())
    offer = (await client.get("/api/me", headers={"X-User-Id": a})).json()["offer"]
    r = await client.post(
        "/api/pay",
        json={"hold_id": offer["hold_id"], "idempotency_key": "k1", "force": "success"},
        headers={"X-User-Id": b},
    )
    assert r.status_code == 403


async def test_baseline_grab_until_no_seats(client, session, redis):
    await _running_sale(session, mode="baseline", seats=3)
    ids = [(await _join(client, f"u{i}")).json()["user_id"] for i in range(4)]
    results = [await client.post("/api/baseline/grab", headers={"X-User-Id": u}) for u in ids]
    assert [r.status_code for r in results] == [200, 200, 200, 409]
    assert results[3].json() == {"detail": "no_seats"}
    assert {r.json()["seat"]["number"] for r in results[:3]} == {1, 2, 3}
    failed = (
        await session.execute(select(Event).where(Event.type == "grab_failed"))
    ).scalars().all()
    assert len(failed) == 1
    # a user who already holds a seat can't grab a second one
    again = await client.post("/api/baseline/grab", headers={"X-User-Id": ids[0]})
    assert again.status_code == 409


async def test_grab_requires_baseline_mode(client, session, redis):
    await _running_sale(session)
    uid = (await _join(client)).json()["user_id"]
    r = await client.post("/api/baseline/grab", headers={"X-User-Id": uid})
    assert r.status_code == 409


async def test_request_counter_increments(client, session, redis):
    await _running_sale(session)
    await client.get("/api/sale")
    await client.get("/api/sale")
    keys = await redis.keys("rps:*")
    assert keys
    total = 0
    for k in keys:
        total += int(await redis.get(k))
    assert total == 2
    assert 0 < await redis.ttl(keys[0]) <= 300
