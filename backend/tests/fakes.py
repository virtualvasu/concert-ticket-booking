from collections.abc import Callable

from app.payments.gateway import ChargeResult


class FakeGateway:
    """Stand-in for GatewayClient: scripted outcome, call counters, a charge hook."""

    def __init__(self) -> None:
        self.next_status = "succeeded"
        self.on_charge: Callable[[], None] | None = None
        self.charges = 0
        self.refunds: list[str] = []
        self.config: dict = {}

    async def charge(self, idempotency_key, amount, force=None) -> ChargeResult:
        self.charges += 1
        status = {"success": "succeeded", "fail": "failed"}.get(force, self.next_status)
        if self.on_charge:
            self.on_charge()
        return ChargeResult(status=status, ref=f"ref-{self.charges}")

    async def refund(self, ref) -> None:
        self.refunds.append(ref)

    async def get_config(self) -> dict:
        return self.config

    async def set_config(self, cfg: dict) -> dict:
        self.config = cfg
        return cfg
