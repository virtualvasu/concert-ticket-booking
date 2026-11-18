import random

from loadgen.arrivals import arrival_times
from loadgen.scenario import Arrival


def test_spike_within_spread():
    times = arrival_times(Arrival(kind="spike", spread_s=10), 1000, random.Random(1))
    assert len(times) == 1000
    assert times == sorted(times)
    assert 0 <= times[0] and times[-1] <= 10


def test_poisson_mean_rate():
    times = arrival_times(Arrival(kind="poisson", rate_per_s=50), 10_000, random.Random(2))
    assert len(times) == 10_000
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert abs(sum(gaps) / len(gaps) - 0.02) < 0.02 * 0.05


def test_waves_total_and_ranges():
    arrival = Arrival(
        kind="waves",
        waves=[
            {"at_s": 0, "users": 30, "spread_s": 2},
            {"at_s": 10, "users": 70, "spread_s": 5},
        ],
    )
    times = arrival_times(arrival, 100, random.Random(3))
    assert len(times) == 100
    assert times == sorted(times)
    assert all(0 <= t <= 2 for t in times[:30])
    assert all(10 <= t <= 15 for t in times[30:])


def test_waves_total_must_match_users():
    arrival = Arrival(kind="waves", waves=[{"at_s": 0, "users": 5, "spread_s": 1}])
    try:
        arrival_times(arrival, 6, random.Random(0))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_same_seed_same_times():
    a = Arrival(kind="spike", spread_s=5)
    assert arrival_times(a, 200, random.Random(7)) == arrival_times(a, 200, random.Random(7))
    assert arrival_times(a, 200, random.Random(7)) != arrival_times(a, 200, random.Random(8))
