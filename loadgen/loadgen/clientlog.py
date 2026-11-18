"""Client-side request log (client.jsonl): what each user saw."""

import json
import time
from pathlib import Path


class ClientLog:
    def __init__(self, path: Path):
        self._f = open(path, "w", buffering=1 << 16)

    def write(self, ts: float, user_idx: int, endpoint: str, status_code: int, latency_ms: float) -> None:
        self._f.write(
            json.dumps(
                {
                    "ts": ts,
                    "user_idx": user_idx,
                    "endpoint": endpoint,
                    "status": status_code,
                    "latency_ms": round(latency_ms, 2),
                }
            )
            + "\n"
        )

    def close(self) -> None:
        self._f.close()


def now() -> float:
    return time.time()
