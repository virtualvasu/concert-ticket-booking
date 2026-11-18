"""Metrics on a hand-built trace: 4 seats, 5 users.

u1 and u4 book straight away. u2's hold expires, u3 pays after its hold expired
(late success, refunded); both grab again and book. u5 never gets a seat.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from analysis.metrics import allocation_efficiency, fairness, rejections, surge, waiting_times

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trace() -> pd.DataFrame:
    ev = [
        (0, "sale_started", None, None, {}),
        (1, "user_joined", "u1", None, {"seq": 1}),
        (2, "user_joined", "u2", None, {"seq": 2}),
        (3, "user_joined", "u3", None, {"seq": 3}),
        (4, "user_joined", "u4", None, {"seq": 4}),
        (5, "user_joined", "u5", None, {"seq": 5}),
        (10, "seats_released", None, None, {"seat_ids": [1, 2, 3, 4], "count": 4}),
        (10, "offer_made", "u1", 1, {"hold_id": 1}),
        (11, "offer_made", "u2", 2, {"hold_id": 2}),
        (12, "offer_made", "u3", 3, {"hold_id": 3}),
        (13, "offer_made", "u4", 4, {"hold_id": 4}),
        (15, "booking_confirmed", "u1", 1, {"hold_id": 1}),
        (16, "booking_confirmed", "u4", 4, {"hold_id": 4}),
        (70, "payment_attempt", "u3", 3, {"hold_id": 3}),
        (71, "hold_expired", "u2", 2, {"hold_id": 2}),
        (71, "hold_expired", "u3", 3, {"hold_id": 3}),
        (72, "payment_succeeded", "u3", None, {"hold_id": 3}),
        (72, "refund_issued", "u3", None, {"hold_id": 3}),
        (72, "offer_made", "u2", 2, {"hold_id": 5}),
        (73, "offer_made", "u3", 3, {"hold_id": 6}),
        (75, "booking_confirmed", "u2", 2, {"hold_id": 5}),
        (76, "booking_confirmed", "u3", 3, {"hold_id": 6}),
        (80, "user_rejected", "u5", None, {"reason": "sold_out"}),
        (80, "sale_ended", None, None, {"end_reason": "sold_out"}),
    ]
    df = pd.DataFrame(
        [
            {"ts": T0 + timedelta(seconds=t), "type": ty, "user_id": u, "seat_id": s, "data": d}
            for t, ty, u, s, d in ev
        ]
    )
    df["t"] = (df["ts"] - T0).dt.total_seconds()
    return df


def test_waiting_times_values():
    w = waiting_times(_trace()).set_index("user_id")
    assert w.loc["u1", "wait_booked_s"] == 14
    assert w.loc["u2", "wait_booked_s"] == 73
    assert w.loc["u3", "wait_booked_s"] == 73
    assert w.loc["u4", "wait_booked_s"] == 12
    assert w.loc["u1", "wait_offer_s"] == 9
    assert pd.isna(w.loc["u5", "wait_booked_s"]) and pd.isna(w.loc["u5", "offered_t"])
    assert list(w.loc[["u1", "u2", "u3", "u4", "u5"], "outcome"]) == ["booked"] * 4 + ["sold_out"]


def test_allocation_efficiency_values():
    a = allocation_efficiency(_trace(), total_seats=4)
    assert a["sold_ratio"] == 1.0
    assert a["time_to_sellout_s"] == 80
    assert a["wasted_holds"] == 2
    assert a["wasted_hold_ratio"] == pytest.approx(2 / 6)
    assert a["mean_seat_idle_s"] == pytest.approx(1.5)


def _waits(joined, booked) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(len(joined))],
            "joined_t": joined,
            "booked_t": booked,
            "wait_booked_s": [b - j for j, b in zip(joined, booked)],
        }
    )


def test_fairness_perfect_order():
    f = fairness(_waits([1, 2, 3, 4], [10, 11, 12, 13]))
    assert f["kendall_tau"] == pytest.approx(1.0)
    assert f["inversions"] == 0


def test_fairness_one_swap():
    f = fairness(_waits([1, 2, 3, 4], [10, 12, 11, 13]))
    assert f["inversions"] == 1
    assert f["kendall_tau"] < 1.0


def test_jain_equal_waits_is_one():
    f = fairness(_waits([1, 2, 3, 4], [11, 12, 13, 14]))  # every wait is 10 s
    assert f["jain_index"] == pytest.approx(1.0)
    assert len(f["success_by_arrival_decile"]) == 10


def test_surge_peak_to_mean():
    ts = [100.0 + i * 0.01 for i in range(10)] + [101.0 + i for i in range(9)]  # 10 in second 0, then 1/s
    client = pd.DataFrame({"ts": ts, "user_idx": 0, "endpoint": "me", "status": 200, "latency_ms": 10.0})
    s = surge(client, _trace())
    assert s["peak_rps"] == 10
    assert s["mean_rps"] == pytest.approx(1.9)
    assert s["peak_to_mean"] == pytest.approx(10 / 1.9)
    assert s["error_rate"] == 0.0
    assert s["peak_payment_attempts_per_s"] == 1


def test_rejections_counts():
    r = rejections(waiting_times(_trace())).set_index("reason")
    assert r.loc["sold_out", "count"] == 1
    assert r.loc["sold_out", "first_t"] == 80
    assert "booked" not in r.index
