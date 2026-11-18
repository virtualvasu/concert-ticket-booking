#!/usr/bin/env bash
# Run experiment groups against the running stack and build comparison figures.
#   bash scripts/run_experiments.sh            # all groups exp1..exp6
#   bash scripts/run_experiments.sh exp1 exp4  # selected groups
# Needs the stack up (docker compose up -d --build) and the .venv from requirements-dev.txt.
set -uo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
[ -x "$PY" ] || PY=python3
export PYTHONPATH=loadgen:analysis
API=${API:-http://localhost:8080}

curl -fsS "$API/api/health" >/dev/null || { echo "stack is not up at $API (run: docker compose up -d --build)"; exit 1; }

groups=("$@")
[ ${#groups[@]} -eq 0 ] && groups=(exp1 exp2 exp3 exp4 exp5 exp6)

failed=0
for g in "${groups[@]}"; do
  dirs=(); labels=()
  for f in loadgen/scenarios/${g}_*.yaml; do
    [ -e "$f" ] || { echo "no scenarios for $g"; failed=1; continue 2; }
    name=$(basename "$f" .yaml); label=${name#${g}_}
    echo "=== $g: $name"
    out=$("$PY" -m loadgen.run "$f" --out runs | tee /dev/stderr)
    status=$?
    run_dir=$(echo "$out" | sed -n 's/^run written to //p')
    if [ $status -ne 0 ] || [ -z "$run_dir" ]; then
      echo "!!! $name failed (exit $status)"; failed=1
    fi
    if [ -n "$run_dir" ]; then dirs+=("$run_dir"); labels+=("$label"); fi
  done
  if [ ${#dirs[@]} -gt 0 ]; then
    "$PY" -m analysis.report "${dirs[@]}" --labels "$(IFS=,; echo "${labels[*]}")" --out "analysis/out/$g" || failed=1
  fi
done
exit $failed
