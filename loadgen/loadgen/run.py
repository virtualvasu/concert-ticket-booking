"""CLI: ``python -m loadgen.run <scenario.yaml> [--out runs]``."""

import argparse
import asyncio
import json
import random
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from loadgen.arrivals import arrival_times
from loadgen.clientlog import ClientLog
from loadgen.scenario import Scenario
from loadgen.user import simulate_user


async def _user_task(i, at, start, sem, client, scenario, log):
    await asyncio.sleep(max(0.0, start + at - time.monotonic()))
    async with sem:
        await simulate_user(client, i, scenario, random.Random(f"{scenario.seed}-{i}"), log)


async def _get_json(client: httpx.AsyncClient, path: str, tries: int = 5, **kw):
    """GET that survives transient errors (a saturated proxy answers 502/504 with HTML)."""
    for attempt in range(tries):
        try:
            res = await client.get(path, **kw)
            if res.status_code == 200:
                return res.json()
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(min(2 ** attempt, 10))
    return None


async def run(scenario: Scenario, scenario_path: Path, out: Path) -> int:
    run_dir = out / f"{scenario.name}-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True)
    shutil.copy(scenario_path, run_dir / "scenario.yaml")
    log = ClientLog(run_dir / "client.jsonl")

    admin = {"X-Admin-Token": scenario.admin_token}
    limits = httpx.Limits(max_connections=scenario.behavior.max_concurrency, max_keepalive_connections=200)
    async with httpx.AsyncClient(base_url=scenario.base_url, timeout=30, limits=limits) as client:
        if scenario.gateway:
            current = (await client.get("/api/admin/gateway", headers=admin)).json()
            r = await client.put("/api/admin/gateway", headers=admin, json={**current, **scenario.gateway})
            r.raise_for_status()
        r = await client.post("/api/admin/sales", headers=admin, json=scenario.sale)
        r.raise_for_status()
        sale_id = r.json()["sale_id"]
        r = await client.post(f"/api/admin/sales/{sale_id}/start", headers=admin)
        r.raise_for_status()
        print(f"sale {sale_id} started, launching {scenario.users} users", flush=True)

        rng = random.Random(scenario.seed)
        times = arrival_times(scenario.arrival, scenario.users, rng)
        sem = asyncio.Semaphore(scenario.behavior.max_concurrency)
        start = time.monotonic()
        tasks = [
            asyncio.create_task(_user_task(i, at, start, sem, client, scenario, log))
            for i, at in enumerate(times)
        ]

        stopped_early = False
        sale: dict = {}
        while True:
            await asyncio.sleep(1)
            polled = await _get_json(client, "/api/sale", tries=1)
            if polled is None:
                continue
            sale = polled
            if sale["status"] == "ended":
                break
            elapsed = time.monotonic() - start
            all_done = all(t.done() for t in tasks)
            if elapsed > scenario.max_duration_s or all_done:
                await client.post(f"/api/admin/sales/{sale_id}/stop", headers=admin)
                stopped_early = True
                sale = await _get_json(client, "/api/sale") or sale
                break
        duration = time.monotonic() - start

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.close()

        for attempt in range(5):
            trace = await client.get(f"/api/admin/sales/{sale_id}/trace", headers=admin, timeout=300)
            if trace.status_code == 200:
                break
            await asyncio.sleep(2 ** attempt)
        else:
            raise RuntimeError("could not download the trace")
        (run_dir / "trace.jsonl").write_bytes(trace.content)
        consistency = await _get_json(client, f"/api/admin/sales/{sale_id}/consistency", headers=admin)
        if consistency is None:
            raise RuntimeError("could not fetch the consistency check")
        (run_dir / "consistency.json").write_text(json.dumps(consistency, indent=2))

    summary = {
        "name": scenario.name,
        "sale_id": sale_id,
        "users": scenario.users,
        "duration_s": round(duration, 2),
        "end_reason": sale.get("end_reason"),
        "stopped_early": stopped_early,
        "seats": sale.get("seats"),
        "consistency_ok": all(v == 0 for v in consistency.values()),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    print(f"run written to {run_dir}")
    return 0 if summary["consistency_ok"] else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("scenario")
    p.add_argument("--out", default="runs")
    args = p.parse_args()
    sys.exit(asyncio.run(run(Scenario.load(args.scenario), Path(args.scenario), Path(args.out))))


if __name__ == "__main__":
    main()
