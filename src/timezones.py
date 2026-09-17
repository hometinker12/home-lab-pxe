"""IANA timezone helpers for the machine console and Settings defaults."""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

PREFERRED = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Australia/Sydney",
)


def is_valid_timezone(name: str) -> bool:
    text = (name or "").strip()
    if not text or len(text) > 64 or "\x00" in text:
        return False
    if text == "localtime" or text.startswith("SystemV/"):
        return False
    try:
        ZoneInfo(text)
        return True
    except Exception:
        return text == "UTC"


@lru_cache(maxsize=1)
def list_timezones() -> tuple[str, ...]:
    zones: set[str] = {"UTC"}
    try:
        zones.update(z for z in available_timezones() if z and z != "localtime" and not z.startswith("SystemV/"))
    except Exception:
        pass
    for name in PREFERRED:
        if is_valid_timezone(name):
            zones.add(name)
    preferred = [z for z in PREFERRED if z in zones]
    rest = sorted(zones - set(preferred), key=str.lower)
    return tuple(preferred + rest)


def timezone_choices(selected: str | None) -> list[str]:
    zones = list(list_timezones())
    current = (selected or "").strip() or "UTC"
    if current not in zones:
        zones.insert(0, current)
    return zones
