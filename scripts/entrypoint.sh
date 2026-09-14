#!/bin/sh
# Container entrypoint: optional dnsmasq (DHCP/TFTP) then FastAPI.
set -e

TFTP_ROOT="${PXE_TFTP_ROOT:-/var/lib/pxe/tftp}"
IMAGE_ROOT="${PXE_IMAGE_ROOT:-/var/lib/pxe/images}"
DATA_DIR="/var/lib/pxe/data"
HTTP_PORT="${PXE_HTTP_PORT:-8080}"

mkdir -p "$TFTP_ROOT" "$IMAGE_ROOT" "$DATA_DIR"

for f in undionly.kpxe ipxe.efi snponly.efi wimboot; do
  if [ ! -f "$TFTP_ROOT/$f" ]; then
    printf 'ipxe-stub\n' > "$TFTP_ROOT/$f"
  fi
done

python - <<'PY'
from pathlib import Path

from src.dhcp_config import render_dnsmasq_conf
from src.settings import get_settings

settings = get_settings()
render_dnsmasq_conf(settings, tftp_root=settings.tftp_root, conf_path=Path("/tmp/dnsmasq-pxe.conf"))
print("wrote /tmp/dnsmasq-pxe.conf")
PY

if [ "${PXE_ENABLE_DHCP:-1}" != "0" ]; then
  if dnsmasq -C /tmp/dnsmasq-pxe.conf --pid-file=/tmp/dnsmasq-pxe.pid; then
    echo "dnsmasq started"
  else
    if [ "${PXE_DHCP_OPTIONAL:-0}" = "1" ]; then
      echo "WARN: dnsmasq did not start; continuing with HTTP PXE endpoints only" >&2
    else
      echo "ERROR: dnsmasq failed to start" >&2
      exit 1
    fi
  fi
fi

if command -v gosu >/dev/null 2>&1 && id app >/dev/null 2>&1; then
  chown -R app:app "$DATA_DIR" "$TFTP_ROOT" "$IMAGE_ROOT" || true
  exec gosu app uvicorn src.app:create_app --factory --host 0.0.0.0 --port "$HTTP_PORT" --workers 1
fi

exec uvicorn src.app:create_app --factory --host 0.0.0.0 --port "$HTTP_PORT" --workers 1
