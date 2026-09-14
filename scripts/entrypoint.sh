#!/bin/sh
# Container entrypoint: DHCP helper (start/stop/reload dnsmasq) + FastAPI.
set -e

TFTP_ROOT="${PXE_TFTP_ROOT:-/var/lib/pxe/tftp}"
IMAGE_ROOT="${PXE_IMAGE_ROOT:-/var/lib/pxe/images}"
DATA_DIR="${PXE_DATA_DIR:-/var/lib/pxe/data}"
SSL_DIR="${PXE_SSL_DIR:-/var/lib/pxe/ssl}"
CONF="$DATA_DIR/dnsmasq-pxe.conf"
ENABLED_FILE="$DATA_DIR/dhcp.enabled"
TFTP_ENABLED_FILE="$DATA_DIR/tftp.enabled"
CMD_FILE="$DATA_DIR/dhcp.cmd"
STATUS_FILE="$DATA_DIR/dhcp.status"
PID_FILE="/tmp/dnsmasq-pxe.pid"

export PXE_DATA_DIR="$DATA_DIR"
export PXE_SSL_DIR="$SSL_DIR"

mkdir -p "$TFTP_ROOT" "$IMAGE_ROOT" "$DATA_DIR" "$SSL_DIR" || true

for f in undionly.kpxe ipxe.efi snponly.efi wimboot; do
  if [ ! -f "$TFTP_ROOT/$f" ]; then
    if ! printf 'ipxe-stub\n' > "$TFTP_ROOT/$f" 2>/dev/null; then
      echo "WARN: cannot write $TFTP_ROOT/$f (read-only rootfs?)" >&2
    fi
  fi
done

python - <<'PY'
import os
from pathlib import Path

from src.dhcp_config import render_dnsmasq_conf, spec_from_settings
from src.settings import get_settings

settings = get_settings()
conf = Path(os.environ.get("PXE_DATA_DIR", "/var/lib/pxe/data")) / "dnsmasq-pxe.conf"
if not conf.is_file():
    render_dnsmasq_conf(spec_from_settings(settings), tftp_root=settings.tftp_root, conf_path=conf)
    print(f"wrote {conf}")
else:
    print(f"keeping {conf}")
PY

python - <<'PY' || echo "WARN: could not ensure TLS certificate" >&2
from src.tls_store import TlsError, ensure_tls_material

try:
    info = ensure_tls_material()
    print(f"tls {info.source} subject={info.subject}")
except TlsError as exc:
    print(f"WARN: TLS material: {exc}")
PY

stop_dnsmasq() {
  if [ -f "$PID_FILE" ]; then
    pid=$(tr -d '\r\n' < "$PID_FILE" 2>/dev/null || true)
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
      i=0
      while [ "$i" -lt 20 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        i=$((i + 1))
        sleep 0.1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  echo stopped > "$STATUS_FILE" || true
}

start_dnsmasq() {
  if [ ! -f "$CONF" ]; then
    echo "WARN: missing $CONF" >&2
    echo failed > "$STATUS_FILE" || true
    return 1
  fi
  if dnsmasq -C "$CONF" --pid-file="$PID_FILE"; then
    echo running > "$STATUS_FILE" || true
    echo "dnsmasq started"
    return 0
  fi
  echo failed > "$STATUS_FILE" || true
  echo "WARN: dnsmasq did not start" >&2
  return 1
}

apply_dhcp() {
  enabled="0"
  tftp_enabled="0"
  if [ -f "$ENABLED_FILE" ]; then
    enabled=$(tr -d '\r\n' < "$ENABLED_FILE")
  elif [ "${PXE_ENABLE_DHCP:-1}" != "0" ]; then
    enabled="1"
  fi
  if [ -f "$TFTP_ENABLED_FILE" ]; then
    tftp_enabled=$(tr -d '\r\n' < "$TFTP_ENABLED_FILE")
  elif [ "${PXE_ENABLE_TFTP:-1}" != "0" ]; then
    tftp_enabled="1"
  fi
  stop_dnsmasq
  if [ "$enabled" = "1" ] || [ "$tftp_enabled" = "1" ]; then
    if start_dnsmasq; then
      return 0
    fi
    if [ "${PXE_DHCP_OPTIONAL:-0}" != "1" ] && [ "${1:-0}" = "1" ]; then
      echo "ERROR: dnsmasq failed to start" >&2
      return 1
    fi
  fi
  return 0
}

if [ ! -f "$ENABLED_FILE" ]; then
  if [ "${PXE_ENABLE_DHCP:-1}" != "0" ]; then
    printf '1\n' > "$ENABLED_FILE"
  else
    printf '0\n' > "$ENABLED_FILE"
  fi
fi

if [ ! -f "$TFTP_ENABLED_FILE" ]; then
  if [ "${PXE_ENABLE_TFTP:-1}" != "0" ]; then
    printf '1\n' > "$TFTP_ENABLED_FILE"
  else
    printf '0\n' > "$TFTP_ENABLED_FILE"
  fi
fi

if ! apply_dhcp 1; then
  exit 1
fi

# Root watcher: consume dhcp.cmd from the console. Ignore HUP so exec below does not kill it.
(
  trap '' HUP
  while true; do
    if [ -f "$CMD_FILE" ]; then
      rm -f "$CMD_FILE"
      apply_dhcp 0 || true
    fi
    sleep 1
  done
) &

if command -v gosu >/dev/null 2>&1 && id app >/dev/null 2>&1; then
  chown -R app:app "$DATA_DIR" "$TFTP_ROOT" "$IMAGE_ROOT" "$SSL_DIR" || true
  exec gosu app ./scripts/run-web.sh
fi

exec ./scripts/run-web.sh
