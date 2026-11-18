import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.api.deps import get_gateway
from app.domain.dispatcher import run_tick
from app.main import app
from app.models import Payment
from fakes import FakeGateway

ADMIN = {"X-Admin-Token": "admin"}
SMALL = {
    "sections": [{"name": "GA", "rows": 1, "seats_per_row": 20, "price": 1000}],
    "seats_per_tick": 10,
    "tick_seconds": 1000,
    "hold_seconds": 30,
}


def _now():
    return datetime.now(timezone.utc)


async def _create_and_start(client, **overrides):
    r = await client.post("/api/admin/sales", json={**SMALL, **overrides}, headers=ADMIN)
    assert r.status_code == 200, r.text
    sale_id = r.json()["sale_id"]
    r = await client.post(f"/api/admin/sales/{sale_id}/start", headers=ADMIN)
    assert r.status_code == 200, r.text
    return sale_id


async def _join(client, name):
    return (await client.post("/api/queue/join", json={"name": name})).json()["user_id"]


async def _pay_first_offer(client, uid, key="k1"):
    h = {"X-User-Id": uid}
    offer = (await client.get("/api/me", headers=h)).json()["offer"]
    r = await client.post(
        "/api/pay",
        json={"hold_id": offer["hold_id"], "idempotency_key": key, "force": "success"},
        headers=h,
    )
    assert r.json()["status"] == "confirmed"


async def test_admin_requires_token(client):
    for method, path in [
        ("get", "/api/admin/stats"),
        ("post", "/api/admin/sales"),
        ("get", "/api/admin/sales/1/trace"),
        ("get", "/api/admin/sales/1/consistency"),
        ("get", "/api/admin/gateway"),
    ]:
        no_token = await getattr(client, method)(path)
        wrong = await getattr(client, method)(path, headers={"X-Admin-Token": "nope"})
        assert no_token.status_code == 401 and wrong.status_code == 401, path


async def test_create_start_stop_flow(client, session, redis):
    r = await client.post("/api/admin/sales", json=SMALL, headers=ADMIN)
    sale_id = r.json()["sale_id"]
    assert r.json()["status"] == "idle" and r.json()["seats"]["total"] == 20
    started = (await client.post(f"/api/admin/sales/{sale_id}/start", headers=ADMIN)).json()
    assert started["status"] == "running"
    # a second start (or another running sale) conflicts
    assert (await client.post(f"/api/admin/sales/{sale_id}/start", headers=ADMIN)).status_code == 409

    uid = await _join(client, "a")
    await run_tick(session, redis, _now())
    other = (await client.post("/api/admin/sales", json=SMALL, headers=ADMIN)).json()["sale_id"]
    assert (await client.post(f"/api/admin/sales/{other}/start", headers=ADMIN)).status_code == 409
    stopped = (await client.post(f"/api/admin/sales/{sale_id}/stop", headers=ADMIN)).json()
    assert stopped["status"] == "ended" and stopped["end_reason"] == "stopped"
    me = (await client.get("/api/me", headers={"X-User-Id": uid})).json()
    assert me["state"] == "left" and me["reason"] == "sale_stopped"
    assert (await client.post("/api/admin/sales/999/stop", headers=ADMIN)).status_code == 404


async def test_invalid_config_rejected(client):
    r = await client.post("/api/admin/sales", json={"tick_seconds": 0}, headers=ADMIN)
    assert r.status_code == 422


async def test_stats_shape_and_counts(client, session, redis):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    await _create_and_start(client)
    a = await _join(client, "a")
    await _join(client, "b")
    await run_tick(session, redis, _now())
    await _pay_first_offer(client, a)

    s = (await client.get("/api/admin/stats?window=60", headers=ADMIN)).json()
    assert s["sale"]["status"] == "running"
    assert s["counts"] == {"joined": 2, "waiting": 0, "offered": 1, "booked": 1, "left": 0, "rejected": 0}
    assert s["holds"] == {"active": 1, "paid": 1, "expired": 0, "failed": 0}
    assert s["payments"] == {"succeeded": 1, "failed": 0, "refunded": 0}
    assert s["wait_seconds"]["max"] is not None and s["wait_seconds"]["p50"] >= 0
    assert len(s["series"]) == 60
    totals = {k: sum(p[k] for p in s["series"]) for k in ("joins", "offers", "bookings", "payment_attempts")}
    assert totals == {"joins": 2, "offers": 2, "bookings": 1, "payment_attempts": 1}
    assert sum(p["rps"] for p in s["series"]) >= 4  # joins, me, pay, ... all counted
    assert set(s["series"][0]) == {"t", "rps", "joins", "offers", "bookings", "payment_attempts", "expired"}


async def test_trace_is_ndjson_in_order(client, session, redis):
    sale_id = await _create_and_start(client)
    await _join(client, "a")
    await run_tick(session, redis, _now())
    r = await client.get(f"/api/admin/sales/{sale_id}/trace", headers=ADMIN)
    assert r.headers["content-type"].startswith("application/x-ndjson")
    rows = [json.loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in rows] == ["sale_started", "user_joined", "seats_released", "offer_made"]
    assert set(rows[0]) == {"ts", "type", "user_id", "seat_id", "data"}
    assert rows[1]["data"]["name"] == "a"


async def test_consistency_all_zero_after_normal_sale(client, session, redis):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    sale_id = await _create_and_start(client)
    a = await _join(client, "a")
    await run_tick(session, redis, _now())
    await _pay_first_offer(client, a)
    r = await client.get(f"/api/admin/sales/{sale_id}/consistency", headers=ADMIN)
    assert r.json() == {
        "double_bookings": 0,
        "bookings_without_successful_payment": 0,
        "charged_without_booking_or_refund": 0,
        "sold_seats_without_booking": 0,
    }


async def test_consistency_detects_unrefunded_charge(client, session, redis):
    sale_id = await _create_and_start(client)
    session.add(
        Payment(
            sale_id=sale_id, hold_id=1, user_id="u", idempotency_key="orphan",
            amount=1000, status="succeeded", created_at=_now(),
        )
    )
    await session.commit()
    r = await client.get(f"/api/admin/sales/{sale_id}/consistency", headers=ADMIN)
    assert r.json()["charged_without_booking_or_refund"] == 1
    assert r.json()["double_bookings"] == 0


async def test_gateway_proxy(client):
    gw = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: gw
    cfg = {"failure_rate": 0.3, "latency_ms_mean": 10, "latency_ms_jitter": 0, "seed": 1}
    assert (await client.put("/api/admin/gateway", json=cfg, headers=ADMIN)).json() == cfg
    assert (await client.get("/api/admin/gateway", headers=ADMIN)).json() == cfg
