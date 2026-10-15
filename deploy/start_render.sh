#!/bin/sh
# One Render free web service: Core + Relay + a port proxy.
set -eu

rewrite_database_url() {
  if [ -z "${DATABASE_URL:-}" ]; then
    return
  fi
  case "$DATABASE_URL" in
    postgresql+psycopg://*) ;;
    postgres://*)
      DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}"
      ;;
    postgresql://*)
      DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}"
      ;;
  esac
  case "$DATABASE_URL" in
    *sslmode=*) ;;
    *) DATABASE_URL="${DATABASE_URL}?sslmode=require" ;;
  esac
  export DATABASE_URL
}

rewrite_database_url

if [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-$RENDER_EXTERNAL_URL}"
  export RELAY_PUBLIC_URL="${RELAY_PUBLIC_URL:-$RENDER_EXTERNAL_URL/webhook/relay}"
fi

export CORE_BASE_URL="${CORE_BASE_URL:-http://127.0.0.1:8080}"
export RELAY_SERVICE_URL="${RELAY_SERVICE_URL:-http://127.0.0.1:3000}"
export NOTIFY_MODE="${NOTIFY_MODE:-relay_service}"
export FETCH_MODE="${FETCH_MODE:-local_direct}"
export RELAY_MODE="${RELAY_MODE:-live}"
export RELAY_PORT="${RELAY_PORT:-3000}"
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=192}"

wait_http() {
  url=$1
  name=$2
  i=0
  while [ "$i" -lt 60 ]; do
    if python -c "import urllib.request; urllib.request.urlopen('$url', timeout=2)" >/dev/null 2>&1; then
      echo "[start] $name ready"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "[start] $name failed to become healthy at $url" >&2
  return 1
}

cd /repo/core
alembic upgrade head
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8080 &
CORE_PID=$!
wait_http http://127.0.0.1:8080/health core

cd /repo/relay
npx tsx src/index.ts &
RELAY_PID=$!
wait_http http://127.0.0.1:3000/health relay

python /repo/deploy/proxy.py &
PROXY_PID=$!

term() {
  kill "$CORE_PID" "$RELAY_PID" "$PROXY_PID" 2>/dev/null || true
}
trap term INT TERM EXIT

wait "$CORE_PID" "$RELAY_PID" "$PROXY_PID"
