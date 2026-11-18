"""When each simulated user shows up."""

import random

from loadgen.scenario import Arrival


def arrival_times(arrival: Arrival, users: int, rng: random.Random) -> list[float]:
    """Sorted arrival offsets in seconds, one per user."""
    if arrival.kind == "spike":
        times = [rng.uniform(0, arrival.spread_s) for _ in range(users)]
    elif arrival.kind == "poisson":
        if not arrival.rate_per_s or arrival.rate_per_s <= 0:
            raise ValueError("poisson arrival needs rate_per_s > 0")
        t, times = 0.0, []
        for _ in range(users):
            t += rng.expovariate(arrival.rate_per_s)
            times.append(t)
    else:  # waves
        if sum(w.users for w in arrival.waves) != users:
            raise ValueError("wave user counts must add up to users")
        times = [rng.uniform(w.at_s, w.at_s + w.spread_s) for w in arrival.waves for _ in range(w.users)]
    return sorted(times)
