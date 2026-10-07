#!/usr/bin/env bash
# Container smoke test: build both images, start the stack, make three HTTP checks, remove everything.
# Needs Docker with Compose v2 and curl; no key, no .env, no call to ClinicalTrials.gov or OpenAI.
# Written for bash 3.2 (what macOS ships) and Linux.            Usage: bash docker-smoke.sh
set -euo pipefail
cd "$(dirname "$0")"

# Its own project name and ports, so a stack started with "docker compose up" or "make dev" is not touched.
project="${SMOKE_PROJECT:-ctviz-smoke}"
export BACKEND_PORT="${SMOKE_BACKEND_PORT:-18000}" FRONTEND_PORT="${SMOKE_FRONTEND_PORT:-18300}"
api="http://127.0.0.1:$BACKEND_PORT" web="http://127.0.0.1:$FRONTEND_PORT"

finish() {  # runs on every exit: show the logs if anything failed, then remove containers, network, images
  code=$?
  [ "$code" -eq 0 ] || docker compose -p "$project" logs --tail 40 || true
  docker compose -p "$project" down --rmi local > /dev/null 2>&1 || true
  exit "$code"
}
trap finish EXIT

check() {  # check NAME URL TEXT: the URL must answer 200 with TEXT in its body
  local body
  if body=$(curl --silent --show-error --fail --max-time 10 "$2") && [[ $body == *"$3"* ]]; then
    echo "ok    $1"
  else
    echo "FAIL  $1: $2 did not answer 200 with $3"
    return 1
  fi
}

check_proxy() {  # the browser's path: frontend proxy to backend, which lists the recorded examples
  check "browser path: proxy to backend" "$web/api/backend/v1/examples" '"01-'
}

docker compose -p "$project" up --build --wait --wait-timeout 120

failed=0
check "backend on its published port" "$api/healthz" '"ok"' || failed=1
check "page" "$web/" "<html" || failed=1
check_proxy || failed=1
[ "$failed" -eq 0 ] && echo "smoke test passed"
exit "$failed"
