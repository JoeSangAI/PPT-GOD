#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/.logs/public-mvp"
mkdir -p "$LOG_DIR"

find_free_port() {
  local start="${1:-8000}"
  local port="$start"
  while lsof -ti "tcp:${port}" >/dev/null 2>&1; do
    port=$((port + 1))
  done
  printf '%s\n' "$port"
}

PORT="${PPTGOD_PORT:-$(find_free_port 8000)}"
BACKEND_URL="http://127.0.0.1:${PORT}"
TUNNEL_PROTOCOL="${PPTGOD_TUNNEL_PROTOCOL:-auto}"
BACKEND_LOG="$LOG_DIR/backend-${PORT}.log"
CELERY_LOG="$LOG_DIR/celery-${PORT}.log"
TUNNEL_LOG="$LOG_DIR/cloudflared-${PORT}.log"
PID_FILE="$LOG_DIR/pids-${PORT}.txt"
PUBLIC_URL_FILE="$LOG_DIR/latest-url-${PORT}.txt"
TUNNEL_HEALTH_INTERVAL="${PPTGOD_TUNNEL_HEALTH_INTERVAL:-30}"
TUNNEL_HEALTH_FAILURES="${PPTGOD_TUNNEL_HEALTH_FAILURES:-3}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required for the public MVP link."
  echo "Install it with: brew install cloudflared"
  exit 1
fi

cleanup() {
  if [[ -f "$PID_FILE" ]]; then
    while read -r pid; do
      [[ -n "$pid" ]] && kill "$pid" >/dev/null 2>&1 || true
    done < "$PID_FILE"
  fi
}
trap cleanup EXIT INT TERM

echo "==> Building frontend for same-origin production serving"
(cd "$ROOT_DIR/frontend" && npm run build)

echo "==> Checking Redis"
if ! (cd "$ROOT_DIR/backend" && python - <<'PY' >/dev/null 2>&1
import redis
from app.core.config import settings
redis.from_url(settings.REDIS_URL or "redis://localhost:6379/0").ping()
PY
); then
  if command -v docker >/dev/null 2>&1; then
    echo "Redis is not reachable. Starting docker compose redis..."
    (cd "$ROOT_DIR" && docker compose up -d redis)
    sleep 2
  else
    echo "Warning: Redis is not reachable and Docker is unavailable. Image generation tasks need Redis/Celery."
  fi
fi

: > "$PID_FILE"
rm -f "$PUBLIC_URL_FILE"

start_tunnel() {
  local label="${1:-initial}"
  if [[ "$label" == "initial" ]]; then
    TUNNEL_LOG="$LOG_DIR/cloudflared-${PORT}.log"
  else
    TUNNEL_LOG="$LOG_DIR/cloudflared-${PORT}-${label}-$(date +%Y%m%d-%H%M%S).log"
  fi

  echo "==> Opening Cloudflare Tunnel (${TUNNEL_PROTOCOL})"
  cloudflared tunnel --url "$BACKEND_URL" --protocol "$TUNNEL_PROTOCOL" --no-autoupdate >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  echo "$TUNNEL_PID" >> "$PID_FILE"
}

wait_for_public_url() {
  PUBLIC_URL=""
  for _ in $(seq 1 90); do
    PUBLIC_URL="$(grep -Eo 'https://[-a-zA-Z0-9.]+\.trycloudflare\.com' "$TUNNEL_LOG" | tail -n 1 || true)"
    if [[ -n "$PUBLIC_URL" ]] && grep -q "Registered tunnel connection" "$TUNNEL_LOG"; then
      break
    fi
    sleep 1
  done

  if [[ -z "$PUBLIC_URL" ]] || ! grep -q "Registered tunnel connection" "$TUNNEL_LOG"; then
    echo "Failed to create a reachable public URL. See: $TUNNEL_LOG"
    echo
    tail -n 40 "$TUNNEL_LOG" || true
    return 1
  fi

  echo "==> Verifying public URL"
  for _ in $(seq 1 30); do
    if curl -fsS "${PUBLIC_URL}/health" >/dev/null 2>&1; then
      printf '%s\n' "$PUBLIC_URL" > "$PUBLIC_URL_FILE"
      return 0
    fi
    sleep 1
  done

  echo "Public URL was created but did not pass health check: $PUBLIC_URL"
  echo "See: $TUNNEL_LOG"
  return 1
}

announce_public_url() {
  echo
  echo "PPT GOD public MVP is live:"
  echo "$PUBLIC_URL"
  echo
  echo "Share this URL with testers. Keep this script running while they use it."
  echo "Latest URL file: $PUBLIC_URL_FILE"
  echo "Logs:"
  echo "  Backend: $BACKEND_LOG"
  echo "  Celery:  $CELERY_LOG"
  echo "  Tunnel:  $TUNNEL_LOG"
}

restart_tunnel() {
  while true; do
    echo
    echo "Cloudflare quick Tunnel became unreachable. Restarting it now..."
    kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    wait "$TUNNEL_PID" >/dev/null 2>&1 || true
    start_tunnel "restart"
    if wait_for_public_url; then
      announce_public_url
      return 0
    fi

    echo "Cloudflare quick Tunnel restart failed. Retrying in 15 seconds..."
    kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    wait "$TUNNEL_PID" >/dev/null 2>&1 || true
    sleep 15
  done
}

echo "==> Starting backend on ${BACKEND_URL}"
(
  cd "$ROOT_DIR/backend"
  exec python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
) >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" >> "$PID_FILE"

for _ in $(seq 1 60); do
  if curl -fsS "${BACKEND_URL}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "${BACKEND_URL}/health" >/dev/null

echo "==> Starting Celery worker"
(
  cd "$ROOT_DIR/backend"
  exec python -m celery -A app.celery_app worker -l info -n "pptgod-public-${PORT}@%h"
) >"$CELERY_LOG" 2>&1 &
CELERY_PID=$!
echo "$CELERY_PID" >> "$PID_FILE"

start_tunnel "initial"
wait_for_public_url
announce_public_url

if command -v open >/dev/null 2>&1; then
  open "$PUBLIC_URL"
fi

tunnel_failures=0
while true; do
  sleep "$TUNNEL_HEALTH_INTERVAL"

  if ! kill -0 "$TUNNEL_PID" >/dev/null 2>&1; then
    wait "$TUNNEL_PID" >/dev/null 2>&1 || true
    restart_tunnel
    tunnel_failures=0
    continue
  fi

  if curl -fsS "${PUBLIC_URL}/health" >/dev/null 2>&1; then
    tunnel_failures=0
    continue
  fi

  tunnel_failures=$((tunnel_failures + 1))
  echo "Warning: public URL health check failed (${tunnel_failures}/${TUNNEL_HEALTH_FAILURES}): $PUBLIC_URL"
  if [[ "$tunnel_failures" -ge "$TUNNEL_HEALTH_FAILURES" ]]; then
    restart_tunnel
    tunnel_failures=0
  fi
done
