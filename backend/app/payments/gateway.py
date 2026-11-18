"""HTTP client for the mock payment gateway."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import httpx

from app.config import get_settings


@dataclass(frozen=True)
class ChargeResult:
    status: Literal["succeeded", "failed"]
    ref: str


class GatewayClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def charge(
        self, idempotency_key: str, amount: int, force: str | None = None
    ) -> ChargeResult:
        r = await self._http.post(
            "/charge",
            json={"idempotency_key": idempotency_key, "amount": amount, "force": force},
        )
        r.raise_for_status()
        body = r.json()
        return ChargeResult(status=body["status"], ref=body["ref"])

    async def refund(self, ref: str) -> None:
        r = await self._http.post("/refund", json={"ref": ref})
        r.raise_for_status()

    async def get_config(self) -> dict:
        r = await self._http.get("/config")
        r.raise_for_status()
        return r.json()

    async def set_config(self, cfg: dict) -> dict:
        r = await self._http.put("/config", json=cfg)
        r.raise_for_status()
        return r.json()


@lru_cache
def get_gateway() -> GatewayClient:
    """FastAPI dependency; tests override it with a fake."""
    return GatewayClient(get_settings().payment_url)
