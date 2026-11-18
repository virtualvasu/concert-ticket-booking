import itertools

_ids = itertools.count()


def key(prefix: str = "k") -> str:
    return f"{prefix}-{next(_ids)}"


async def charge(gw, force=None, idem=None, amount=1000):
    r = await gw.post(
        "/charge", json={"idempotency_key": idem or key(), "amount": amount, "force": force}
    )
    assert r.status_code == 200
    return r.json()


async def test_force_success_and_fail(gw):
    await gw.put("/config", json={"failure_rate": 1.0, "latency_ms_mean": 0, "latency_ms_jitter": 0})
    ok = await charge(gw, force="success")
    bad = await charge(gw, force="fail")
    assert ok["status"] == "succeeded" and ok["ref"]
    assert bad["status"] == "failed" and bad["ref"]
    await gw.put("/config", json={"failure_rate": 0.0, "latency_ms_mean": 0, "latency_ms_jitter": 0})
    assert (await charge(gw, force="fail"))["status"] == "failed"


async def test_idempotent_charge(gw):
    k = key()
    first = await charge(gw, idem=k)
    second = await charge(gw, idem=k)
    assert first == second
    stats = (await gw.get("/stats")).json()
    assert stats["charges"] == 1


async def test_failure_rate_one_always_fails(gw):
    await gw.put("/config", json={"failure_rate": 1.0, "latency_ms_mean": 0, "latency_ms_jitter": 0})
    results = [(await charge(gw))["status"] for _ in range(10)]
    assert results == ["failed"] * 10
    stats = (await gw.get("/stats")).json()
    assert stats == {"charges": 10, "succeeded": 0, "failed": 10, "refunds": 0}


async def test_refund(gw):
    ref = (await charge(gw, force="success"))["ref"]
    r = await gw.post("/refund", json={"ref": ref})
    assert r.status_code == 200 and r.json() == {"status": "refunded"}
    assert (await gw.get("/stats")).json()["refunds"] == 1


async def test_refund_unknown_ref_404(gw):
    r = await gw.post("/refund", json={"ref": "nope"})
    assert r.status_code == 404


async def test_seeded_outcomes_repeat(gw):
    cfg = {"failure_rate": 0.5, "latency_ms_mean": 0, "latency_ms_jitter": 0, "seed": 42}
    runs = []
    for run in range(2):
        assert (await gw.put("/config", json=cfg)).status_code == 200
        runs.append([(await charge(gw, idem=f"run{run}-{i}"))["status"] for i in range(20)])
    assert runs[0] == runs[1]
    assert {"succeeded", "failed"} <= set(runs[0])


async def test_config_roundtrip(gw):
    cfg = {"failure_rate": 0.3, "latency_ms_mean": 50, "latency_ms_jitter": 10, "seed": 7}
    assert (await gw.put("/config", json=cfg)).json() == cfg
    assert (await gw.get("/config")).json() == cfg


async def test_latency_is_applied(gw):
    import time

    await gw.put("/config", json={"failure_rate": 0.0, "latency_ms_mean": 120, "latency_ms_jitter": 0})
    t0 = time.monotonic()
    await charge(gw)
    assert time.monotonic() - t0 >= 0.1
    # a repeated key must not sleep again
    k = key()
    await charge(gw, idem=k)
    t1 = time.monotonic()
    await charge(gw, idem=k)
    assert time.monotonic() - t1 < 0.05
