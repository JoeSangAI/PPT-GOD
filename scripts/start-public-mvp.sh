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
BACKEND_LOG="$LOG_DIR/backend-${PORT}.log"
CELERY_LOG="$LOG_DIR/celery-${PORT}.log"
TUNNEL_LOG="$LOG_DIR/cloudflared-${PORT}.log"
PID_FILE="$LOG_DIR/pids-${PORT}.txt"

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

echo "==> Starting backend on ${BACKEND_URL}"
(
  cd "$ROOT_DIR/backend"
  python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
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
  python -m celery -A app.celery_app worker -l info
) >"$CELERY_LOG" 2>&1 &
CELERY_PID=$!
echo "$CELERY_PID" >> "$PID_FILE"

echo "==> Opening Cloudflare Tunnel"
cloudflared tunnel --url "$BACKEND_URL" --protocol http2 --no-autoupdate >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
echo "$TUNNEL_PID" >> "$PID_FILE"

PUBLIC_URL=""
for _ in $(seq 1 90); do
  PUBLIC_URL="$(grep -Eo 'https://[-a-zA-Z0-9.]+\.trycloudflare\.com' "$TUNNEL_LOG" | tail -n 1 || true)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Failed to create public URL. See: $TUNNEL_LOG"
  exit 1
fi

echo
echo "PPT GOD public MVP is live:"
echo "$PUBLIC_URL"
echo
echo "Share this URL with testers. Keep this script running while they use it."
echo "Logs:"
echo "  Backend: $BACKEND_LOG"
echo "  Celery:  $CELERY_LOG"
echo "  Tunnel:  $TUNNEL_LOG"

if command -v open >/dev/null 2>&1; then
  open "$PUBLIC_URL"
fi

wait "$TUNNEL_PID"
