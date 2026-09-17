#!/usr/bin/env python3
"""Write per-generation ganesha exports and optionally SIGHUP ganesha.nfsd."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from src.ganesha_exports import (
    GENERATIONS_CONF,
    cleanup_legacy_export_root,
    clear_reload_request,
    reload_requested,
    sync_generation_exports,
)
from src.settings import get_settings


def _reload_ganesha() -> None:
    listed = subprocess.run(["pgrep", "-x", "ganesha.nfsd"], capture_output=True, text=True, check=False)
    pids = [pid for pid in (listed.stdout or "").split() if pid.isdigit()]
    if not pids:
        return
    subprocess.run(["kill", "-HUP", pids[0]], check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--dest", default=str(GENERATIONS_CONF))
    args = parser.parse_args()
    image_root = get_settings().image_root
    cleanup_legacy_export_root(image_root)
    changed = sync_generation_exports(image_root, Path(args.dest))
    dirty = reload_requested(image_root)
    if args.reload and (changed or dirty):
        _reload_ganesha()
        clear_reload_request(image_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
