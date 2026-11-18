"""Numbers derived from a run's trace and client log."""

import numpy as np
import pandas as pd
from scipy.stats import kendalltau

from analysis.load import Run

WAIT_COLUMNS = [
    "user_id", "joined_t", "offered_t", "booked_t", "wait_offer_s", "wait_booked_s", "outcome", "outcome_t",
]


def waiting_times(trace: pd.DataFrame) -> pd.DataFrame:
    """One row per user. In baseline mode the first offer is the first successful grab."""
    users = trace[trace["user_id"].notna()]
    joined = users[users["type"] == "user_joined"].groupby("user_id")["t"].min()
    offered = users[users["type"] == "offer_made"].groupby("user_id")["t"].min()
    booked = users[users["type"] == "booking_confirmed"].groupby("user_id")["t"].min()

    rows = []
    for uid, joined_t in joined.items():
        own = users[users["user_id"] == uid]
        if uid in booked.index:
            outcome, outcome_t = "booked", booked[uid]
        else:
            outcome, outcome_t = "never_served", np.nan
            ends = own[own["type"].isin(["user_rejected", "hold_failed", "hold_expired"])]
            if len(ends):
                last = ends.sort_values("t").iloc[-1]
                outcome_t = last["t"]
                if last["type"] == "hold_expired":
                    outcome = "hold_expired"
                else:
                    outcome = last["data"].get("reason", "never_served")
        offered_t = offered.get(uid, np.nan)
        booked_t = booked.get(uid, np.nan)
        rows.append(
            {
                "user_id": uid,
                "joined_t": joined_t,
                "offered_t": offered_t,
                "booked_t": booked_t,
                "wait_offer_s": offered_t - joined_t,
                "wait_booked_s": booked_t - joined_t,
                "outcome": outcome,
                "outcome_t": outcome_t,
            }
        )
    return pd.DataFrame(rows, columns=WAIT_COLUMNS)


def allocation_efficiency(trace: pd.DataFrame, total_seats: int) -> dict:
    sold = int((trace["type"] == "booking_confirmed").sum())
    ended = trace[(trace["type"] == "sale_ended")]
    sellout = None
    if len(ended) and ended.iloc[0]["data"].get("end_reason") == "sold_out":
        sellout = float(ended.iloc[0]["t"])

    # A seat becomes free when released or when a hold on it lapses; idle = free -> next offer.
    free: list[tuple[int, float]] = []
    for _, r in trace[trace["type"] == "seats_released"].iterrows():
        free += [(int(s), float(r["t"])) for s in r["data"]["seat_ids"]]
    for _, r in trace[trace["type"].isin(["hold_expired", "hold_failed"])].iterrows():
        free.append((int(r["seat_id"]), float(r["t"])))
    offers: dict[int, list[float]] = {}
    for _, r in trace[trace["type"] == "offer_made"].iterrows():
        offers.setdefault(int(r["seat_id"]), []).append(float(r["t"]))
    idle = []
    for seat, t in free:
        later = [o for o in offers.get(seat, []) if o >= t]
        if later:
            idle.append(min(later) - t)

    offered = trace[trace["type"] == "offer_made"]["data"].map(lambda d: d["hold_id"]).unique()
    booked = set(trace[trace["type"] == "booking_confirmed"]["data"].map(lambda d: d["hold_id"]))
    wasted = len([h for h in offered if h not in booked])
    return {
        "sold_ratio": sold / total_seats if total_seats else 0.0,
        "time_to_sellout_s": sellout,
        "mean_seat_idle_s": float(np.mean(idle)) if idle else None,
        "wasted_holds": wasted,
        "wasted_hold_ratio": wasted / len(offered) if len(offered) else 0.0,
    }


def fairness(waits: pd.DataFrame) -> dict:
    b = waits[waits["booked_t"].notna()]
    join, book = b["joined_t"].to_numpy(), b["booked_t"].to_numpy()
    n = len(b)
    inversions = int(sum(((join[i] < join[j]) != (book[i] < book[j])) for i in range(n) for j in range(i + 1, n)))
    tau = float(kendalltau(join, book).statistic) if n > 1 else float("nan")
    x = b["wait_booked_s"].to_numpy(dtype=float)
    jain = float(x.sum() ** 2 / (n * (x**2).sum())) if n and (x**2).sum() else float("nan")

    booked_flag = waits.sort_values("joined_t")["booked_t"].notna().to_numpy()
    deciles = [float(c.mean()) if len(c) else float("nan") for c in np.array_split(booked_flag, 10)]
    return {"kendall_tau": tau, "inversions": inversions, "jain_index": jain, "success_by_arrival_decile": deciles}


def surge(client: pd.DataFrame, trace: pd.DataFrame) -> dict:
    sec = np.floor(client["ts"]).astype(int)
    counts = sec.value_counts()
    span = int(sec.max() - sec.min() + 1)
    mean_rps = float(len(client) / span)
    peak = int(counts.max())
    attempts = trace[trace["type"] == "payment_attempt"]["t"].astype(int).value_counts()
    return {
        "peak_rps": peak,
        "mean_rps": mean_rps,
        "peak_to_mean": peak / mean_rps,
        "p95_latency_ms": float(np.percentile(client["latency_ms"], 95)),
        "error_rate": float(((client["status"] == 0) | (client["status"] >= 500)).mean()),
        "peak_payment_attempts_per_s": int(attempts.max()) if len(attempts) else 0,
    }


def rejections(waits: pd.DataFrame) -> pd.DataFrame:
    lost = waits[~waits["outcome"].isin(["booked", "never_served"])]
    out = (
        lost.groupby("outcome")["outcome_t"]
        .agg(count="count", first_t="min", median_t="median")
        .reset_index()
        .rename(columns={"outcome": "reason"})
    )
    return out


def summarize(run: Run) -> dict:
    waits = waiting_times(run.trace)
    total = int(run.trace[run.trace["type"] == "seats_released"]["data"].map(lambda d: d["count"]).sum())
    seats_cfg = run.scenario.get("sale", {}).get("sections")
    if seats_cfg:
        total = sum(s["rows"] * s["seats_per_row"] for s in seats_cfg)
    elif not total:
        total = 500
    booked_waits = waits["wait_booked_s"].dropna()
    return {
        "name": run.scenario.get("name"),
        "users": int(len(waits)),
        "booked": int(len(booked_waits)),
        "wait_booked_p50_s": float(booked_waits.median()) if len(booked_waits) else None,
        "wait_booked_p95_s": float(booked_waits.quantile(0.95)) if len(booked_waits) else None,
        "allocation": allocation_efficiency(run.trace, total),
        "fairness": fairness(waits),
        "surge": surge(run.client, run.trace),
        "rejections": rejections(waits).to_dict("records"),
        "consistency": run.consistency,
    }
