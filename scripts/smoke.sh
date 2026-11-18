#!/usr/bin/env bash
# End-to-end smoke test of the dockerized stack.
set -euo pipefail
cd "$(dirname "$0")/.."

API=${API:-http://localhost:8000}
TOKEN=${ADMIN_TOKEN:-admin}

[ -f .env ] || cp .env.example .env
docker compose up -d --build

echo "waiting for /api/health ..."
for _ in $(seq 1 60); do
  curl -fsS "$API/api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "$API/api/health" >/dev/null || { echo "SMOKE FAILED: api never became healthy"; exit 1; }

admin() { curl -fsS -H "X-Admin-Token: $TOKEN" -H 'Content-Type: application/json' "$@"; }
jget() { python3 -c "import sys,json; print(json.load(sys.stdin)$1)"; }

SALE=$(admin -X POST "$API/api/admin/sales" -d '{
  "mode":"bucket","label":"smoke",
  "sections":[{"name":"Gold","rows":2,"seats_per_row":10,"price":3000}],
  "seats_per_tick":5,"tick_seconds":2,"hold_seconds":30
}' | jget "['sale_id']")
admin -X POST "$API/api/admin/sales/$SALE/start" >/dev/null
echo "sale $SALE started"

USERS=()
for n in alice bob carol; do
  uid=$(curl -fsS -X POST "$API/api/queue/join" -H 'Content-Type: application/json' \
        -d "{\"name\":\"$n\"}" | jget "['user_id']")
  USERS+=("$uid")
done

for uid in "${USERS[@]}"; do
  hold=""
  for _ in $(seq 1 40); do
    me=$(curl -fsS "$API/api/me" -H "X-User-Id: $uid")
    hold=$(echo "$me" | python3 -c "import sys,json; o=json.load(sys.stdin)['offer']; print(o['hold_id'] if o else '')")
    [ -n "$hold" ] && break
    sleep 1
  done
  [ -n "$hold" ] || { echo "SMOKE FAILED: $uid never got an offer"; exit 1; }
  curl -fsS -X POST "$API/api/pay" -H "X-User-Id: $uid" -H 'Content-Type: application/json' \
    -d "{\"hold_id\":$hold,\"idempotency_key\":\"smoke-$uid\",\"force\":\"success\"}" >/dev/null
done

for uid in "${USERS[@]}"; do
  state=$(curl -fsS "$API/api/me" -H "X-User-Id: $uid" | jget "['state']")
  [ "$state" = "booked" ] || { echo "SMOKE FAILED: $uid is $state, expected booked"; exit 1; }
done

admin "$API/api/admin/sales/$SALE/consistency" | python3 -c "
import sys, json
c = json.load(sys.stdin)
print('consistency:', c)
sys.exit(0 if all(v == 0 for v in c.values()) else 1)
" || { echo "SMOKE FAILED: consistency violations"; exit 1; }

echo "SMOKE OK"
