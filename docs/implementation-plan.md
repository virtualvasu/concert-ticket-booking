# Concert Booking with a Leaky Bucket — Implementation Plan

> Work through the tasks in order (or in parallel where dependencies allow). Each task follows test-first steps; tick the checkboxes (`- [ ]`) as you go.

**Goal:** A concert ticket booking system that releases seats with a leaky bucket, serves users in queue order, confirms seats only after successful payment, and can be load-tested and measured against a no-bucket baseline.

**Architecture:** FastAPI `api` (stateless, can run several workers) and a single `worker` process share Postgres (source of truth for seats, holds, payments, bookings, and the event trace) and Redis (the waiting queue and request-rate counters). A separate mock payment gateway simulates latency and failures. A React + shadcn/ui frontend (buyer page and admin dashboard) is served by nginx. A Python load generator drives the real API and an analysis package turns traces into report figures.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async) + asyncpg, redis-py (asyncio), pydantic-settings, httpx, pytest + pytest-asyncio; React + TypeScript + Vite + Tailwind + shadcn/ui (charts via shadcn chart / Recharts), react-router; pandas, matplotlib, scipy; Postgres 16, Redis 7, Docker Compose, nginx.

**Design:** `docs/design.md`

## Global Constraints

- A seat is confirmed only after a successful payment; a failed or incomplete payment never produces a booking.
- No seat is ever booked twice: `bookings` has `UNIQUE (sale_id, seat_id)`.
- A charged payment ends as either a booking or a recorded refund, never neither.
- Payment requests carry an idempotency key; repeating it never charges twice.
- Seat states: `LOCKED → AVAILABLE → HELD → SOLD`; `HELD → AVAILABLE` on failed payment or expiry.
- Released-but-unused seats carry over to the next tick.
- Offers go out strictly in queue order (join sequence), one exclusive seat per user.
- Defaults: 500 seats (VIP 2×25 at ₹5000, Gold 6×25 at ₹3000, Silver 12×25 at ₹1500), `seats_per_tick=10`, `tick_seconds=10`, `hold_seconds=60`, `max_payment_attempts=3` (1 try + 2 retries), `tickets_per_user=1`.
- Seats are assigned best-available by `rank` (VIP first, then Gold, then Silver; row ascending; number ascending).
- Two modes on one system: `bucket` and `baseline` (all seats AVAILABLE at start, no queue, users grab directly).
- Every state change is written to the `events` table with a server timestamp (UTC).
- All UI is built from shadcn/ui components.
- Everything runs with `docker compose up --build`; no host dependencies besides Docker for the deployed system.
- No Kafka, no real payments, no passwords. Admin endpoints are protected by an `X-Admin-Token` header (`ADMIN_TOKEN` env, default `admin`).

## Review Focus

1. **Payment succeeds at the gateway right as the hold expires** — expect: no booking, a refund recorded, the seat offered to the next user. Test: Task 7 `test_late_success_is_refunded`.
2. **Double-clicked Pay / client retry with the same idempotency key** — expect exactly one charge and the same result returned. Test: Task 7 `test_pay_is_idempotent`.
3. **Buyer refreshes or reopens the tab mid-offer** — expect `/api/me` to return the live offer with remaining time. Test: Task 7 `test_me_restores_offer`; frontend keeps `user_id` in localStorage (Task 10).
4. **Same user joins twice / joins after being booked** — expect 409 and the queue unchanged. Test: Task 7 `test_join_twice_conflicts`.
5. **Admin stops a sale with active holds and waiting users** — expect holds expired, seats not sold, waiting users rejected with `sale_stopped`. Test: Task 5 `test_stop_sale_rejects_waiting_and_expires_holds`.

---

## File Structure

```
docker-compose.yml               all services; dev ports: postgres 55432, redis 56379, api 8000, payment 8001, web 8080
.env.example
requirements-dev.txt             union of all Python deps + pytest tools, for the local .venv
pytest.ini                       asyncio_mode=auto, testpaths
backend/
  Dockerfile                     one image; command picks api or worker
  requirements.txt
  app/
    config.py                    Settings
    db.py                        engine, session factory, init_db
    models.py                    SQLAlchemy tables
    schemas.py                   pydantic: SaleConfig, SectionSpec, API models
    trace.py                     record()
    redis_client.py              get_redis()
    domain/
      sale.py                    create/start/stop/end sale, get_current_sale, seat counts
      seats.py                   release_seats, hold_best_available, confirm_hold, fail_hold, expire_holds
      queue.py                   QueueStore (Redis sorted set)
      users.py                   join_sale
      dispatcher.py              dispatch_offers, run_tick
    payments/
      gateway.py                 GatewayClient
      service.py                 pay()
    api/
      deps.py                    session, redis, gateway, current user, admin guard
      buyer.py                   /api/sale, /api/queue/join, /api/me, /api/pay, /api/baseline/grab
      admin.py                   /api/admin/*
      stats.py                   stats + time series queries
    main.py                      FastAPI app, rps middleware
    worker.py                    tick loop
  tests/
    conftest.py
    test_health.py test_sale.py test_seats.py test_queue.py test_dispatcher.py
    test_payments.py test_buyer_api.py test_admin_api.py
payment_mock/
  Dockerfile requirements.txt app.py
  tests/test_gateway.py
web/                             Vite + React + TS + Tailwind + shadcn/ui
  nginx.conf Dockerfile
  src/lib/api.ts src/hooks/usePoll.ts
  src/pages/BuyerPage.tsx src/pages/AdminPage.tsx
  src/components/buyer/{JoinCard,WaitingCard,OfferCard,ResultCard,BaselineCard}.tsx
  src/components/admin/{SaleConfigForm,StatsCards,LiveCharts,ConsistencyPanel,GatewayForm}.tsx
loadgen/
  loadgen/{scenario.py,arrivals.py,user.py,clientlog.py,run.py}
  scenarios/*.yaml
  tests/test_arrivals.py
analysis/
  analysis/{load.py,metrics.py,figures.py,report.py}
  tests/test_metrics.py
scripts/
  smoke.sh run_experiments.sh
README.md
```

---

### Task 1: Infrastructure, backend skeleton, test harness

**Files:**
- Create: `docker-compose.yml` (only `postgres` and `redis` for now), `.env.example`, `.gitignore`, `requirements-dev.txt`, `pytest.ini`, `backend/requirements.txt`, `backend/app/{__init__,config,db,redis_client,main}.py`, `backend/tests/conftest.py`, `backend/tests/test_health.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings): `database_url: str`, `redis_url: str`, `payment_url: str = "http://payment:8001"`, `admin_token: str = "admin"`, `worker_interval_ms: int = 200`; `get_settings() -> Settings` (lru_cache).
  - `db.engine`, `db.SessionLocal: async_sessionmaker[AsyncSession]`, `async def init_db() -> None` (`Base.metadata.create_all`), `class Base(DeclarativeBase)`.
  - `redis_client.get_redis() -> redis.asyncio.Redis` (decode_responses=True).
  - `main.app: FastAPI`; `GET /api/health -> {"status": "ok", "db": true, "redis": true}`.
  - conftest fixtures: `session` (AsyncSession, tables truncated before each test), `redis` (db 15, flushed before each test), `client` (`httpx.AsyncClient` on `ASGITransport(app)` with dependency overrides to the test session/redis). Tests read `TEST_DATABASE_URL` (default `postgresql+asyncpg://booking:booking@localhost:55432/booking_test`) and `TEST_REDIS_URL` (default `redis://localhost:56379/15`).

- [ ] **Step 1:** Write `docker-compose.yml` with `postgres:16` (user/password/db `booking`, an init SQL that also creates `booking_test`, port `55432:5432`) and `redis:7` (port `56379:6379`), both with healthchecks. Create `.venv`, install `requirements-dev.txt`. Run `docker compose up -d postgres redis`. Expected: both healthy in `docker compose ps`.
- [ ] **Step 2: Write the failing test** `test_health_ok`: `GET /api/health` → 200 and body `{"status": "ok", "db": True, "redis": True}`.
- [ ] **Step 3:** Run `pytest backend/tests/test_health.py -v`. Expected: FAIL (no app).
- [ ] **Step 4:** Implement config, db, redis_client, main with the health route (runs `SELECT 1` and `PING`).
- [ ] **Step 5:** Run the test. Expected: PASS.
- [ ] **Step 6: Commit** `chore: scaffold backend, compose infra and test harness`.

### Task 2: Data model, sale creation, trace

**Files:**
- Create: `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/trace.py`, `backend/app/domain/__init__.py`, `backend/app/domain/sale.py`, `backend/tests/test_sale.py`

**Interfaces:**
- Produces (tables; ids are `int` identity except `users.id` which is `uuid str`):
  - `Sale`: id, mode (`"bucket"|"baseline"`), status (`"idle"|"running"|"ended"`), config (JSONB, the `SaleConfig` dump), seats_per_tick, tick_seconds (float), hold_seconds (float), max_payment_attempts, tickets_per_user, label, created_at, started_at, ended_at, next_tick_at, end_reason (`"sold_out"|"stopped"|None`).
  - `Seat`: id, sale_id, section, row, number, price, rank, status (`LOCKED|AVAILABLE|HELD|SOLD`), released_at, sold_at. Index on (sale_id, status, rank).
  - `User`: id, sale_id, name, joined_at, queue_seq (nullable), state (`waiting|offered|booked|left|rejected|browsing`), reason (nullable: `hold_expired|payment_failed|sold_out|sale_stopped`).
  - `Hold`: id, sale_id, seat_id, user_id, created_at, expires_at, status (`active|paid|expired|failed`), attempts (int, 0).
  - `Payment`: id, sale_id, hold_id, user_id, idempotency_key (UNIQUE), amount, status (`pending|succeeded|failed|refunded`), gateway_ref, created_at, finished_at.
  - `Booking`: id, sale_id, seat_id, user_id, hold_id, payment_id, created_at; `UNIQUE(sale_id, seat_id)`.
  - `Event`: id, sale_id, ts, type, user_id, seat_id, data (JSONB). Index (sale_id, ts).
  - `SectionSpec(name: str, rows: int, seats_per_row: int, price: int)`; `SaleConfig` with the defaults from Global Constraints, `mode: Literal["bucket","baseline"] = "bucket"`, `label: str = ""`, validators `seats_per_tick >= 1`, `tick_seconds > 0`, `hold_seconds > 0`, `max_payment_attempts >= 1`.
  - `async def record(session, sale_id: int, type: str, ts: datetime, user_id: str | None = None, seat_id: int | None = None, **data) -> None` (adds, does not commit).
  - `async def create_sale(session, cfg: SaleConfig, now: datetime) -> Sale` — seats all LOCKED, rank 1..total in section order then row then number.
  - `async def get_current_sale(session) -> Sale | None` — most recently created sale.
  - `async def seat_counts(session, sale_id) -> dict[str, int]` — keys `LOCKED, AVAILABLE, HELD, SOLD, total`.

- [ ] **Step 1: Write failing tests:**
  - `test_create_sale_builds_default_seat_map`: counts == `{"LOCKED": 500, "AVAILABLE": 0, "HELD": 0, "SOLD": 0, "total": 500}`; the rank-1 seat is `("VIP", 1, 1)`, rank 51 is `("Gold", 1, 1)`, rank 500 is `("Silver", 12, 25)`; status `"idle"`.
  - `test_sale_config_rejects_zero_tick`: `SaleConfig(tick_seconds=0)` raises `ValidationError`.
  - `test_bookings_unique_per_seat`: inserting two `Booking` rows for the same (sale_id, seat_id) raises `IntegrityError`.
- [ ] **Step 2:** Run `pytest backend/tests/test_sale.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement models, schemas, trace, `create_sale`, `get_current_sale`, `seat_counts`. Add model import to `init_db` so tables are created.
- [ ] **Step 4:** Run tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: data model, sale creation and event trace`.

### Task 3: Seat state machine

**Files:**
- Create: `backend/app/domain/seats.py`, `backend/tests/test_seats.py`

**Interfaces:**
- Consumes: models, `record` (Task 2).
- Produces (each runs and commits its own transaction; all transitions are conditional `UPDATE ... WHERE status=... RETURNING`, seat picking uses `FOR UPDATE SKIP LOCKED`):
  - `release_seats(session, sale_id, n, now) -> list[int]` — the `n` lowest-rank LOCKED seats → AVAILABLE; event `seats_released` with `seat_ids`, `count`.
  - `hold_best_available(session, sale, user_id, now) -> Hold | None` — lowest-rank AVAILABLE seat → HELD, Hold with `expires_at = now + hold_seconds`; user state → `offered`; event `offer_made` (seat_id, hold_id, expires_at). None if no AVAILABLE seat.
  - `confirm_hold(session, hold_id, payment_id, now) -> Booking | None` — only if hold `active` and `expires_at > now`: hold `paid`, seat `SOLD` (sold_at), Booking inserted, user `booked`; event `booking_confirmed`. Otherwise None and nothing changes.
  - `fail_hold(session, hold_id, now, reason: str) -> bool` — active hold → `failed`, seat → AVAILABLE, user → `left` with `reason`; event `hold_failed`.
  - `expire_holds(session, sale_id, now) -> list[int]` — all active holds with `expires_at <= now` → `expired`, seats → AVAILABLE, users → `left`/`hold_expired`; event `hold_expired` per hold. Returns hold ids.

- [ ] **Step 1: Write failing tests** (sale from `create_sale`, `now` a fixed UTC datetime):
  - `test_release_takes_lowest_rank_first`: `release_seats(n=10)` returns ranks 1..10; counts AVAILABLE 10, LOCKED 490.
  - `test_release_more_than_locked_releases_remaining`: after releasing 495, `release_seats(n=10)` returns 5 ids.
  - `test_hold_best_available_takes_rank_order`: after releasing 3, two holds get rank 1 then rank 2; third user gets rank 3; fourth gets None.
  - `test_confirm_after_expiry_fails`: hold with hold_seconds=60, `confirm_hold(now + 61s)` → None; seat still HELD; no booking.
  - `test_confirm_twice_books_once`: first confirm returns Booking, second returns None; one row in bookings.
  - `test_fail_hold_returns_seat`: after `fail_hold`, seat AVAILABLE, user state `left`, reason `payment_failed`.
  - `test_expire_holds`: two holds, one expired at `now + 61s`; `expire_holds` returns one id; that seat AVAILABLE; other still HELD.
  - `test_concurrent_holds_never_share_a_seat`: release 10; 200 concurrent `hold_best_available` calls (each in its own session via `asyncio.gather`) → exactly 10 non-None holds with 10 distinct seat ids.
  - `test_concurrent_confirms_book_once`: 20 concurrent `confirm_hold` on the same hold (own sessions) → exactly 1 Booking.
- [ ] **Step 2:** Run `pytest backend/tests/test_seats.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `seats.py`.
- [ ] **Step 4:** Run tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: seat state machine with atomic holds and confirmation`.

### Task 4: Queue store and joining

**Files:**
- Create: `backend/app/domain/queue.py`, `backend/app/domain/users.py`, `backend/tests/test_queue.py`

**Interfaces:**
- Produces:
  - `class QueueStore(redis, sale_id: int)` using sorted set `queue:{sale_id}` scored by sequence from `INCR queue_seq:{sale_id}`:
    `async join(user_id) -> int` (seq; raises `AlreadyQueued` if present), `async position(user_id) -> int | None` (1-based via ZRANK), `async length() -> int`, `async pop_head() -> tuple[str, int] | None` (ZPOPMIN), `async restore(user_id, seq) -> None` (ZADD with original score), `async remove(user_id) -> None`, `async drain() -> list[str]` (removes and returns all, in order).
  - `class AlreadyJoined(Exception)`.
  - `async def join_sale(session, redis, sale: Sale, name: str, now) -> User` — bucket mode: creates User with `queue_seq` and state `waiting`, pushes to queue; baseline mode: state `browsing`, no queue entry. Event `user_joined` (data: name, seq). Raises `ValueError("sale not running")` unless sale status `running`.

- [ ] **Step 1: Write failing tests:**
  - `test_queue_order_and_position`: join a, b, c → positions 1, 2, 3; `pop_head()` → `("a", 1)`; b now position 1.
  - `test_join_twice_raises`: second `join("a")` raises `AlreadyQueued`; length stays 1.
  - `test_restore_keeps_original_place`: pop a, restore a with its seq → a position 1 again.
  - `test_join_sale_bucket_queues_user` / `test_join_sale_baseline_does_not_queue`: states `waiting` / `browsing`; queue length 1 / 0; a `user_joined` event exists.
  - `test_join_sale_requires_running`: idle sale → `ValueError`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: redis-backed queue and joining a sale`.

### Task 5: Sale lifecycle, dispatcher and worker tick

**Files:**
- Modify: `backend/app/domain/sale.py` (add start/stop/end)
- Create: `backend/app/domain/dispatcher.py`, `backend/app/worker.py`, `backend/tests/test_dispatcher.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `start_sale(session, sale_id, now) -> Sale` — status `running`, `started_at=now`; bucket: `next_tick_at = now` (first release is immediate); baseline: releases all seats now. Event `sale_started`. Raises `ValueError` if another sale is running.
  - `stop_sale(session, redis, sale_id, now) -> Sale` — expires all active holds (reason `sale_stopped`), drains queue, waiting users → `rejected`/`sale_stopped`, status `ended`, `end_reason="stopped"`. Event `sale_ended`.
  - `end_if_sold_out(session, redis, sale, now) -> bool` — if SOLD == total: drain queue, waiting users → `rejected`/`sold_out` (event `user_rejected` each), status `ended`, `end_reason="sold_out"`.
  - `dispatch_offers(session, queue, sale, now) -> int` — while AVAILABLE seats exist and the queue is non-empty: `pop_head`; skip users whose state isn't `waiting`; `hold_best_available`; if it returns None, `restore` the user and stop. Returns offers made.
  - `run_tick(session, redis, now) -> dict` — for the current running sale: bucket and `now >= next_tick_at` → `release_seats(seats_per_tick)` and advance `next_tick_at += tick_seconds` (loop until in the future, so a stalled worker catches up without bursting more than the missed ticks); then `expire_holds`; then (bucket only) `dispatch_offers`; then `end_if_sold_out`. Returns `{"released": int, "expired": int, "offers": int, "ended": bool}`.
  - `worker.main()` — loop `run_tick(datetime.now(UTC))` every `worker_interval_ms`; logs and continues on exceptions. Run as `python -m app.worker`.

- [ ] **Step 1: Write failing tests** (config `seats_per_tick=10, tick_seconds=10, hold_seconds=30`, 50 seats via one section `SectionSpec("GA", 2, 25, 1000)`):
  - `test_first_tick_releases_immediately`: start at t0, `run_tick(t0)` → released 10; `run_tick(t0+5s)` → released 0; `run_tick(t0+10s)` → released 10.
  - `test_unused_seats_carry_over`: no users; ticks at t0, t0+10 → AVAILABLE 20.
  - `test_offers_follow_queue_order`: 15 users joined in order u0..u14; `run_tick(t0)` → offers 10; holds belong to u0..u9 with seat ranks 1..10 in that order; u10 position 1.
  - `test_expired_hold_goes_to_next_user`: config `tick_seconds=100` (so no second release interferes); u0..u10 joined; `run_tick(t0)` → 10 offers to u0..u9; `run_tick(t0+31s)` → expired 10, offers 1 (to u10, seat rank 1), AVAILABLE 9, u0..u9 state `left`/`hold_expired`.
  - `test_sold_out_rejects_waiting`: 50 seats, 60 users; simulate confirming every hold as it is offered across ticks until sold; final `run_tick` → `ended` True, sale `end_reason == "sold_out"`, 10 users `rejected`/`sold_out`.
  - `test_baseline_releases_all_and_never_dispatches`: baseline sale start → AVAILABLE 50; `run_tick` → offers 0.
  - `test_stop_sale_rejects_waiting_and_expires_holds` (Review Focus 5).
  - `test_start_second_running_sale_fails`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: leaky bucket tick, dispatcher and worker loop`.

### Task 6: Mock payment gateway

**Files:**
- Create: `payment_mock/{app.py,requirements.txt,Dockerfile}`, `payment_mock/tests/test_gateway.py`

**Interfaces:**
- Produces (FastAPI on port 8001, in-memory state):
  - `GatewayConfig(failure_rate: float = 0.1, latency_ms_mean: int = 300, latency_ms_jitter: int = 200, seed: int | None = None)`.
  - `POST /charge {idempotency_key: str, amount: int, force: "success"|"fail"|null}` → `{"status": "succeeded"|"failed", "ref": str}` after sleeping `max(0, uniform(mean-jitter, mean+jitter))` ms. Same key → same stored response, no new sleep.
  - `POST /refund {ref}` → `{"status": "refunded"}`; unknown ref → 404.
  - `GET /config`, `PUT /config` (replaces config, reseeds RNG if seed set), `GET /stats` → `{"charges": int, "succeeded": int, "failed": int, "refunds": int}`.

- [ ] **Step 1: Write failing tests** (latency set to 0 via PUT /config):
  - `test_force_success_and_fail`, `test_idempotent_charge` (two calls same key → same ref, `stats.charges == 1`), `test_failure_rate_one_always_fails`, `test_refund_unknown_ref_404`, `test_seeded_outcomes_repeat` (same seed, failure_rate 0.5, 20 charges with distinct keys → identical status sequence across two config resets).
- [ ] **Step 2:** Run `pytest payment_mock/tests -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: mock payment gateway`.

### Task 7: Payment service and buyer API

**Files:**
- Create: `backend/app/payments/{__init__,gateway,service}.py`, `backend/app/api/{__init__,deps,buyer}.py`, `backend/tests/test_payments.py`, `backend/tests/test_buyer_api.py`
- Modify: `backend/app/main.py` (include router, rps middleware)

**Interfaces:**
- Consumes: Tasks 2–6.
- Produces:
  - `GatewayClient(base_url)`: `async charge(idempotency_key, amount, force) -> ChargeResult(status, ref)`, `async refund(ref) -> None`, `async get_config() -> dict`, `async set_config(cfg: dict) -> dict`. Dependency `get_gateway()`; tests override it with a `FakeGateway` whose next result and an optional `on_charge` callback (used to advance the clock mid-charge) are set per test.
  - `PayResult(status: Literal["confirmed","failed_retry","failed_final","expired_refunded","expired"], booking_id: int | None, attempts_left: int)`.
  - `async def pay(session, gateway, user_id, hold_id, idempotency_key, force, now_fn: Callable[[], datetime]) -> PayResult`:
    1. Existing Payment with this key → return the result derived from it (no gateway call).
    2. Hold must belong to user (else `PermissionError`), be `active` and unexpired at `now_fn()` (else `expired`), and have `attempts < max_payment_attempts`.
    3. Insert Payment `pending`, `attempts += 1`, commit; event `payment_attempt`.
    4. Charge. Failed → Payment `failed`; if attempts reached max → `fail_hold(reason="payment_failed")` → `failed_final`, else `failed_retry`.
    5. Succeeded → Payment `succeeded`; `confirm_hold(now_fn())`; None → `gateway.refund`, Payment `refunded`, event `refund_issued` → `expired_refunded`.
  - Routes (user identified by `X-User-Id` header; unknown id → 401):
    - `GET /api/sale` → `{sale_id, mode, status, end_reason, seats: {LOCKED, AVAILABLE, HELD, SOLD, total}, seats_per_tick, tick_seconds, hold_seconds, next_tick_at, queue_length, server_time}`; 404 if no sale.
    - `POST /api/queue/join {name}` → `{user_id, position}`; 409 if not running. Rejoining after `left` is done with a new join (new user id).
    - `GET /api/me` → `{user_id, name, state, reason, position, ahead, offer: {hold_id, seat: {section,row,number,price}, expires_at, attempts_left} | null, booking: {booking_id, seat, created_at} | null, server_time}`.
    - `POST /api/pay {hold_id, idempotency_key, force?}` → `PayResult`; 403 on someone else's hold.
    - `POST /api/baseline/grab` → offer object as in `/api/me`; 409 `{"detail": "no_seats"}` if none (event `grab_failed`), 409 if not baseline mode or user already holds/booked.
  - Middleware: `INCR rps:{epoch_second}` with 300s expiry for every `/api/` request except `/api/admin/*`.

- [ ] **Step 1: Write failing tests** in `test_payments.py`:
  - `test_success_confirms_booking`: status `confirmed`, seat SOLD, user `booked`.
  - `test_failure_allows_retries_then_releases`: three forced failures → `failed_retry` (attempts_left 2), `failed_retry` (1), `failed_final` (0); seat AVAILABLE; user `left`/`payment_failed`.
  - `test_late_success_is_refunded` (Review Focus 1): `on_charge` advances the clock past `expires_at` → `expired_refunded`; Payment `refunded`; no booking; seat AVAILABLE (expired by `expire_holds` in a following tick).
  - `test_pay_is_idempotent` (Review Focus 2): same key twice → same result, one Payment row, `FakeGateway.charges == 1`.
  - `test_pay_someone_elses_hold_forbidden`.
- [ ] **Step 2: Write failing tests** in `test_buyer_api.py` (through `client`):
  - `test_join_and_me_shows_position`; `test_join_twice_conflicts` (Review Focus 4: same `X-User-Id` header on a second join → 409; queue length unchanged); `test_me_restores_offer` (Review Focus 3: after a tick, `/api/me` returns `offer` with `expires_at`, and again on a second call); `test_pay_endpoint_confirms`; `test_baseline_grab_until_no_seats` (3 seats, 4 users → 3 offers, 4th gets 409 `no_seats`); `test_request_counter_increments`.
- [ ] **Step 3:** Run `pytest backend/tests/test_payments.py backend/tests/test_buyer_api.py -v`. Expected: FAIL.
- [ ] **Step 4:** Implement.
- [ ] **Step 5:** Run all backend tests. Expected: PASS.
- [ ] **Step 6: Commit** `feat: payment flow and buyer API`.

### Task 8: Admin API, stats and trace export

**Files:**
- Create: `backend/app/api/admin.py`, `backend/app/api/stats.py`, `backend/tests/test_admin_api.py`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces (all require `X-Admin-Token`; wrong/missing → 401):
  - `POST /api/admin/sales` (SaleConfig) → sale summary; `POST /api/admin/sales/{id}/start`; `POST /api/admin/sales/{id}/stop`.
  - `GET /api/admin/stats?window=120` → `{sale: <as /api/sale>, counts: {joined, waiting, offered, booked, left, rejected}, holds: {active, paid, expired, failed}, payments: {succeeded, failed, refunded}, wait_seconds: {p50, p95, max} (join→booking), series: [{t: epoch_second, rps, joins, offers, bookings, payment_attempts, expired}] }` for the last `window` seconds (series from `events` grouped by second plus `rps:*` keys).
  - `GET /api/admin/sales/{id}/trace` → `application/x-ndjson`, one event per line `{ts, type, user_id, seat_id, data}` ordered by id.
  - `GET /api/admin/sales/{id}/consistency` → `{double_bookings, bookings_without_successful_payment, charged_without_booking_or_refund, sold_seats_without_booking}` (all SQL counts).
  - `GET /api/admin/gateway`, `PUT /api/admin/gateway` → proxy to `GatewayClient`.

- [ ] **Step 1: Write failing tests:** `test_admin_requires_token`; `test_create_start_stop_flow`; `test_stats_shape_and_counts` (2 joins, 1 tick, 1 paid → counts.booked 1, series non-empty); `test_trace_is_ndjson_in_order`; `test_consistency_all_zero_after_normal_sale`; `test_consistency_detects_unrefunded_charge` (insert a `succeeded` Payment with no booking → `charged_without_booking_or_refund == 1`).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run all backend tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: admin API, live stats, trace export and consistency checks`.

### Task 9: Full Docker Compose stack

**Files:**
- Create: `backend/Dockerfile`, `scripts/smoke.sh`
- Modify: `docker-compose.yml` (add `api` with `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4`, `worker` with `python -m app.worker`, `payment`; env from `.env`; `depends_on` with healthchecks), `.env.example`

- [ ] **Step 1:** Write `scripts/smoke.sh`: `docker compose up -d --build`, wait for `/api/health`, create a 20-seat bucket sale (`seats_per_tick=5, tick_seconds=2`), start it, join 3 users, poll `/api/me` until each has an offer, pay each with `force=success`, assert 3 `booked`, assert every consistency count is 0, print `SMOKE OK`.
- [ ] **Step 2:** Run `bash scripts/smoke.sh`. Expected: FAIL (services missing).
- [ ] **Step 3:** Add Dockerfiles and services.
- [ ] **Step 4:** Run `bash scripts/smoke.sh`. Expected: `SMOKE OK`.
- [ ] **Step 5: Commit** `feat: dockerized api, worker and payment gateway`.

### Task 10: Frontend scaffold and buyer page

**Files:**
- Create: `web/` via Vite `react-ts` template; Tailwind + shadcn/ui initialised per the current shadcn Vite guide (check the docs: `npx shadcn@latest init`); add components `button card input badge progress alert separator sonner skeleton tabs table label select slider switch chart`.
- Create: `web/src/lib/api.ts`, `web/src/hooks/usePoll.ts`, `web/src/pages/BuyerPage.tsx`, `web/src/components/buyer/{JoinCard,WaitingCard,OfferCard,ResultCard,BaselineCard}.tsx`, `web/src/App.tsx` (routes `/` and `/admin`, header with nav), `web/vite.config.ts` (dev proxy `/api` → `http://localhost:8000`), `web/nginx.conf`, `web/Dockerfile`
- Modify: `docker-compose.yml` (add `web` on `8080:80`)

**Interfaces:**
- Produces: `api.ts` typed functions mirroring Task 7/8 routes (`getSale`, `join`, `getMe`, `pay`, `grab`, admin functions taking the token). `usePoll<T>(fn, intervalMs, deps) -> {data, error, refresh}`. User id stored under `localStorage["concert.userId"]` (reads/writes wrapped in try/catch; the page works without it).

Buyer page behaviour (one card at a time, driven by `/api/me` polled every 1s, `/api/sale` every 2s):
- **JoinCard**: concert name and date, seats left, name input, "Join the queue" (or "Enter sale" in baseline mode). Disabled with a message when the sale isn't running.
- **WaitingCard**: big position number, "N people ahead", countdown to the next release (`next_tick_at - server_time`, corrected for clock skew), `Progress` of seats sold, seats released per tick.
- **OfferCard**: seat (section, row, number, price), countdown `Progress` for the hold, attempts left, "Pay ₹price" button, and a small "demo" `Switch` to force the payment to fail. One idempotency key per click (reuse it if the request errored at network level).
- **ResultCard**: booked → ticket-style confirmation; left/rejected → the reason in plain words (`hold_expired`: "Your 60 s to pay ran out", `payment_failed`, `sold_out`, `sale_stopped`) and "Rejoin at the back".
- **BaselineCard**: "Grab a seat" button; on 409 `no_seats` show "No seats free right now" toast.

- [ ] **Step 1:** Scaffold, add shadcn components, run `npm run build`. Expected: success.
- [ ] **Step 2:** Implement api, hook, page and cards.
- [ ] **Step 3:** Run `npm run build` and `npx tsc --noEmit`. Expected: no errors.
- [ ] **Step 4:** With the stack up and `npm run dev`, create and start a sale via curl; in a browser join, wait, pay successfully in one tab and force-fail three times in another; refresh mid-offer (offer still shown). Take screenshots of each card. Expected: all states render and match `/api/me`.
- [ ] **Step 5:** `docker compose up -d --build web`; open `http://localhost:8080`. Expected: same page served by nginx, `/api` proxied.
- [ ] **Step 6: Commit** `feat: buyer page with shadcn/ui`.

### Task 11: Admin dashboard

**Files:**
- Create: `web/src/pages/AdminPage.tsx`, `web/src/components/admin/{SaleConfigForm,StatsCards,LiveCharts,ConsistencyPanel,GatewayForm}.tsx`

Behaviour:
- Token prompt (stored in localStorage, try/catch); 401 → prompt again.
- **SaleConfigForm**: mode `Select`, seats per tick, tick seconds, hold seconds, max attempts, label; "Create & start" and "Stop sale" buttons.
- **GatewayForm**: failure rate `Slider` (0–100%), latency mean/jitter inputs; saves via `PUT /api/admin/gateway`.
- **StatsCards**: seats by state, queue length, booked, expired, refunds, wait p50/p95, next release countdown.
- **LiveCharts** (shadcn chart): stacked area of seat states over time (client-side accumulated from polling), line of rps and payment attempts per second, bar of offers/bookings per second.
- **ConsistencyPanel**: the four consistency counts as green badges at 0, red otherwise; polled every 5s.
- Stats polled every 1s.

- [ ] **Step 1:** Implement.
- [ ] **Step 2:** `npm run build`, `npx tsc --noEmit`. Expected: no errors.
- [ ] **Step 3:** With the stack running, start a sale from the dashboard, run the browser buyer flow plus `scripts/smoke.sh`-style joins; verify charts move and consistency stays green. Screenshot.
- [ ] **Step 4: Commit** `feat: admin dashboard with live charts`.

### Task 12: Load generator

**Files:**
- Create: `loadgen/loadgen/{__init__,scenario,arrivals,user,clientlog,run}.py`, `loadgen/scenarios/{smoke,spike_bucket,spike_baseline}.yaml`, `loadgen/tests/test_arrivals.py`

**Interfaces:**
- Produces:
  - `Scenario` (pydantic, loaded from YAML): `name`, `seed: int`, `base_url = "http://localhost:8080"`, `admin_token = "admin"`, `users: int`, `arrival: Arrival`, `behavior: Behavior`, `sale: SaleConfig-shaped dict`, `gateway: dict`, `max_duration_s: float = 1800`.
  - `Arrival(kind: Literal["spike","poisson","waves"], spread_s: float = 0, rate_per_s: float | None = None, waves: list[{at_s: float, users: int, spread_s: float}] = [])`.
  - `Behavior(poll_seconds: float = 2, pay_delay_s: tuple[float, float] = (3, 15), abandon_prob: float = 0.05, max_concurrency: int = 2000)`.
  - `arrival_times(arrival, users, rng: random.Random) -> list[float]` — sorted offsets in seconds: spike = uniform in `[0, spread_s]`; poisson = cumulative exponential gaps at `rate_per_s`; waves = each wave's users uniform in `[at_s, at_s + spread_s]`, total must equal `users`.
  - `simulate_user(client, idx, scenario, rng, log)`: join (bucket) → poll `/api/me` every `poll_seconds` → on offer, with `abandon_prob` do nothing, else sleep a sampled pay delay and pay (retry on `failed_retry` after 1–3s); baseline: retry `grab` every `poll_seconds` until a hold or the sale ends. Stops when state is terminal or the sale ends.
  - `ClientLog.write(ts, user_idx, endpoint, status_code, latency_ms)` → `client.jsonl`.
  - CLI `python -m loadgen.run <scenario.yaml> [--out runs]` → creates the sale, sets the gateway, starts the sale, launches users at their arrival times, waits for `sale.status == "ended"` (or stops it at `max_duration_s`), downloads the trace and consistency, writes `runs/<name>-<YYYYmmdd-HHMMSS>/{scenario.yaml, trace.jsonl, client.jsonl, consistency.json, summary.json}`. Exits 1 if any consistency count is non-zero.

- [ ] **Step 1: Write failing tests:** `test_spike_within_spread`, `test_poisson_mean_rate` (10,000 arrivals at 50/s → mean gap within 5% of 0.02s), `test_waves_total_and_ranges`, `test_same_seed_same_times`.
- [ ] **Step 2:** Run `pytest loadgen/tests -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run tests. Expected: PASS.
- [ ] **Step 5:** Run `python -m loadgen.run loadgen/scenarios/smoke.yaml` (200 users, 50 seats, `tick_seconds=2`, `hold_seconds=15`). Expected: sale ends sold out, exit code 0, run directory written.
- [ ] **Step 6: Commit** `feat: scenario-driven load generator`.

### Task 13: Analysis and report figures

**Files:**
- Create: `analysis/analysis/{__init__,load,metrics,figures,report}.py`, `analysis/tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `load_run(run_dir) -> Run(trace: DataFrame, client: DataFrame, scenario: dict, consistency: dict)` (`ts` parsed to datetime, `t` = seconds since `sale_started`).
  - `waiting_times(trace) -> DataFrame[user_id, joined_t, offered_t, booked_t, wait_offer_s, wait_booked_s, outcome]` (outcome: `booked|hold_expired|payment_failed|sold_out|sale_stopped|never_served`). Baseline: offer = first successful grab.
  - `allocation_efficiency(trace, total_seats) -> {sold_ratio, time_to_sellout_s, mean_seat_idle_s, wasted_holds, wasted_hold_ratio}` (idle = released → next offer of that seat).
  - `fairness(waits) -> {kendall_tau, inversions, jain_index, success_by_arrival_decile: list[float]}` (tau/inversions between join order and booking order of booked users; Jain over `wait_booked_s` of booked users).
  - `surge(client, trace) -> {peak_rps, mean_rps, peak_to_mean, p95_latency_ms, error_rate, peak_payment_attempts_per_s}`.
  - `rejections(waits) -> DataFrame[reason, count, first_t, median_t]`.
  - `summarize(run) -> dict` (all of the above plus consistency).
  - `plot_run(run, out_dir)`: request rate over time, seat states over time, wait-time CDF, success probability by arrival decile. `plot_comparison(runs, labels, out_dir)`: overlaid request rate, wait CDFs, bar chart of key metrics; `summary.csv` table. Figures saved as PNG (300 dpi) and PDF, consistent style.
  - CLI `python -m analysis.report <run_dir>... --labels a,b --out analysis/out/<name>`.

- [ ] **Step 1: Write failing tests** on a hand-built 5-user trace (4 seats, one hold expiry, one late refund, one sold-out rejection):
  - `test_waiting_times_values` (exact `wait_booked_s` per user and outcomes), `test_allocation_efficiency_values` (`sold_ratio == 1.0`, `wasted_holds == 2`), `test_fairness_perfect_order` (tau 1.0, inversions 0) and `test_fairness_one_swap` (inversions 1), `test_jain_equal_waits_is_one`, `test_surge_peak_to_mean`, `test_rejections_counts`.
- [ ] **Step 2:** Run `pytest analysis/tests -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run tests. Expected: PASS.
- [ ] **Step 5:** Run `python -m analysis.report runs/<smoke run>`. Expected: figures and `summary.json` in the out dir; open one PNG to check it reads well.
- [ ] **Step 6: Commit** `feat: trace analysis and report figures`.

### Task 14: Experiments and README

**Files:**
- Create: `loadgen/scenarios/exp1_*.yaml … exp6_*.yaml`, `scripts/run_experiments.sh`, `README.md`

Experiments (500 seats, `hold_seconds=30`, gateway `failure_rate=0.1`, latency 300±200 ms unless varied; time compressed so each run finishes in a few minutes):
1. `exp1`: spike, 5,000 users within 10s — bucket (`N=10, T=2`) vs baseline.
2. `exp2`: same rate 5 seats/s — `N=1,T=0.2` vs `N=10,T=2` vs `N=50,T=10`.
3. `exp3`: rate sweep — 2, 5, 10, 25 seats/s.
4. `exp4`: gateway failure rate 0%, 10%, 30%.
5. `exp5`: hold window 10s, 30s, 60s.
6. `exp6`: scale — 1k, 5k, 10k users (adjust `poll_seconds` to 5 for 10k; note it in the README).

- [ ] **Step 1:** Write the scenario files and `run_experiments.sh` (runs each group, then `analysis.report` per group into `analysis/out/expN/`).
- [ ] **Step 2:** Run `bash scripts/run_experiments.sh exp1`. Expected: both runs exit 0, comparison figures in `analysis/out/exp1/`.
- [ ] **Step 3:** Run the remaining groups. Expected: every run exits 0 (consistency all zero). Record any run that could not complete on the laptop and why.
- [ ] **Step 4:** Write `README.md`: what the project is, architecture diagram (text), how to run (`docker compose up --build`, URLs for buyer/admin, admin token), how to run tests, the load generator and experiments, where figures land, and deploying to another machine (copy repo, set `.env`, `docker compose up -d --build`).
- [ ] **Step 5: Commit** `feat: experiment suite and README`.
