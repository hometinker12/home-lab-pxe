"""Cloud-init node editor: view and apply cloud-config mappings inside Linux seeds."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from ..seed_store import SeedError
from .generated_nodes import ALIASES
from .schema import MODELED_KEYS, NODES, node_label_for_key

_MAX_EDITOR_BYTES = 256 * 1024
_TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")
_BLOCK_MARKERS = frozenset({"ssh_keys", "packages"})
_PXE_BLOCK_RE = re.compile(r"^__PXE_([a-z_]+)__$")
_PASSWORD_KEYS = frozenset({"password", "hashed_passwd", "passwd"})

_SHIELDED_PREFIX = "__PXE_"
_SHIELDED_SUFFIX = "__"


class _LiteralDumper(yaml.SafeDumper):
    pass


def _literal_str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_LiteralDumper.add_representer(str, _literal_str_representer)


def shield_tokens(text: str) -> str:
    return _TOKEN.sub(lambda m: json.dumps(f"{_SHIELDED_PREFIX}{m.group(1)}{_SHIELDED_SUFFIX}"), text)


def unshield_scalars(text: str) -> str:
    return re.sub(r"""(["']?)__PXE_([a-z_]+)__\1""", r"{{\2}}", text)


def restore_block_lines(text: str) -> str:
    return re.sub(
        r"^([ \t]*)([A-Za-z0-9_-]+):\s+PXEBLOCK_([a-z_]+)\s*$",
        lambda m: f"{m.group(1)}{m.group(2)}:\n{m.group(1)}  {{{{{m.group(3)}}}}}",
        text,
        flags=re.M,
    )


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _unshield_value(value: Any) -> Any:
    if isinstance(value, str):
        match = _PXE_BLOCK_RE.fullmatch(value)
        if match and match.group(1) in _BLOCK_MARKERS:
            return {"__pxe_block__": match.group(1)}
        if value.startswith(_SHIELDED_PREFIX) and value.endswith(_SHIELDED_SUFFIX):
            token = value[len(_SHIELDED_PREFIX) : -len(_SHIELDED_SUFFIX)]
            return f"{{{{{token}}}}}"
        return value
    if isinstance(value, list):
        return [_unshield_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _unshield_value(v) for k, v in value.items()}
    return value


def _shield_value(value: Any) -> Any:
    if isinstance(value, str):
        match = _TOKEN.fullmatch(value.strip())
        if match:
            return f"{_SHIELDED_PREFIX}{match.group(1)}{_SHIELDED_SUFFIX}"
        return value
    if isinstance(value, list):
        return [_shield_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _shield_value(v) for k, v in value.items()}
    return value


def _is_block_marker(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value.keys()) == {"__pxe_block__"} and value["__pxe_block__"] in _BLOCK_MARKERS
    )


def _emit_block_scalar(token: str) -> str:
    return f"PXEBLOCK_{token}"


def _normalize_user_row(row: dict) -> dict:
    out = dict(row)
    if "lock-passwd" in out and "lock_passwd" not in out:
        out["lock_passwd"] = out.pop("lock-passwd")
    if "ssh-authorized-keys" in out and "ssh_authorized_keys" not in out:
        out["ssh_authorized_keys"] = out.pop("ssh-authorized-keys")
    return out


def _groups_to_lines(groups: list) -> list[str]:
    lines: list[str] = []
    for item in groups:
        if isinstance(item, dict):
            for name, members in item.items():
                if isinstance(members, list):
                    lines.append(f"{name}: {', '.join(str(m) for m in members)}")
                else:
                    lines.append(f"{name}: {members}")
        else:
            lines.append(str(item))
    return lines


def _lines_to_groups(lines: list[str]) -> list:
    result: list = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if ":" in text:
            name, _, rest = text.partition(":")
            members = [m.strip() for m in rest.split(",") if m.strip()]
            result.append({name.strip(): members})
        else:
            result.append(text)
    return result


def _options_to_lines(options: dict) -> list[str]:
    return [f"{k}:{v}" for k, v in options.items()]


def _lines_to_options(lines: list[str]) -> dict:
    result: dict = {}
    for line in lines:
        text = line.strip()
        if not text or ":" not in text:
            continue
        key, _, val = text.partition(":")
        result[key.strip()] = val.strip()
    return result


def _parse_int(value: Any, key: str) -> int | None:
    if _is_empty(value):
        return None
    if isinstance(value, bool):
        raise SeedError(f"Expected an integer for {key}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise SeedError(f"Expected an integer for {key}") from exc


def _is_password_placeholder(value: Any) -> bool:
    text = str(value)
    if "{{password" in text or "{{password_hash}}" in text:
        return True
    if text.startswith(_SHIELDED_PREFIX) and text.endswith(_SHIELDED_SUFFIX):
        token = text[len(_SHIELDED_PREFIX) : -len(_SHIELDED_SUFFIX)]
        return token.startswith("password")
    return False


def _validate_password_value(value: Any, context: str, *, strict: bool = False) -> None:
    if _is_empty(value):
        return
    text = str(value)
    if _is_password_placeholder(text):
        return
    if not strict and text.startswith("$"):
        return
    raise SeedError(f"Credential fields must use placeholders ({context})")


def _rename_aliases(value: Any) -> Any:
    if isinstance(value, list):
        return [_rename_aliases(item) for item in value]
    if not isinstance(value, dict):
        return value
    renamed: dict = {}
    for key, item in value.items():
        canon = ALIASES.get(key, key)
        if key != canon and canon in value:
            continue
        renamed[canon] = _rename_aliases(item)
    return renamed


def _enum_to_view(value: Any, choices: list[str]) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text if text in choices else ""


def _enum_from_doc(value: Any, choices: list[str], key: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    if text not in choices:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "":
        return None
    return text


def _string_list_from_doc(value: Any) -> list[str] | dict | None:
    if _is_block_marker(value):
        return value
    if isinstance(value, list):
        items = [str(v) for v in value if not _is_empty(v)]
        return items or None
    if isinstance(value, str) and not _is_empty(value):
        return [value]
    return None


def _emit_string_list(value: Any, block_token: str | None = None) -> Any:
    blocked = _block_scalar_from_value(value)
    if blocked:
        return blocked
    if isinstance(value, list):
        items = [_shield_value(str(v)) for v in value if not _is_empty(v)]
        return items or None
    return None


def _emit_groups(value: Any) -> list | None:
    if not isinstance(value, list):
        return None
    lines = [str(v) for v in value if not _is_empty(v)]
    if not lines:
        return None
    return _lines_to_groups(lines)


def _view_groups(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    return _groups_to_lines(value)


def _emit_object_field(field: dict, raw: dict) -> Any:
    ftype = field["type"]
    key = field["key"]
    value = raw.get(key)
    if ftype == "string":
        if _is_empty(value):
            return None
        if field.get("credential") or key in _PASSWORD_KEYS or key == "password":
            _validate_password_value(value, key, strict=bool(field.get("credential") or key == "password"))
        return _shield_value(str(value))
    if ftype == "text":
        if _is_empty(value):
            return None
        return _shield_value(str(value))
    if ftype == "bool":
        if value is None:
            return None
        return bool(value)
    if ftype == "enum":
        return _enum_from_doc(value, field.get("choices", []), key)
    if ftype == "int":
        return _parse_int(value, key)
    if ftype == "string_list":
        return _emit_string_list(value, field.get("block_token"))
    if ftype == "lines":
        parser = field.get("parser")
        if parser == "groups":
            return _emit_groups(value)
        if parser == "options":
            if not isinstance(value, list):
                return None
            lines = [str(v) for v in value if not _is_empty(v)]
            if not lines:
                return None
            return _lines_to_options(lines)
        if not isinstance(value, list):
            return None
        lines = [_shield_value(str(v)) for v in value if not _is_empty(v)]
        return lines or None
    if ftype == "row_list":
        return _emit_row_list(field, value)
    if ftype == "object":
        emitted = _emit_object(field, raw)
        if not emitted:
            return None
        return emitted.get(key)
    if ftype == "disk_layout":
        return _emit_layout(value)
    if ftype == "flex":
        return _emit_flex(value)
    if ftype == "yaml_value":
        return _emit_yaml_value(value, key)
    if ftype == "all_or_list":
        return _emit_all_or_list(value)
    return None


def _block_scalar_from_value(value: Any) -> str | None:
    if _is_block_marker(value):
        return _emit_block_scalar(value["__pxe_block__"])
    if isinstance(value, str):
        match = _PXE_BLOCK_RE.fullmatch(value)
        if match and match.group(1) in _BLOCK_MARKERS:
            return _emit_block_scalar(match.group(1))
    if isinstance(value, list) and len(value) == 1:
        return _block_scalar_from_value(value[0])
    return None


def _emit_layout(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    mode = str(value.get("mode") or "")
    if mode in {"", "unset"}:
        return None
    if mode == "true":
        return True
    if mode == "false":
        return False
    if mode == "remove":
        return "remove"
    if mode != "custom":
        return None
    lines = value.get("lines") or []
    if isinstance(lines, str):
        lines = [line for line in lines.splitlines() if line.strip()]
    parsed: list = []
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        if "," in text:
            parts: list = []
            for piece in text.split(","):
                item = piece.strip()
                parts.append(int(item) if item.isdigit() else item)
            parsed.append(parts)
        elif text.isdigit():
            parsed.append(int(text))
        else:
            parsed.append(text)
    return parsed or None


def _emit_flex(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if _is_empty(value):
        return None
    text = str(value).strip()
    if text == "true":
        return True
    if text == "false":
        return False
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    return _shield_value(text)


def _emit_yaml_value(value: Any, key: str) -> Any:
    if _is_empty(value):
        return None
    if not isinstance(value, str):
        return _shield_value(value)
    try:
        parsed = yaml.safe_load(shield_tokens(value))
    except yaml.YAMLError as exc:
        raise SeedError(f"Invalid YAML in {key}") from exc
    return None if parsed is None else parsed


def _emit_all_or_list(value: Any) -> Any:
    if isinstance(value, dict) and value.get("all") is True:
        return "all"
    if value == "all":
        return "all"
    return _emit_string_list(value)


def _coerce_rows(field: dict, rows: Any) -> list:
    label = str(field.get("label") or field.get("key"))
    if isinstance(rows, str):
        if not rows.strip():
            return []
        try:
            parsed = yaml.safe_load(shield_tokens(rows))
        except yaml.YAMLError as exc:
            raise SeedError(f"Invalid YAML in {label}") from exc
        if parsed is None:
            return []
        if not isinstance(parsed, list):
            raise SeedError(f"{label} must be a YAML list")
        return parsed
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise SeedError(f"{label} must be a list")
    return rows


def _emit_row_list(field: dict, rows: Any) -> list | dict | None:
    rows = _coerce_rows(field, rows)
    item_fields = field.get("item_fields", [])
    known = {item["key"] for item in item_fields}
    required_keys = {item["key"] for item in item_fields if item.get("required")}
    emit = field.get("emit") or "list"
    key_field = field.get("key_field") or "name"
    out_rows: list = []
    mapped: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            if field.get("allow_scalars") and not _is_empty(row):
                out_rows.append(_shield_value(row) if isinstance(row, str) else row)
            continue
        row = _normalize_user_row(row)
        if field.get("entry_rule") == "chpasswd":
            if _is_empty(row.get("name")):
                raise SeedError("chpasswd user name is required")
            kind = str(row.get("type") or "hash")
            if kind != "RANDOM" and _is_empty(row.get("password")):
                raise SeedError("chpasswd user password is required")
        elif any(_is_empty(row.get(req)) for req in required_keys):
            continue
        emitted: dict = {}
        for item_field in item_fields:
            ikey = item_field["key"]
            if emit in {"mapping", "mapping_scalar", "list_or_map"} and ikey == key_field:
                continue
            if item_field["type"] == "string" and ikey == "groups" and not _is_empty(row.get(ikey)):
                parts = [part.strip() for part in str(row.get(ikey)).split(",") if part.strip()]
                if parts:
                    emitted[ikey] = parts
                continue
            value = _emit_object_field(item_field, row)
            if value is not None:
                if emit == "list_or_map" and ikey == "value":
                    emitted[ikey] = value
                else:
                    emitted[ikey] = value
        if field.get("preserve_extra"):
            for extra_key, extra_value in row.items():
                if extra_key in known or str(extra_key).startswith("__"):
                    continue
                if not _is_empty(extra_value):
                    emitted[extra_key] = _shield_value(extra_value)
        if emit == "mapping_scalar":
            name = str(row.get(key_field) or "").strip()
            if not name:
                continue
            mapped[name] = emitted.get("value", row.get("value"))
            continue
        if emit == "mapping":
            name = str(row.get(key_field) or "").strip()
            if not name:
                continue
            mapped[name] = emitted
            continue
        if emit == "tuple":
            values = []
            for item_field in item_fields:
                piece = emitted.get(item_field["key"], "")
                values.append("" if piece is None else piece)
            while values and _is_empty(values[-1]):
                values.pop()
            if values:
                out_rows.append(values)
            continue
        if emit == "list_or_map":
            out_rows.append(emitted)
            continue
        if emitted:
            out_rows.append(emitted)
    if emit == "mapping_scalar":
        return mapped or None
    if emit == "mapping":
        return mapped or None
    if emit == "list_or_map":
        if any(isinstance(item, dict) and item.get("name") for item in out_rows):
            named = {
                str(item["name"]): item.get("value") for item in out_rows if isinstance(item, dict) and item.get("name")
            }
            return named or None
        values = [item.get("value") for item in out_rows if isinstance(item, dict) and item.get("value") is not None]
        return values or None
    return out_rows or None


def _emit_object(field: dict, raw: dict) -> dict | None:
    obj_key = field["key"]
    obj_raw = raw.get(obj_key)
    if not isinstance(obj_raw, dict):
        obj_raw = {}
    inner: dict = {}
    known = {sub["key"] for sub in field.get("object_fields", [])}
    for sub in field.get("object_fields", []):
        sub_key = sub["key"]
        emitted = _emit_object_field(sub, obj_raw)
        if emitted is not None:
            inner[sub_key] = emitted
    extra_text = obj_raw.get("__extra__")
    if field.get("preserve_extra") and isinstance(extra_text, str) and extra_text.strip():
        try:
            extra = yaml.safe_load(shield_tokens(extra_text))
        except yaml.YAMLError as exc:
            raise SeedError(f"Invalid additional keys in {obj_key}") from exc
        if extra is not None:
            if not isinstance(extra, dict):
                raise SeedError(f"Additional keys in {obj_key} must be a mapping")
            for extra_key, extra_value in extra.items():
                if extra_key in known or extra_key in MODELED_KEYS:
                    raise SeedError(f"Move '{extra_key}' to the {node_label_for_key(extra_key)} section.")
                inner[extra_key] = extra_value
    required_sub = field.get("require_subkey")
    if required_sub and _is_empty(inner.get(required_sub)):
        return None
    if obj_key == "phone_home" and _is_empty(inner.get("url")):
        return None
    if obj_key == "power_state" and _is_empty(inner.get("mode")):
        return None
    if obj_key == "growpart":
        mode = inner.get("mode")
        if mode == "false":
            inner["mode"] = False
        elif _is_empty(mode) and not any(not _is_empty(inner.get(k)) for k in inner if k != "mode"):
            return None
        elif _is_empty(mode):
            inner.pop("mode", None)
        if not inner:
            return None
    if obj_key == "chpasswd":
        expire = inner.get("expire")
        users = inner.get("users")
        if expire is None and not users:
            return None
        if expire is None:
            inner.pop("expire", None)
    if obj_key == "resolv_conf":
        if not inner:
            return None
    if obj_key == "ntp":
        if not inner:
            return None
    if not inner:
        return None
    return {obj_key: inner}


def _view_object_field(field: dict, value: Any) -> Any:
    ftype = field["type"]
    if ftype == "string":
        return _unshield_value(value) if not _is_empty(value) else None
    if ftype == "text":
        return _unshield_value(value) if not _is_empty(value) else None
    if ftype == "bool":
        return value if isinstance(value, bool) else None
    if ftype == "enum":
        return _enum_to_view(value, field.get("choices", []))
    if ftype == "int":
        return value if isinstance(value, int) else None
    if ftype == "string_list":
        if _is_block_marker(value):
            return value
        if isinstance(value, str):
            unshielded = _unshield_value(value)
            if _is_block_marker(unshielded):
                return unshielded
            if _PXE_BLOCK_RE.fullmatch(str(value)):
                token = _PXE_BLOCK_RE.fullmatch(str(value)).group(1)
                if token in _BLOCK_MARKERS:
                    return {"__pxe_block__": token}
        if isinstance(value, list):
            return [_unshield_value(v) for v in value]
        return None
    if ftype == "lines":
        parser = field.get("parser")
        if parser == "groups":
            return _view_groups(value)
        if parser == "options" and isinstance(value, dict):
            return _options_to_lines(value)
        if isinstance(value, list):
            return [_unshield_value(v) for v in value]
        return None
    if ftype == "row_list":
        return _view_row_list(field, value)
    if ftype == "object":
        if not isinstance(value, dict):
            return None
        viewed = _view_object(field, {field["key"]: value})
        if not viewed:
            return None
        return viewed.get(field["key"])
    if ftype == "disk_layout":
        return _view_layout(value)
    if ftype == "flex":
        return _view_flex(value)
    if ftype == "yaml_value":
        return _view_yaml_value(value)
    if ftype == "all_or_list":
        if value == "all":
            return {"all": True}
        return _view_object_field({**field, "type": "string_list"}, value)
    return None


def _view_layout(value: Any) -> dict | None:
    if value is True:
        return {"mode": "true"}
    if value is False:
        return {"mode": "false"}
    if value == "remove":
        return {"mode": "remove"}
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, list):
                lines.append(", ".join(str(part) for part in item))
            else:
                lines.append(str(item))
        return {"mode": "custom", "lines": lines}
    return None


def _view_flex(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if _is_empty(value) or isinstance(value, (dict, list)):
        return None
    return _unshield_value(value)


def _view_yaml_value(value: Any) -> str | None:
    if _is_empty(value):
        return None
    if isinstance(value, str):
        return str(_unshield_value(value))
    text = yaml.safe_dump(value, default_flow_style=False, sort_keys=False).rstrip()
    return unshield_scalars(text)


def _view_row_list(field: dict, rows: Any) -> list | None:
    emit = field.get("emit") or "list"
    key_field = field.get("key_field") or "name"
    if emit in {"mapping", "mapping_scalar"} and isinstance(rows, dict):
        converted = []
        for name, payload in rows.items():
            if emit == "mapping_scalar":
                converted.append({key_field: name, "value": payload})
            elif isinstance(payload, dict):
                converted.append({key_field: name, **payload})
            else:
                converted.append({key_field: name, "value": payload})
        rows = converted
    elif emit == "list_or_map" and isinstance(rows, dict):
        rows = [{key_field: name, "value": payload} for name, payload in rows.items()]
    elif emit == "list_or_map" and isinstance(rows, list):
        rows = [{"value": item} if not isinstance(item, dict) else item for item in rows]
    elif emit == "tuple" and isinstance(rows, list):
        converted = []
        item_fields = field.get("item_fields", [])
        for item in rows:
            if isinstance(item, list):
                converted.append({item_fields[i]["key"]: item[i] for i in range(min(len(item), len(item_fields)))})
            elif isinstance(item, dict):
                converted.append(item)
        rows = converted
    if not isinstance(rows, list):
        return None
    item_fields = field.get("item_fields", [])
    out: list = []
    known = {item["key"] for item in item_fields}
    for row in rows:
        if not isinstance(row, dict):
            if field.get("allow_scalars") and not _is_empty(row):
                out.append(_unshield_value(row))
            continue
        normalized = _normalize_user_row(row)
        viewed: dict = {}
        for item_field in item_fields:
            ikey = item_field["key"]
            if ikey not in normalized:
                continue
            ivalue = normalized[ikey]
            if item_field["type"] == "bool":
                viewed[ikey] = ivalue if isinstance(ivalue, bool) else None
            elif item_field["type"] == "enum":
                viewed[ikey] = _enum_to_view(ivalue, item_field.get("choices", []))
            elif item_field["type"] == "string_list":
                if isinstance(ivalue, str):
                    unshielded = _unshield_value(ivalue)
                    if _is_block_marker(unshielded):
                        viewed[ikey] = unshielded
                    elif _PXE_BLOCK_RE.fullmatch(str(ivalue)):
                        token = _PXE_BLOCK_RE.fullmatch(str(ivalue)).group(1)
                        if token in _BLOCK_MARKERS:
                            viewed[ikey] = {"__pxe_block__": token}
                    else:
                        viewed[ikey] = [unshielded]
                elif isinstance(ivalue, list):
                    viewed[ikey] = [_unshield_value(v) for v in ivalue]
            elif item_field["type"] == "string" and ikey == "groups":
                if isinstance(ivalue, list):
                    viewed[ikey] = ", ".join(str(v) for v in ivalue)
                elif not _is_empty(ivalue):
                    viewed[ikey] = str(_unshield_value(ivalue))
            else:
                viewed_value = _view_object_field(item_field, ivalue)
                if viewed_value is not None:
                    viewed[ikey] = viewed_value
        if field.get("preserve_extra"):
            for extra_key, extra_value in normalized.items():
                if extra_key in known or str(extra_key).startswith("__"):
                    continue
                if not _is_empty(extra_value):
                    viewed[extra_key] = _unshield_value(extra_value)
        if viewed:
            out.append(viewed)
    return out or None


def _view_object(field: dict, cc: dict) -> dict | None:
    obj_key = field["key"]
    obj = cc.get(obj_key)
    if not isinstance(obj, dict):
        return None
    inner: dict = {}
    for sub in field.get("object_fields", []):
        sub_key = sub["key"]
        if sub_key not in obj:
            continue
        viewed = _view_object_field(sub, obj[sub_key])
        if viewed is not None:
            inner[sub_key] = viewed
    if field.get("preserve_extra"):
        known = {sub["key"] for sub in field.get("object_fields", [])}
        extra = {key: value for key, value in obj.items() if key not in known}
        if extra:
            inner["__extra__"] = yaml.safe_dump(extra, default_flow_style=False, sort_keys=False).rstrip("\n")
    return {obj_key: inner} if inner else None


def _yaml_node_id(node: dict, field: dict) -> str:
    if field.get("yaml_mode") == "mapping" and node["id"] == "ssh":
        return "ssh"
    if field.get("yaml_mode") == "mapping" and node["id"] == "apt":
        return "apt"
    return node["id"]


def _yaml_text_for_node(node: dict, cc: dict, field: dict) -> str:
    parts: dict = {}
    yaml_mode = field.get("yaml_mode", "value")
    if yaml_mode == "mapping":
        for yk in field.get("yaml_keys", []):
            if yk in cc:
                parts[yk] = cc[yk]
    else:
        key = field["key"]
        if key in cc:
            parts[key] = cc[key]
    if not parts:
        return ""
    if yaml_mode == "value" and len(parts) == 1:
        value = next(iter(parts.values()))
        return yaml.safe_dump(value, default_flow_style=False, sort_keys=False).rstrip("\n")
    return yaml.safe_dump(parts, default_flow_style=False, sort_keys=False).rstrip("\n")


def _apply_yaml_field(field: dict, node: dict, yaml_text: str, cc: dict) -> None:
    if not yaml_text or not yaml_text.strip():
        return
    yaml_mode = field.get("yaml_mode", "value")
    yaml_type = field.get("yaml_type", "dict")
    label = node["label"]
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise SeedError(f"Invalid YAML in {label}") from exc
    if yaml_mode == "mapping":
        if not isinstance(parsed, dict):
            raise SeedError(f"{label} must be a YAML mapping")
        allowed = set(field.get("yaml_keys", []))
        for key in parsed:
            if key not in allowed:
                raise SeedError(f"Unexpected key {key!r} in {label}")
            if key in MODELED_KEYS:
                owner = node_label_for_key(key)
                if owner != label:
                    raise SeedError(f"Move '{key}' to the {owner} section.")
            cc[key] = parsed[key]
    else:
        key = field["key"]
        if yaml_type == "dict" and parsed is not None and not isinstance(parsed, dict):
            raise SeedError(f"{label} must be a YAML mapping")
        if yaml_type == "list" and parsed is not None and not isinstance(parsed, list):
            raise SeedError(f"{label} must be a YAML list")
        if isinstance(parsed, dict):
            for sub_key in parsed:
                if sub_key in MODELED_KEYS and sub_key != key:
                    raise SeedError(f"Move '{sub_key}' to the {node_label_for_key(sub_key)} section.")
        cc[key] = parsed


def prune_document(raw: dict) -> dict:
    """Return cloud-config keys to emit from posted editor JSON."""
    if not isinstance(raw, dict):
        raise SeedError("Editor document must be a JSON object")
    yaml_blobs = raw.get("__yaml__")
    if yaml_blobs is not None and not isinstance(yaml_blobs, dict):
        raise SeedError("__yaml__ must be an object")
    yaml_blobs = yaml_blobs or {}
    cc: dict = {}
    for node in NODES:
        for field in node.get("fields", []):
            ftype = field.get("type")
            if ftype == "yaml":
                yid = _yaml_node_id(node, field)
                text = yaml_blobs.get(yid, "")
                if text and str(text).strip():
                    _apply_yaml_field(field, node, str(text), cc)
                continue
            if ftype == "object":
                emitted = _emit_object(field, raw)
                if emitted:
                    cc.update(emitted)
                continue
            if ftype == "row_list":
                key = field["key"]
                rows = raw.get(key)
                emitted = _emit_row_list(field, rows)
                if emitted is not None:
                    cc[key] = emitted
                continue
            key = field.get("key")
            if not key or str(key).startswith("__yaml_"):
                continue
            if ftype == "string":
                val = raw.get(key)
                if not _is_empty(val):
                    if key in _PASSWORD_KEYS or key == "password":
                        _validate_password_value(val, key)
                    cc[key] = _shield_value(str(val))
            elif ftype == "text":
                val = raw.get(key)
                if not _is_empty(val):
                    cc[key] = _shield_value(str(val))
            elif ftype == "bool":
                val = raw.get(key)
                if val is not None:
                    cc[key] = bool(val)
            elif ftype == "enum":
                choice = _enum_from_doc(raw.get(key), field.get("choices", []), key)
                if choice is not None:
                    cc[key] = choice
            elif ftype == "int":
                val = _parse_int(raw.get(key), key)
                if val is not None:
                    cc[key] = val
            elif ftype == "string_list":
                sl = _emit_string_list(raw.get(key), field.get("block_token"))
                if sl is not None:
                    cc[key] = sl
            elif ftype == "lines":
                parser = field.get("parser")
                val = raw.get(key)
                if parser == "groups":
                    groups = _emit_groups(val)
                    if groups is not None:
                        cc[key] = groups
                elif isinstance(val, list):
                    lines = [_shield_value(str(v)) for v in val if not _is_empty(v)]
                    if lines:
                        cc[key] = lines
    return cc


def _parse_seed(seed_text: str) -> tuple[dict, str]:
    text = (seed_text or "").strip()
    if not text:
        return {}, "cloud-config"
    shielded = shield_tokens(text)
    try:
        parsed = yaml.safe_load(shielded)
    except yaml.YAMLError as exc:
        raise SeedError("user-data is not valid YAML") from exc
    if parsed is None:
        return {}, "cloud-config"
    if not isinstance(parsed, dict):
        raise SeedError("user-data must be a YAML mapping")
    if "autoinstall" in parsed and isinstance(parsed.get("autoinstall"), dict):
        auto = parsed["autoinstall"]
        ud = auto.get("user-data")
        if not isinstance(ud, dict):
            ud = {}
        return parsed, "autoinstall"
    if "autoinstall" not in parsed:
        return parsed, "cloud-config"
    return parsed, "cloud-config"


def _cloud_config_mapping(parsed: dict, mode: str) -> dict:
    if mode == "autoinstall":
        auto = parsed.get("autoinstall")
        if not isinstance(auto, dict):
            return {}
        ud = auto.get("user-data")
        return dict(ud) if isinstance(ud, dict) else {}
    return dict(parsed)


def _build_view_doc(cc: dict) -> dict:
    doc: dict = {"__yaml__": {}}
    for node in NODES:
        for field in node.get("fields", []):
            ftype = field.get("type")
            if ftype == "yaml":
                yid = _yaml_node_id(node, field)
                text = _yaml_text_for_node(node, cc, field)
                if text:
                    doc["__yaml__"][yid] = text
                continue
            if ftype == "object":
                viewed = _view_object(field, cc)
                if viewed:
                    doc.update(viewed)
                continue
            if ftype == "row_list":
                key = field["key"]
                if key in cc:
                    rows = _view_row_list(field, cc[key])
                    if rows:
                        doc[key] = rows
                continue
            key = field.get("key")
            if not key or str(key).startswith("__yaml_"):
                continue
            if key not in cc:
                continue
            value = cc[key]
            if ftype == "string" or ftype == "text":
                doc[key] = _unshield_value(value)
            elif ftype == "bool":
                if isinstance(value, bool):
                    doc[key] = value
            elif ftype == "enum":
                doc[key] = _enum_to_view(value, field.get("choices", []))
            elif ftype == "int":
                if isinstance(value, int):
                    doc[key] = value
            elif ftype == "string_list":
                if isinstance(value, str):
                    unshielded = _unshield_value(value)
                    if _is_block_marker(unshielded):
                        doc[key] = unshielded
                    elif _PXE_BLOCK_RE.fullmatch(value):
                        doc[key] = {"__pxe_block__": _PXE_BLOCK_RE.fullmatch(value).group(1)}
                    else:
                        doc[key] = [unshielded]
                elif isinstance(value, list):
                    doc[key] = [_unshield_value(v) for v in value]
            elif ftype == "lines":
                parser = field.get("parser")
                if parser == "groups":
                    lines = _view_groups(value)
                    if lines:
                        doc[key] = lines
                else:
                    if isinstance(value, list):
                        doc[key] = [_unshield_value(v) for v in value]
    if not doc["__yaml__"]:
        doc["__yaml__"] = {}
    return doc


def cloud_config_view(seed_text: str) -> dict:
    """Return mode, structured doc, and extra_yaml for unknown keys."""
    parsed, mode = _parse_seed(seed_text)
    cc = _rename_aliases(_cloud_config_mapping(parsed, mode))
    extra = {k: v for k, v in cc.items() if k not in MODELED_KEYS}
    doc = _build_view_doc(cc)
    extra_yaml = ""
    if extra:
        extra_yaml = yaml.safe_dump(extra, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip("\n")
    return {"mode": mode, "doc": doc, "extra_yaml": extra_yaml}


def _ensure_header(text: str) -> str:
    body = text.lstrip("\n")
    if not body.lstrip().startswith("#cloud-config"):
        body = "#cloud-config\n" + body
    if not body.endswith("\n"):
        body += "\n"
    return body


def _dump_seed(parsed: dict) -> str:
    dumped = yaml.dump(
        parsed,
        Dumper=_LiteralDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return _ensure_header(dumped)


def apply_cloudinit_editor(seed_text: str, cc_json: str, extra_yaml: str) -> str:
    """Return full seed text with cloud-config mapping replaced. Raises SeedError."""
    if len((cc_json or "").encode("utf-8")) > _MAX_EDITOR_BYTES:
        raise SeedError("Editor JSON exceeds 256 KiB")
    if len((extra_yaml or "").encode("utf-8")) > _MAX_EDITOR_BYTES:
        raise SeedError("Advanced YAML exceeds 256 KiB")
    try:
        raw_doc = json.loads(cc_json)
    except json.JSONDecodeError as exc:
        raise SeedError("Editor document is not valid JSON") from exc
    if not isinstance(raw_doc, dict):
        raise SeedError("Editor document must be a JSON object")
    pruned = prune_document(raw_doc)
    extra_text = (extra_yaml or "").strip()
    if not extra_text:
        extra: dict = {}
    else:
        try:
            loaded_extra = yaml.safe_load(extra_text)
        except yaml.YAMLError as exc:
            raise SeedError("Advanced YAML is not valid YAML") from exc
        if loaded_extra is None:
            extra = {}
        elif not isinstance(loaded_extra, dict):
            raise SeedError("Advanced YAML must be a mapping")
        else:
            extra = loaded_extra
    for key in extra:
        if key in MODELED_KEYS:
            raise SeedError(f"Move '{key}' to the {node_label_for_key(key)} section.")
    merged = {**extra, **pruned}
    parsed, mode = _parse_seed(seed_text)
    if mode == "autoinstall":
        auto = parsed.get("autoinstall")
        if not isinstance(auto, dict):
            auto = {}
            parsed["autoinstall"] = auto
        auto["user-data"] = merged
    else:
        parsed = merged
    dumped = _dump_seed(parsed)
    dumped = restore_block_lines(dumped)
    dumped = unshield_scalars(dumped)
    return _ensure_header(dumped)
