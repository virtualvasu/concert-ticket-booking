# Concert Ticket Booking with a Leaky Bucket — Design

Date: 2026-11-14
Course: Computer System Design, Booking theme, Sub-theme 3 (Leaky Bucket-Based Booking)
Source requirements: `requirement-docs/Project_Themes.pdf` (pages 5–7)

## 1. Intent

Build a working concert ticket booking system where seats are released at a fixed rate
(a leaky bucket), users are served strictly in queue order, and a seat is confirmed only
after successful payment. Then measure it under realistic surges and compare it against
a no-bucket baseline.

Two end products:

1. **A live demo**, hosted on machines the team has access to. People open the site,
   join the queue, watch their position and the next-release countdown, and pay with a
   mock gateway. An admin dashboard shows the system working in real time.
2. **A 10-page report** built on measurements from the real system (not a separate
   model), covering waiting time, seat-allocation efficiency, fairness and surge
   smoothing, plus advantages and limitations.

Everything is developed on the laptop first; the same setup must run unchanged on the
hosting machines.

### What the evaluators look for (from the sheet)

- A working prototype or simulator, with **test cases** and **performance measurements**.
- Evaluation via "simulations, traces, and performance metrics".
- Sub-theme 3 objective: control booking demand over time, reduce sudden surges, and
  evaluate **waiting times**, **seat-allocation efficiency** and **fairness**.
- The common rule for sub-themes 1–3: a seat is confirmed **only after successful
  payment**; failed or incomplete payments never produce a confirmed booking.
- It's a comparative exercise: results get compared with sub-theme 1 (virtual queue) and
  sub-theme 2 (payment queue), so the metrics should be ones those teams can also report
  (throughput, time to sell out, wait times, failed-payment handling).

## 2. Goals, non-goals, anti-goals

**Goals**
- Correct leaky-bucket booking under heavy concurrent load.
- A baseline "free-for-all" mode on the same system, so the bucket's effect is measurable.
- Configurable bucket parameters (seats per tick, tick interval, hold window) so they can
  be swept in experiments.
- A reproducible load generator with scenario files and seeds; every run produces a trace.
- Analysis scripts that turn traces into the report's tables and figures.
- A demo that looks clean and is easy to follow live.

**Non-goals**
- Real payments, real authentication, user accounts with passwords.
- Users choosing their exact seat on a seat map.
- Several concerts on sale at the same time (the schema may hold more than one event, but
  a sale run is one event).
- Kafka, Kubernetes, microservice sprawl.
- Writing the report itself (the system produces the data and figures; the report is
  written afterwards).

**Anti-goals: failure even if everything else works**
- Any seat booked twice.
- Any booking confirmed without a successful payment.
- A charged payment that results in neither a booking nor a refund.
- Users overtaking each other in the queue in ways the policy doesn't explain.
- Report numbers that didn't come from runs of the actual system.

## 3. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Real web app (frontend, API, Redis, Postgres, mock payment gateway) plus a load generator that drives it | Most code, but the demo is real and the measurements test real concurrency and consistency |
| B | Discrete-event simulator in Python with a thin UI | Fast and reproducible at huge scale, but no real system to demo and no proof against double booking under real load |
| C | A and B together, sharing the policy code | Strongest analysis, roughly double the work for no gain the report needs |

We chose **A**.

## 4. How the booking works

### 4.1 Concepts

- **Event**: one concert with a fixed seat inventory (default 500 seats in sections and
  rows, configurable).
- **Seat states**: `LOCKED` (not yet released) → `AVAILABLE` (released by the bucket) →
  `HELD` (offered to a specific user, with an expiry) → `SOLD`. A `HELD` seat whose payment
  fails or whose hold expires goes back to `AVAILABLE`.
- **Queue**: users join with a display name and get a queue token. Order is by join time
  (ties broken by a server-assigned sequence number). One active queue entry per user.
- **Bucket tick**: every `T` seconds, `N` seats move from `LOCKED` to `AVAILABLE`.
  Available seats that are unused carry over to the next tick (they don't leak away).
- **Offer**: whenever there are `AVAILABLE` seats and users waiting, the user at the head of
  the queue is given an exclusive offer on one seat (`HELD`, expiring after `H` seconds).
  The seat is assigned by the system (best available: front sections first), and the user
  sees which seat it is.
- **Payment**: within the hold window the user pays through the mock gateway. Success
  before expiry → `SOLD` and a booking is written. Failure → the user may retry until the
  window ends (max retries configurable, default 2). On window expiry the seat returns to
  `AVAILABLE` and the user leaves the queue (they may rejoin at the back).
- **Late success**: a payment that succeeds at the gateway after the hold expired produces
  no booking and a recorded refund.
- **Eligibility**: a user is eligible for an offer if they are in the queue, have no active
  hold and haven't reached the per-user ticket limit (default 1).
- **Sale end**: the sale ends when every seat is `SOLD`, or at a configured end time. Users
  still waiting get a clear "sold out" result and are counted as rejected.

This interprets the sheet's "the seat is allocated to the first eligible user who
successfully completes payment for it" as: offers go out in queue order, one user per seat
at a time, and if that user fails the seat moves to the next eligible user. Making users
race for the same seat was rejected because it wastes payments and is less fair.

### 4.2 Baseline mode ("free-for-all")

Same system, bucket off: all seats become `AVAILABLE` at sale start, there is no queue,
and any user can request a hold directly. Whoever gets the atomic hold first gets the seat;
the payment and hold rules are the same. This produces the surge the bucket is meant to
prevent, so the two can be compared on the same metrics.

### 4.3 Correctness rules (must hold under any load)

- Taking a hold and confirming a sale are atomic, conditional state changes (only
  `AVAILABLE → HELD`; only `HELD by this user and not expired → SOLD`).
- Postgres has a unique constraint on (event, seat) for bookings. This is the final guard
  against double booking even if application logic is wrong.
- Payment requests carry an idempotency key; a repeated request can't charge twice.
- An expiry sweeper returns expired holds to `AVAILABLE` continuously, not just on tick.
- Every state change is written to an append-only event log (the trace) with timestamps.

## 5. Architecture

All of it runs under **Docker Compose**: one command on the laptop now, the same on the
hosting machines later.

| Service | Role |
|---|---|
| `web` | React (Vite) frontend built with **shadcn/ui** components (Tailwind), served by nginx; also reverse-proxies `/api` |
| `api` | FastAPI (Python): queue, offers, holds, payments, admin and metrics endpoints, live updates to browsers over WebSocket/SSE |
| `worker` | Bucket ticker, offer dispatcher, hold-expiry sweeper (kept separate from `api`, so the API can run more than one replica) |
| `payment` | Mock payment gateway: configurable latency distribution, failure rate and timeout rate |
| `redis` | Queue (ordered by join sequence), live counters, pub/sub for pushing updates |
| `postgres` | Source of truth for events, seats, holds, bookings, payments, refunds and the event log |

Stack choices: Python for
the backend because the load generator and analysis are Python too; React for the demo UI;
Redis + Postgres to show the split between the hot queue and durable bookings.

### Screens

- **Buyer page**: join the queue → see position, people ahead, next-release countdown and
  seats left → on your turn, see your seat and a hold timer → pay (the mock checkout lets
  the demoer force success or failure) → see the confirmation or the reason you lost the
  seat.
- **Admin dashboard**: set the mode (bucket or baseline), `N`, `T`, `H`, the payment
  failure rate and latency; start/reset a sale; live charts of queue length, seats by
  state, seats released vs sold over time, request rate, wait-time distribution; live
  counters for double bookings (always 0), refunds and expired holds.

## 6. Load generator and experiments

A Python asyncio client (`loadgen/`) drives the real API with simulated users.

**Scenario files** (YAML) define: the number of users, the arrival pattern (everyone at
sale open, Poisson trickle, several spikes), user behaviour (payment success probability,
probability of abandoning a hold, time taken to pay), bucket/baseline settings, and a
random seed. Each run stores its config together with the full event trace.

**Planned experiments** (the minimum set for the report):
1. Bucket vs baseline under a sale-open spike (e.g. 5,000 users for 500 seats).
2. Granularity sweep: same release rate `N/T`, different `N` and `T` (e.g. 1 seat/1s vs
   10 seats/10s vs 50 seats/50s).
3. Release-rate sweep: different `N/T`.
4. Payment failure rate sweep (0%, 10%, 30%).
5. Hold window sweep.
6. Scale: 1k / 5k / 10k users.

**Metrics** (computed by `analysis/` from traces):
- *Waiting time*: join → offer and join → confirmed (mean, p50, p95, p99, distribution).
- *Seat-allocation efficiency*: seats sold / total, time to sell out, seat idle time
  (released but not held), holds wasted through failure or expiry.
- *Fairness*: how well queue order is preserved (inversions / Kendall tau between join
  order and confirmation order), success probability by arrival time, Jain's index on
  waits for successful users.
- *Surge smoothing*: request rate at the API and at the payment gateway over time, peak to
  mean ratio, API p95 latency and error rate under load.
- *Consistency*: double bookings, bookings without successful payment, charged-but-unbooked
  payments that were not refunded (all must be 0, and are reported as such).
- *Rejections*: who was rejected, when and why (sold out, hold expired, payment failed).

The analysis produces publication-ready figures (matplotlib) and tables into
`analysis/out/`, ready to drop into the report.

## 7. Testing

- Unit tests on the state machine (every allowed and forbidden transition).
- Concurrency tests: many parallel holds and confirms on the same seats, asserting no
  double booking and no confirmation without payment.
- Integration tests through the API with the mock gateway forced into failure, timeout and
  late-success cases.
- A smoke run of the load generator in CI-style local scripts, asserting the consistency
  metrics are all zero.

## 8. Left to the builder

- Repository layout, module boundaries, exact API routes and schemas.
- Whether seat state lives in Postgres with conditional updates or in Redis with
  scripts, as long as section 4.3 holds and Postgres stays the source of truth for bookings.
- WebSocket vs SSE for live updates.
- Chart library for the dashboard (shadcn/ui's chart components, built on Recharts, are
  the natural fit); styling within shadcn/ui (it should look clean for the demo).

Decided: the UI uses **shadcn/ui** components throughout.
- Default seat map layout and section names.

## 9. Default decisions

- Seats are assigned by the system, not picked by the user.
- One ticket per user by default.
- Failed payments can be retried within the hold window (2 retries by default).
- Unused released seats carry over rather than leaking away.
- Python/FastAPI + React + Redis + Postgres + Docker Compose.
- No Kafka; one event on sale at a time.
