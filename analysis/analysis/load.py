"""Load one load-generator run directory."""

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml


@dataclass
class Run:
    trace: pd.DataFrame  # ts (UTC datetime), t (s since sale_started), type, user_id, seat_id, data
    client: pd.DataFrame  # ts (epoch s), t (s since sale_started), user_idx, endpoint, status, latency_ms
    scenario: dict
    consistency: dict
    path: Path | None = None


def _read_jsonl(path: Path) -> pd.DataFrame:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return pd.DataFrame(rows)


def load_run(run_dir: str | Path) -> Run:
    d = Path(run_dir)
    trace = _read_jsonl(d / "trace.jsonl")
    trace["ts"] = pd.to_datetime(trace["ts"], utc=True, format="ISO8601")
    started = trace.loc[trace["type"] == "sale_started", "ts"].iloc[0]
    trace["t"] = (trace["ts"] - started).dt.total_seconds()

    client = _read_jsonl(d / "client.jsonl")
    client["t"] = client["ts"] - started.timestamp()

    return Run(
        trace=trace,
        client=client,
        scenario=yaml.safe_load((d / "scenario.yaml").read_text()),
        consistency=json.loads((d / "consistency.json").read_text()),
        path=d,
    )
