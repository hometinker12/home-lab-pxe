#!/usr/bin/env python3
"""Detect this computer's LAN IPv4 (not Docker, not 127.0.0.1) and write .host-lan-ip.env."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write PXE_HOST_LAN_IPV4 to .host-lan-ip.env for docker compose",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".host-lan-ip.env",
        help="Path for --write (default: .host-lan-ip.env)",
    )
    args = parser.parse_args()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.netinfo import detect_host_lan_ipv4, write_host_lan_env

    if args.write:
        ip = write_host_lan_env(args.output)
        print(f"wrote {args.output} PXE_HOST_LAN_IPV4={ip}")
        return
    ip = detect_host_lan_ipv4(container=False)
    if not ip:
        print("Could not detect a host LAN IPv4 address", file=sys.stderr)
        raise SystemExit(1)
    print(ip)


if __name__ == "__main__":
    main()
