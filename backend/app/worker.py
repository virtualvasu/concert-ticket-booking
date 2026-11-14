"""Tick loop: ``python -m app.worker``."""

import asyncio
import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.domain.dispatcher import run_tick
from app.redis_client import get_redis

log = logging.getLogger("worker")


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    await init_db()
    redis = get_redis()
    interval = get_settings().worker_interval_ms / 1000
    log.info("worker started, interval %.3fs", interval)
    while True:
        try:
            async with SessionLocal() as session:  # fresh session: no stale ORM state
                result = await run_tick(session, redis, datetime.now(timezone.utc))
            if any(result.values()):
                log.info("tick %s", result)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(interval)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
