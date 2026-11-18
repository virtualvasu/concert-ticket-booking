"""Report figures (PNG at 300 dpi plus PDF), one consistent style."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.load import Run
from analysis.metrics import fairness, summarize, waiting_times

plt.rcParams.update(
    {
        "figure.figsize": (6.4, 3.8),
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 10,
        "axes.prop_cycle": plt.cycler(color=["#2563eb", "#f59e0b", "#16a34a", "#dc2626", "#7c3aed"]),
    }
)
SEAT_COLORS = {"LOCKED": "#94a3b8", "AVAILABLE": "#3b82f6", "HELD": "#f59e0b", "SOLD": "#16a34a"}


def _save(fig, out_dir: Path, name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / f"{name}.png", dpi=300)
    fig.savefig(out_dir / f"{name}.pdf")
    plt.close(fig)


def _rps(run: Run) -> pd.Series:
    return run.client.groupby(np.floor(run.client["t"]).astype(int)).size()


def _total_seats(run: Run) -> int:
    sections = run.scenario.get("sale", {}).get("sections")
    return sum(s["rows"] * s["seats_per_row"] for s in sections) if sections else 500


def seat_states(run: Run) -> pd.DataFrame:
    """Seat counts per state at each second, rebuilt from the event trace."""
    tr = run.trace
    horizon = int(np.ceil(tr["t"].max())) + 1
    grid = np.arange(0, horizon + 1)

    def cum(types, weight=None):
        sub = tr[tr["type"].isin(types)]
        w = sub["data"].map(weight) if weight else pd.Series(1, index=sub.index)
        s = pd.Series(w.to_numpy(), index=np.ceil(sub["t"]).astype(int)).groupby(level=0).sum()
        return s.reindex(grid, fill_value=0).cumsum()

    released = cum(["seats_released"], lambda d: d["count"])
    offers, sold = cum(["offer_made"]), cum(["booking_confirmed"])
    back = cum(["hold_expired", "hold_failed"])
    total = _total_seats(run)
    return pd.DataFrame(
        {"LOCKED": total - released, "AVAILABLE": released + back - offers, "HELD": offers - sold - back, "SOLD": sold},
        index=grid,
    ).clip(lower=0)


def plot_run(run: Run, out_dir: str | Path) -> None:
    out = Path(out_dir)
    waits = waiting_times(run.trace)

    fig, ax = plt.subplots()
    rps = _rps(run)
    ax.plot(rps.index, rps.values)
    ax.set(xlabel="Time since sale start (s)", ylabel="Requests per second", title="Request rate")
    _save(fig, out, "request_rate")

    fig, ax = plt.subplots()
    st = seat_states(run)
    ax.stackplot(st.index, [st[c] for c in SEAT_COLORS], labels=list(SEAT_COLORS), colors=list(SEAT_COLORS.values()))
    ax.set(xlabel="Time since sale start (s)", ylabel="Seats", title="Seat states")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=4, frameon=False)
    _save(fig, out, "seat_states")

    fig, ax = plt.subplots()
    _cdf(ax, waits["wait_booked_s"].dropna(), "booked users")
    ax.set(xlabel="Wait until booked (s)", ylabel="Share of booked users", title="Wait-time CDF")
    _save(fig, out, "wait_cdf")

    fig, ax = plt.subplots()
    dec = fairness(waits)["success_by_arrival_decile"]
    ax.bar(range(1, 11), dec)
    ax.set(xlabel="Arrival decile (1 = earliest)", ylabel="Share who booked", ylim=(0, 1), title="Success by arrival order")
    _save(fig, out, "success_by_decile")


def _cdf(ax, values: pd.Series, label: str) -> None:
    v = np.sort(values.to_numpy(dtype=float))
    if len(v):
        ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post", label=label)


def plot_comparison(runs: list[Run], labels: list[str], out_dir: str | Path) -> pd.DataFrame:
    out = Path(out_dir)
    summaries = [summarize(r) for r in runs]

    fig, ax = plt.subplots()
    for r, lab in zip(runs, labels):
        rps = _rps(r)
        ax.plot(rps.index, rps.values, label=lab)
    ax.set(xlabel="Time since sale start (s)", ylabel="Requests per second", title="Request rate")
    ax.legend(frameon=False)
    _save(fig, out, "compare_request_rate")

    fig, ax = plt.subplots()
    for r, lab in zip(runs, labels):
        _cdf(ax, waiting_times(r.trace)["wait_booked_s"].dropna(), lab)
    ax.set(xlabel="Wait until booked (s)", ylabel="Share of booked users", title="Wait-time CDF")
    ax.legend(frameon=False)
    _save(fig, out, "compare_wait_cdf")

    table = pd.DataFrame(
        [
            {
                "run": lab,
                "booked": s["booked"],
                "sold_ratio": s["allocation"]["sold_ratio"],
                "time_to_sellout_s": s["allocation"]["time_to_sellout_s"],
                "mean_seat_idle_s": s["allocation"]["mean_seat_idle_s"],
                "wasted_hold_ratio": s["allocation"]["wasted_hold_ratio"],
                "wait_p50_s": s["wait_booked_p50_s"],
                "wait_p95_s": s["wait_booked_p95_s"],
                "jain_index": s["fairness"]["jain_index"],
                "kendall_tau": s["fairness"]["kendall_tau"],
                "peak_rps": s["surge"]["peak_rps"],
                "peak_to_mean": s["surge"]["peak_to_mean"],
                "p95_latency_ms": s["surge"]["p95_latency_ms"],
                "error_rate": s["surge"]["error_rate"],
            }
            for lab, s in zip(labels, summaries)
        ]
    )
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "summary.csv", index=False)

    metrics = [("peak_rps", "Peak requests/s"), ("wait_p95_s", "p95 wait (s)"), ("p95_latency_ms", "p95 latency (ms)"), ("kendall_tau", "Kendall tau")]
    fig, axes = plt.subplots(1, len(metrics), figsize=(3 * len(metrics), 3.4))
    for ax, (col, title) in zip(axes, metrics):
        ax.bar(labels, table[col].astype(float).fillna(0), color=plt.rcParams["axes.prop_cycle"].by_key()["color"][: len(labels)])
        ax.set_title(title, fontsize=10)
        ax.tick_params(axis="x", rotation=30)
    _save(fig, out, "compare_metrics")
    return table
