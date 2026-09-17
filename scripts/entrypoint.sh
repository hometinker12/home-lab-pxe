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

mkdir -p "$TFTP_ROOT" "$DATA_DIR" "$SSL_DIR" || true
mkdir -p "$IMAGE_ROOT/smb" "$IMAGE_ROOT/nfs" /run/rpcbind || true

for f in undionly.kpxe ipxe.efi snponly.efi; do
  if [ ! -f "$TFTP_ROOT/$f" ]; then
    if ! printf 'ipxe-stub\n' > "$TFTP_ROOT/$f" 2>/dev/null; then
      echo "WARN: cannot write $TFTP_ROOT/$f (read-only rootfs?)" >&2
    fi
  fi
done

BUNDLED_WIMBOOT="/usr/share/home-lab-pxe/wimboot"
WIMBOOT_DEST="$TFTP_ROOT/wimboot"
wimboot_is_stub() {
  [ ! -s "$1" ] && return 0
  size=$(wc -c < "$1" 2>/dev/null || echo 0)
  if [ "$size" -le 64 ] && grep -q ipxe-stub "$1" 2>/dev/null; then
    return 0
  fi
  return 1
}
if [ -s "$BUNDLED_WIMBOOT" ]; then
  if [ ! -f "$WIMBOOT_DEST" ] || wimboot_is_stub "$WIMBOOT_DEST"; then
    cp "$BUNDLED_WIMBOOT" "$WIMBOOT_DEST" 2>/dev/null || echo "WARN: cannot install bundled wimboot" >&2
  fi
elif [ ! -f "$WIMBOOT_DEST" ]; then
  printf 'ipxe-stub\n' > "$WIMBOOT_DEST" 2>/dev/null || true
fi

mkdir -p "$IMAGE_ROOT/smb" "$IMAGE_ROOT/nfs" || true

python - <<'PY'
from src.db import init_db
from src.dhcp_runtime import conf_path

init_db()
print(f"dhcp conf {conf_path()}")
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

SMB_PID_FILE="/run/samba/smbd.pid"
start_smbd() {
  if [ -z "${PXE_SMB_PASSWORD:-}" ]; then
    echo "SMB skipped: PXE_SMB_PASSWORD unset" >&2
    return 0
  fi
  python - <<'PY' || { echo "WARN: PXE_SMB_PASSWORD rejected" >&2; return 0; }
import os, re, sys
pw = os.environ.get("PXE_SMB_PASSWORD") or ""
sys.exit(0 if re.fullmatch(r"[A-Za-z0-9._~-]{20,128}", pw) else 1)
PY
  mkdir -p /var/log/samba /run/samba "$IMAGE_ROOT/smb" || true
  if ! id pxemedia >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10002 --gid 10001 pxemedia >/dev/null 2>&1 || true
  fi
  printf '%s\n%s\n' "$PXE_SMB_PASSWORD" "$PXE_SMB_PASSWORD" | smbpasswd -s -a pxemedia >/dev/null 2>&1 || {
    echo "WARN: smbpasswd failed" >&2
    return 0
  }
  if smbd -D -s /etc/samba/smb.conf; then
    echo "smbd started"
  else
    echo "WARN: smbd did not start" >&2
  fi
}

stop_smbd() {
  if [ -f "$SMB_PID_FILE" ]; then
    pid=$(tr -d '\r\n' < "$SMB_PID_FILE" 2>/dev/null || true)
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  fi
  pkill smbd 2>/dev/null || true
}

start_smbd || true
(
  trap '' HUP
  while true; do
    if [ -n "${PXE_SMB_PASSWORD:-}" ]; then
      if ! pgrep -x smbd >/dev/null 2>&1; then
        start_smbd || true
      fi
    fi
    sleep 5
  done
) &

start_nfs() {
  mkdir -p /run/rpcbind /run/dbus /var/run/ganesha /var/log/ganesha /var/lib/nfs/ganesha "$IMAGE_ROOT/nfs" || true
  chmod 755 "$IMAGE_ROOT/nfs" 2>/dev/null || true
  if command -v rpcbind >/dev/null 2>&1; then
    if rpcbind -w >/dev/null 2>&1 || rpcbind >/dev/null 2>&1; then
      echo "rpcbind started"
    else
      echo "WARN: rpcbind did not start" >&2
    fi
  fi
  if command -v dbus-uuidgen >/dev/null 2>&1; then
    dbus-uuidgen --ensure >/dev/null 2>&1 || true
  fi
  if command -v dbus-daemon >/dev/null 2>&1 && [ ! -S /run/dbus/system_bus_socket ]; then
    dbus-daemon --system --fork >/dev/null 2>&1 || echo "WARN: dbus-daemon did not start" >&2
  fi
  if ! command -v ganesha.nfsd >/dev/null 2>&1; then
    echo "WARN: ganesha.nfsd not installed" >&2
    return 0
  fi
  if ganesha.nfsd -f /etc/ganesha/ganesha.conf -L /var/log/ganesha/ganesha.log; then
    echo "ganesha.nfsd started"
  else
    echo "WARN: ganesha.nfsd did not start" >&2
  fi
}

start_nfs || true
(
  trap '' HUP
  while true; do
    if command -v ganesha.nfsd >/dev/null 2>&1; then
      if ! pgrep -f ganesha.nfsd >/dev/null 2>&1; then
        start_nfs || true
      fi
    fi
    sleep 5
  done
) &

if command -v gosu >/dev/null 2>&1 && id app >/dev/null 2>&1; then
  chown -R app:app "$DATA_DIR" "$TFTP_ROOT" "$IMAGE_ROOT" "$SSL_DIR" || true
  exec gosu app ./scripts/run-web.sh
fi

exec ./scripts/run-web.sh
