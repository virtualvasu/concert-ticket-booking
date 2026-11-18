"""One simulated buyer."""

import asyncio
import random
import time
import uuid

import httpx

from loadgen.clientlog import ClientLog
from loadgen.scenario import Scenario

TERMINAL = {"booked", "left", "rejected"}


async def _call(client: httpx.AsyncClient, log: ClientLog, idx: int, endpoint: str, method: str, path: str, **kw):
    """One timed request; network errors are logged as status 0 and returned as None."""
    t0 = time.time()
    try:
        res = await client.request(method, path, **kw)
        code = res.status_code
    except httpx.HTTPError:
        res, code = None, 0
    log.write(t0, idx, endpoint, code, (time.time() - t0) * 1000)
    return res


async def simulate_user(
    client: httpx.AsyncClient,
    idx: int,
    scenario: Scenario,
    rng: random.Random,
    log: ClientLog,
) -> None:
    b = scenario.behavior
    baseline = scenario.sale.get("mode", "bucket") == "baseline"

    res = await _call(client, log, idx, "join", "POST", "/api/queue/join", json={"name": f"user-{idx}"})
    if res is None or res.status_code != 200:
        return
    headers = {"X-User-Id": res.json()["user_id"]}
    decided: set[int] = set()  # holds this user already chose to ignore or pay

    while True:
        me = await _call(client, log, idx, "me", "GET", "/api/me", headers=headers)
        if me is None or me.status_code != 200:
            await asyncio.sleep(b.poll_seconds)
            continue
        info = me.json()
        if info["state"] in TERMINAL:
            return

        offer = info["offer"]
        if offer and offer["hold_id"] not in decided:
            decided.add(offer["hold_id"])
            if rng.random() < b.abandon_prob:
                pass  # walk away; the hold will expire
            else:
                await _pay_offer(client, log, idx, headers, offer["hold_id"], b, rng)
                continue
        elif info["state"] == "browsing" and baseline:
            await _call(client, log, idx, "grab", "POST", "/api/baseline/grab", headers=headers)
            await asyncio.sleep(b.poll_seconds)
            continue
        await asyncio.sleep(b.poll_seconds)


async def _pay_offer(client, log, idx, headers, hold_id, b, rng) -> None:
    await asyncio.sleep(rng.uniform(*b.pay_delay_s))
    while True:
        res = await _call(
            client,
            log,
            idx,
            "pay",
            "POST",
            "/api/pay",
            headers=headers,
            json={"hold_id": hold_id, "idempotency_key": str(uuid.UUID(int=rng.getrandbits(128)))},
        )
        if res is None or res.status_code != 200:
            return
        if res.json()["status"] != "failed_retry":
            return
        await asyncio.sleep(rng.uniform(1, 3))
