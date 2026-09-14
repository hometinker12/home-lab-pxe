"""MAC and SMBIOS UUID normalization."""

from __future__ import annotations

import re

_MAC_HEX = re.compile(r"[^0-9a-fA-F]")


class InvalidMacError(ValueError):
    pass


def normalize_mac(value: str) -> str:
    hex_only = _MAC_HEX.sub("", value or "")
    if len(hex_only) != 12:
        raise InvalidMacError("MAC address must contain 12 hex digits")
    parts = [hex_only[i : i + 2].lower() for i in range(0, 12, 2)]
    return ":".join(parts)


def mac_hyphen(value: str) -> str:
    return normalize_mac(value).replace(":", "-")


def normalize_uuid(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().strip("{}").lower()
    if not text or text in {"notavailable", "null", "none"}:
        return None
    return text
