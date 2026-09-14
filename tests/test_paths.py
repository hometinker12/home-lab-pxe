from pathlib import Path

import pytest

from src.paths import UnsafePathError, resolve_under


def test_resolve_under_allows_relative(tmp_path: Path):
    target = tmp_path / "ubuntu" / "vmlinuz"
    target.parent.mkdir()
    target.write_bytes(b"k")
    assert resolve_under(tmp_path, "ubuntu/vmlinuz") == target.resolve()


def test_resolve_under_blocks_escape(tmp_path: Path):
    with pytest.raises(UnsafePathError):
        resolve_under(tmp_path, "../secret")
