#!/bin/sh
# Run HTTP (PXE + console) and HTTPS (console) uvicorn processes as the app user.
set -e

BIND="${PXE_HTTP_BIND:-0.0.0.0}"
HTTP_PORT="${PXE_HTTP_PORT:-8080}"
HTTPS_PORT="${PXE_HTTPS_PORT:-8443}"
SSL_DIR="${PXE_SSL_DIR:-/var/lib/pxe/ssl}"
DATA_DIR="${PXE_DATA_DIR:-/var/lib/pxe/data}"
CERT="$SSL_DIR/tls.crt"
KEY="$SSL_DIR/tls.key"
RELOAD="$DATA_DIR/ssl.reload"

HTTP_PID=""
HTTPS_PID=""
EXTRACT_PID=""
EXTRACT_BACKOFF=1

start_http() {
  uvicorn src.app:create_app --factory --host "$BIND" --port "$HTTP_PORT" --workers 1 &
  HTTP_PID=$!
}

https_health() {
  python -c "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:${HTTPS_PORT}/health', timeout=1, context=ssl._create_unverified_context())" >/dev/null 2>&1
}

start_https() {
  HTTPS_PID=""
  if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "HTTPS skipped: missing $CERT or $KEY" >&2
    return 0
  fi
  uvicorn src.app:create_app --factory --host "$BIND" --port "$HTTPS_PORT" --workers 1 \
    --ssl-certfile "$CERT" --ssl-keyfile "$KEY" &
  HTTPS_PID=$!
  i=0
  while [ "$i" -lt 50 ]; do
    if [ -n "$HTTPS_PID" ] && ! kill -0 "$HTTPS_PID" 2>/dev/null; then
      echo "WARN: HTTPS uvicorn exited during startup" >&2
      HTTPS_PID=""
      return 0
    fi
    if https_health; then
      echo "HTTPS listening on ${HTTPS_PORT}"
      return 0
    fi
    i=$((i + 1))
    sleep 0.1
  done
  echo "WARN: HTTPS did not become ready on ${HTTPS_PORT}" >&2
}

start_extract() {
  python -m src.extract_worker &
  EXTRACT_PID=$!
}

stop_pid() {
  pid="$1"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
}

shutdown() {
  stop_pid "$EXTRACT_PID"
  stop_pid "$HTTP_PID"
  stop_pid "$HTTPS_PID"
  exit 0
}

trap shutdown TERM INT

start_http
# Let HTTP finish init_db before the extractor and HTTPS process open SQLite.
i=0
while [ "$i" -lt 50 ]; do
  if [ -n "$HTTP_PID" ] && ! kill -0 "$HTTP_PID" 2>/dev/null; then
    echo "ERROR: HTTP uvicorn exited during startup" >&2
    exit 1
  fi
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${HTTP_PORT}/health', timeout=1)" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 0.1
done
start_extract
start_https

while true; do
  if [ -f "$RELOAD" ]; then
    rm -f "$RELOAD"
    stop_pid "$HTTPS_PID"
    HTTPS_PID=""
    start_https
  fi
  if [ -n "$HTTP_PID" ] && ! kill -0 "$HTTP_PID" 2>/dev/null; then
    echo "ERROR: HTTP uvicorn exited" >&2
    stop_pid "$EXTRACT_PID"
    stop_pid "$HTTPS_PID"
    exit 1
  fi
  if [ -n "$EXTRACT_PID" ] && ! kill -0 "$EXTRACT_PID" 2>/dev/null; then
    echo "WARN: extract worker exited; restarting in ${EXTRACT_BACKOFF}s" >&2
    EXTRACT_PID=""
    sleep "$EXTRACT_BACKOFF"
    EXTRACT_BACKOFF=$((EXTRACT_BACKOFF * 2))
    if [ "$EXTRACT_BACKOFF" -gt 60 ]; then
      EXTRACT_BACKOFF=60
    fi
    start_extract
  elif [ -n "$EXTRACT_PID" ]; then
    EXTRACT_BACKOFF=1
  fi
  sleep 1
done
