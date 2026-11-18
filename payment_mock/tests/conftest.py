import httpx
import pytest_asyncio

import app as gateway_app


@pytest_asyncio.fixture
async def gw():
    """Client on a fresh gateway: zero latency, empty state."""
    gateway_app.reset_state()
    transport = httpx.ASGITransport(app=gateway_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as c:
        r = await c.put(
            "/config",
            json={"failure_rate": 0.0, "latency_ms_mean": 0, "latency_ms_jitter": 0},
        )
        assert r.status_code == 200
        yield c
