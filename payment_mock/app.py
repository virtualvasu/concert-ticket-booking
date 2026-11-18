"""Mock payment gateway: configurable latency and failure rate, idempotent charges."""

import asyncio
import random
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class GatewayConfig(BaseModel):
    failure_rate: float = 0.1
    latency_ms_mean: int = 300
    latency_ms_jitter: int = 200
    seed: int | None = None


class ChargeRequest(BaseModel):
    idempotency_key: str
    amount: int
    force: Literal["success", "fail"] | None = None


class RefundRequest(BaseModel):
    ref: str


class _State:
    def __init__(self) -> None:
        self.config = GatewayConfig()
        self.rng = random.Random()
        self.charges: dict[str, asyncio.Future] = {}
        self.refs: dict[str, str] = {}  # ref -> "charged" | "refunded"
        self.succeeded = 0
        self.failed = 0
        self.refunds = 0


state = _State()


def reset_state() -> None:
    global state
    state = _State()


app = FastAPI(title="Mock payment gateway")


async def _do_charge(req: ChargeRequest, cfg: GatewayConfig, rng: random.Random) -> dict:
    # Draw outcome and latency up front so a seeded run is repeatable regardless
    # of `force` or how requests interleave while sleeping.
    roll = rng.random()
    lo = cfg.latency_ms_mean - cfg.latency_ms_jitter
    hi = cfg.latency_ms_mean + cfg.latency_ms_jitter
    latency_ms = max(0.0, rng.uniform(lo, hi))
    if req.force == "success":
        ok = True
    elif req.force == "fail":
        ok = False
    else:
        ok = roll >= cfg.failure_rate
    await asyncio.sleep(latency_ms / 1000)
    ref = uuid.uuid4().hex
    state.refs[ref] = "charged"
    if ok:
        state.succeeded += 1
    else:
        state.failed += 1
    return {"status": "succeeded" if ok else "failed", "ref": ref}


@app.post("/charge")
async def charge(req: ChargeRequest) -> dict:
    existing = state.charges.get(req.idempotency_key)
    if existing is not None:
        # Same key: return the stored response (waiting if still in flight).
        return await asyncio.shield(existing)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    state.charges[req.idempotency_key] = fut
    try:
        result = await _do_charge(req, state.config, state.rng)
    except BaseException as exc:
        state.charges.pop(req.idempotency_key, None)
        fut.set_exception(exc)
        fut.exception()  # mark retrieved
        raise
    fut.set_result(result)
    return result


@app.post("/refund")
async def refund(req: RefundRequest) -> dict:
    if req.ref not in state.refs:
        raise HTTPException(404, "unknown ref")
    if state.refs[req.ref] != "refunded":
        state.refs[req.ref] = "refunded"
        state.refunds += 1
    return {"status": "refunded"}


@app.get("/config")
async def get_config() -> GatewayConfig:
    return state.config


@app.put("/config")
async def put_config(cfg: GatewayConfig) -> GatewayConfig:
    state.config = cfg
    state.rng = random.Random(cfg.seed) if cfg.seed is not None else random.Random()
    return cfg


@app.get("/stats")
async def stats() -> dict:
    return {
        "charges": state.succeeded + state.failed,
        "succeeded": state.succeeded,
        "failed": state.failed,
        "refunds": state.refunds,
    }
