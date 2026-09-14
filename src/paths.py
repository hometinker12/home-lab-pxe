"""Safe joins under configured image/TFTP roots."""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(ValueError):
    pass


def resolve_under(root: Path, relative: str) -> Path:
    if not relative or relative.startswith("/") or ":" in relative[:2]:
        raise UnsafePathError("absolute paths are not allowed")
    root_r = root.resolve()
    candidate = (root_r / relative).resolve()
    try:
        candidate.relative_to(root_r)
    except ValueError as exc:
        raise UnsafePathError("path escapes configured root") from exc
    return candidate
