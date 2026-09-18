"""Install-source catalogs from extracted Ubuntu and Windows media."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import ExtractStatus, Image, OsFamily
from .paths import UnsafePathError, resolve_under
from .settings import get_settings

SOURCE_ID_MAX = 200
_WIM_MAGIC = b"MSWIM\x00\x00\x00"
_WIM_COMPRESSED = 0x04
_WIM_XML_RES_OFFSET = 72
_WIM_HEADER_MIN = 96
_WIM_TAIL_BYTES = 2_000_000


@dataclass(frozen=True)
class InstallSource:
    source_id: str
    label: str
    default: bool = False
    wim_index: int | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.source_id,
            "label": self.label,
            "default": self.default,
        }
        if self.wim_index is not None:
            payload["wim_index"] = self.wim_index
        return payload


def sanitize_source_id(value: str) -> str:
    text = (value or "").replace("\x00", "").strip()
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return text[:SOURCE_ID_MAX]


def _localized_name(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("en", "en_US", "en-us"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        for item in value.values():
            text = str(item or "").strip()
            if text:
                return text
    return ""


def parse_ubuntu_install_sources(text: str) -> list[InstallSource]:
    try:
        parsed = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return []
    rows: list[object]
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        raw = parsed.get("sources")
        rows = raw if isinstance(raw, list) else []
    else:
        return []
    items: list[InstallSource] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = sanitize_source_id(str(row.get("id") or ""))
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        label = _localized_name(row.get("name")) or source_id
        items.append(InstallSource(source_id=source_id, label=label, default=bool(row.get("default"))))
    return items


def linux_sources_from_tree(tree: Path) -> list[InstallSource]:
    path = tree / "casper" / "install-sources.yaml"
    if not path.is_file():
        return []
    try:
        return parse_ubuntu_install_sources(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def _decode_xml_payload(payload: bytes) -> str:
    if payload.startswith(b"\xff\xfe"):
        return payload.decode("utf-16-le")
    if payload.startswith(b"\xfe\xff"):
        return payload.decode("utf-16-be")
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig")
    stripped = payload.lstrip()
    if stripped.startswith(b"<"):
        return payload.decode("utf-8")
    try:
        text = payload.decode("utf-16-le")
    except UnicodeDecodeError:
        return payload.decode("utf-8", errors="replace")
    if "<WIM" in text or "<IMAGE" in text:
        return text
    return payload.decode("utf-8", errors="replace")


def parse_wim_image_xml(xml_text: str) -> list[InstallSource]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    tag = root.tag.rsplit("}", 1)[-1].upper()
    nodes = [root] if tag == "IMAGE" else list(root)
    items: list[InstallSource] = []
    seen: set[str] = set()
    for node in nodes:
        if node.tag.rsplit("}", 1)[-1].upper() != "IMAGE":
            continue
        index_raw = (node.attrib.get("INDEX") or node.attrib.get("Index") or "").strip()
        try:
            index = int(index_raw)
        except ValueError:
            index = len(items) + 1
        if index < 1:
            continue
        name = ""
        description = ""
        for child in node:
            child_tag = child.tag.rsplit("}", 1)[-1].upper()
            text = (child.text or "").strip()
            if child_tag == "NAME" and text:
                name = text
            elif child_tag == "DESCRIPTION" and text:
                description = text
        source_id = sanitize_source_id(name or f"image-{index}")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        label = description or name or f"Image {index}"
        if name and description and name not in description:
            label = f"{description} ({name})"
        items.append(InstallSource(source_id=source_id, label=label, default=index == 1, wim_index=index))
    items.sort(key=lambda item: item.wim_index or 0)
    if items and not any(item.default for item in items):
        first = items[0]
        items[0] = InstallSource(
            source_id=first.source_id,
            label=first.label,
            default=True,
            wim_index=first.wim_index,
        )
    return items


def _resource_header(data: bytes, offset: int) -> tuple[int, int, int, int]:
    size_and_flags = int.from_bytes(data[offset : offset + 8], "little")
    flags = (size_and_flags >> 56) & 0xFF
    size = size_and_flags & ((1 << 56) - 1)
    res_offset = int.from_bytes(data[offset + 8 : offset + 16], "little")
    original = int.from_bytes(data[offset + 16 : offset + 24], "little")
    return size, flags, res_offset, original


def _read_wim_xml_from_header(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(208)
        if len(header) < _WIM_HEADER_MIN or not header.startswith(_WIM_MAGIC):
            return ""
        size, flags, offset, original = _resource_header(header, _WIM_XML_RES_OFFSET)
        if size < 8 or offset < 1:
            return ""
        if flags & _WIM_COMPRESSED:
            return ""
        handle.seek(offset)
        payload = handle.read(size)
    if len(payload) < 8:
        return ""
    if original and original != size and original < len(payload) + 8:
        payload = payload[:original]
    return _decode_xml_payload(payload)


def _read_wim_xml_from_tail(path: Path) -> str:
    try:
        file_size = path.stat().st_size
    except OSError:
        return ""
    if file_size < 16:
        return ""
    read_size = min(_WIM_TAIL_BYTES, file_size)
    with path.open("rb") as handle:
        handle.seek(file_size - read_size)
        tail = handle.read(read_size)
    marker = "<WIM>".encode("utf-16-le")
    idx = tail.rfind(marker)
    if idx < 0:
        idx = tail.rfind(b"<WIM>")
        if idx < 0:
            return ""
        return _decode_xml_payload(tail[idx:])
    return _decode_xml_payload(tail[idx:])


def windows_sources_from_wim(wim: Path) -> list[InstallSource]:
    if not wim.is_file():
        return []
    xml_text = _read_wim_xml_from_header(wim) or _read_wim_xml_from_tail(wim)
    if not xml_text.strip():
        return []
    return parse_wim_image_xml(xml_text)


def catalog_json(items: list[InstallSource]) -> str:
    return json.dumps([item.as_dict() for item in items], ensure_ascii=True)


def catalog_from_json(raw: str) -> list[InstallSource]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    items: list[InstallSource] = []
    seen: set[str] = set()
    for row in parsed:
        if not isinstance(row, dict):
            continue
        source_id = sanitize_source_id(str(row.get("id") or row.get("source_id") or ""))
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        label = str(row.get("label") or source_id).strip() or source_id
        index_raw = row.get("wim_index")
        try:
            wim_index = int(index_raw) if index_raw is not None and str(index_raw) != "" else None
        except (TypeError, ValueError):
            wim_index = None
        if wim_index is not None and wim_index < 1:
            wim_index = None
        items.append(
            InstallSource(
                source_id=source_id,
                label=label,
                default=bool(row.get("default")),
                wim_index=wim_index,
            )
        )
    return items


def pick_source_id(items: list[InstallSource], current: str) -> str:
    ids = [item.source_id for item in items]
    wanted = sanitize_source_id(current)
    if wanted in ids:
        return wanted
    for item in items:
        if item.default:
            return item.source_id
    return ids[0] if ids else ""


def wim_index_for_source(items: list[InstallSource], source_id: str) -> int | None:
    wanted = sanitize_source_id(source_id)
    for item in items:
        if item.source_id == wanted:
            return item.wim_index
    return None


def apply_catalog(image: Image, items: list[InstallSource]) -> bool:
    if not items:
        return False
    blob = catalog_json(items)
    chosen = pick_source_id(items, image.source_id or "")
    wim = wim_index_for_source(items, chosen)
    changed = False
    if (image.source_options or "") != blob:
        image.source_options = blob
        changed = True
    if (image.source_id or "") != chosen:
        image.source_id = chosen
        changed = True
    if wim is not None and int(image.wim_index or 1) != int(wim):
        image.wim_index = int(wim)
        changed = True
    return changed


def _linux_tree(image: Image, root: Path) -> Path | None:
    generation = (image.extract_generation or "").replace("\\", "/").strip()
    if generation.startswith("nfs/"):
        try:
            return resolve_under(root, generation)
        except UnsafePathError:
            return None
    return None


def _windows_wim(image: Image, root: Path) -> Path | None:
    relative = (image.install_wim_path or "").replace("\\", "/").strip()
    if not relative:
        return None
    try:
        return resolve_under(root, relative)
    except UnsafePathError:
        return None


def discover_image_sources(image: Image, *, image_root: Path | None = None) -> list[InstallSource]:
    root = (image_root or get_settings().image_root).resolve()
    if image.os_family == OsFamily.linux.value:
        tree = _linux_tree(image, root)
        return linux_sources_from_tree(tree) if tree is not None else []
    if image.os_family == OsFamily.windows.value:
        wim = _windows_wim(image, root)
        return windows_sources_from_wim(wim) if wim is not None else []
    return []


def refresh_image_sources(image: Image, *, image_root: Path | None = None) -> bool:
    if (image.extract_status or "") != ExtractStatus.ready.value:
        return False
    return apply_catalog(image, discover_image_sources(image, image_root=image_root))
