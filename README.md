# Concert ticket booking with a leaky bucket

A concert booking system where seats are released at a fixed rate (a leaky bucket),
users are served strictly in queue order, and a seat is confirmed only after a
successful payment. A second mode, `baseline`, lets everyone grab seats directly so the
bucket's effect can be measured. The repository also holds a load generator and analysis
scripts that turn a run into figures and tables.

Course: Computer System Design, Booking theme, sub-theme 3. Design notes are in
[docs/design.md](docs/design.md), the build plan in [docs/implementation-plan.md](docs/implementation-plan.md).

Rules the system keeps:

- A seat is booked only after a successful payment; a failed or unfinished payment never books.
- No seat is booked twice (`UNIQUE (sale_id, seat_id)` on bookings).
- A charged payment ends as a booking or a recorded refund, never neither.
- Payment calls carry an idempotency key, so a repeat never charges twice.
- Every state change is written to an `events` table with a UTC timestamp.

## Architecture

```
 browser ──► web (nginx, React + shadcn/ui, :8080)
                │  /api/*
                ▼
            api (FastAPI, 4 workers, :8000) ───► payment (mock gateway, :8001)
              │        │
              │        └────────────► redis (queue, request counters)
              ▼
          postgres (seats, holds, bookings, payments, events)
              ▲
              │
          worker (tick loop: release seats, dispatch offers, expire holds)
```

| Service | What it does |
|---|---|
| `web` | Buyer page (`/`) and admin dashboard (`/admin`); nginx proxies `/api` to `api` |
| `api` | Join queue, `/api/me`, pay, baseline grab, admin, stats, trace export |
| `worker` | Every tick, releases `seats_per_tick` seats and offers them to the front of the queue; expires holds |
| `payment` | Mock gateway with configurable latency and failure rate |
| `redis` | Queue (sorted set by join order) and per-second request counters |
| `postgres` | Source of truth |

Seat states: `LOCKED → AVAILABLE → HELD → SOLD`, and `HELD → AVAILABLE` on a failed payment or an expired hold.

## Run it

You need Docker and nothing else.

```bash
cp .env.example .env          # the defaults work as they are
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:8080 | Buyer page |
| http://localhost:8080/admin | Admin dashboard (token: `admin`, set `ADMIN_TOKEN` in `.env` to change it) |
| http://localhost:8000/docs | API docs |

From the admin page, create and start a sale, then open the buyer page in one or more tabs.
The "demo" switch on the offer card forces a payment to fail.

Quick end-to-end check of the whole stack:

```bash
bash scripts/smoke.sh        # prints SMOKE OK
```

### Deploying to another machine

Copy the repository, create `.env` from `.env.example` (set `ADMIN_TOKEN`), then run
`docker compose up -d --build`. Open port 8080 (the other published ports are only for development).

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
docker compose up -d postgres redis      # the backend tests use these
.venv/bin/pytest                          # backend, load generator, analysis
cd payment_mock && ../.venv/bin/pytest    # mock gateway
cd web && npm ci && npm run build         # frontend type-check and build
```

## Load generator

A scenario file (`loadgen/scenarios/*.yaml`) describes the sale, the gateway, how many users
arrive and when, and how they behave. A seed makes a run repeatable.

```bash
PYTHONPATH=loadgen .venv/bin/python -m loadgen.run loadgen/scenarios/smoke.yaml
```

This creates and starts the sale, launches the users, waits for the sale to end, and writes
`runs/<name>-<timestamp>/` with `scenario.yaml`, `trace.jsonl` (server events),
`client.jsonl` (what each user's requests saw), `consistency.json` and `summary.json`.
It exits 1 if any consistency count is not 0.

## Analysis

```bash
PYTHONPATH=analysis .venv/bin/python -m analysis.report runs/<run> [runs/<other> ...] \
    --labels a,b --out analysis/out/<name>
```

One run gives its figures (request rate, seat states, wait-time CDF, success by arrival decile)
and `summary.json`. Several runs add overlaid comparison figures and `summary.csv`.
Figures are saved as PNG (300 dpi) and PDF. Metrics: waiting times, allocation efficiency
(sold ratio, seat idle time, wasted holds), fairness (Kendall tau, inversions, Jain index) and
surge (peak and mean request rate, latency, errors).

## Experiments

Every experiment uses 500 seats, `hold_seconds=30` and a gateway with 10% failures and
300 ± 200 ms latency, unless the experiment varies it. Time is compressed so each run takes
a few minutes. The stack must be up first.

| Group | Question | Runs |
|---|---|---|
| `exp1` | Bucket against baseline under a spike | 1,000 users within 10 s: bucket (N=10, T=2) and baseline |
| `exp2` | Same release rate (5 seats/s), different granularity | N=1/T=0.2, N=10/T=2, N=50/T=10 |
| `exp3` | Release-rate sweep | 2, 5, 10, 25 seats/s (T=2) |
| `exp4` | Payment failure rate | 0%, 10%, 30% |
| `exp5` | Hold window | 10 s, 30 s, 60 s |
| `exp6` | Scale | 1,000, 5,000, 10,000 users (`poll_seconds` is 5 for 10,000 users, 2 otherwise, to keep the load generator itself from becoming the bottleneck) |

```bash
bash scripts/run_experiments.sh          # all groups
bash scripts/run_experiments.sh exp1     # one group
```

**Scale note.** The issue called for 5,000 users in `exp1`–`exp5`. A first `exp1` run at that size
took 30 minutes and the single-process load generator used a full CPU core, so client-side
latencies (p95 57–73 s) measured the laptop rather than the system. `exp1`–`exp5` therefore use
1,000 users; `exp6` is the scale test (1,000, 5,000 and 10,000 users) and shows where the laptop starts to saturate.

Raw runs go to `runs/` and figures to `analysis/out/<group>/` (both are git-ignored).

### Results on the development laptop

All 19 runs finished with every consistency count at 0 (no double bookings, no booking without a
successful payment, no charge without a booking or refund). Two runs did not sell out, for reasons
that are results rather than faults:

- `exp5_hold10`: sold 217 of 500 seats. With a 10 s hold and users who take 3–15 s to pay, many
  holds lapse, and the sale was stopped once every simulated user had finished.
- `exp6_users10000`: reached the 900 s cap with 492 of 500 seats sold. At 10,000 users the laptop
  (database, API and the single-process load generator all share it) is saturated.

Treat client-side latencies (`p95_latency_ms`, seconds in these runs) as a measure of the laptop
under load, not of the system on dedicated hardware; the generator and the stack compete for the
same CPU. Wait times, ordering (Kendall tau), seat allocation and consistency are unaffected.

## Layout

```
backend/       FastAPI app, worker, domain logic, tests
payment_mock/  mock payment gateway
web/           Vite + React + TypeScript + Tailwind + shadcn/ui
loadgen/       scenario-driven load generator
analysis/      metrics, figures and report CLI
scripts/       smoke.sh, run_experiments.sh
docs/          design and implementation plan
```
