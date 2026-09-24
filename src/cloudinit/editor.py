"""Cloud-init node editor: view and apply cloud-config mappings inside Linux seeds.

``cloud_config_view`` turns a seed into editor state (one JSON value per top-level key, shaped by
the field types in ``schema.NODES``). ``apply_cloudinit_editor`` / ``cloud_config_preview`` turn that
state back into YAML. Invariants:

* Never silently drop data. A value the form cannot represent is kept verbatim: nested values go to
  the parent's ``__extra__`` YAML, top-level values go to ``extra_yaml`` (Advanced YAML).
* A modeled key may appear in ``__extra__`` / Advanced YAML as long as the form does not also set it.
* Credential fields accept placeholders only; ``hashed_passwd`` also accepts a crypt hash (``$id$...``),
  the same rule ``seed_render.validate_seed_template`` applies on save. The same check runs on keys that
  arrive through ``__extra__``, Advanced YAML, or YAML-valued fields (``_check_credentials``).
* A no-op apply returns the seed unchanged; in autoinstall mode only the ``user-data`` lines are rewritten.
* Seed text, user-data, and passwords are never logged.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import yaml

from ..seed_store import CRYPT_HASH_RE, SeedError
from .schema import ADVANCED_KEYS, ALIASES, NODES, node_for_key

_MAX_EDITOR_BYTES = 256 * 1024
_TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")
_BLOCK_MARKERS = frozenset({"ssh_keys", "packages"})
_PXE_BLOCK_RE = re.compile(r"^__PXE_([a-z_]+)__$")
_EMBEDDED_RE = re.compile(r'"__PXE_([a-z_]+)__"')
_INT_RE = re.compile(r"-?\d+")
_OCTAL_RE = re.compile(r"0o([0-7]+)")
_PASSWORD_KEYS = frozenset({"password", "hashed_passwd", "passwd"})
_SCALARS = (str, int, float, bool)

_SHIELDED_PREFIX = "__PXE_"
_SHIELDED_SUFFIX = "__"

_OMIT: Any = object()
_DUMP_OPTS: dict = {"allow_unicode": True, "width": 4096}


class EditorError(SeedError):
    """SeedError that knows which node / state path it belongs to."""

    def __init__(self, message: str, *, node: str | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.node = node
        self.path = path


class _Unrepresentable(Exception):
    """Raised by view functions when the form cannot hold a value without changing it."""


class _OctalInt(int):
    """An int that YAML-dumps in octal (write_files permissions)."""


class _LiteralDumper(yaml.SafeDumper):
    """Block literals for multi-line strings; sequences indented under their parent key."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _literal_str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _octal_representer(dumper: yaml.SafeDumper, data: int) -> yaml.nodes.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:int", f"0{int(data):o}")


_LiteralDumper.add_representer(str, _literal_str_representer)
_LiteralDumper.add_representer(_OctalInt, _octal_representer)


# --------------------------------------------------------------------------- tokens


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


def _unshield_str(value: str) -> str:
    match = _PXE_BLOCK_RE.fullmatch(value)
    if match:
        return f"{{{{{match.group(1)}}}}}"
    return _EMBEDDED_RE.sub(lambda m: f"{{{{{m.group(1)}}}}}", value)


def _shield_str(value: str) -> str:
    match = _TOKEN.fullmatch(value.strip())
    if match:
        return f"{_SHIELDED_PREFIX}{match.group(1)}{_SHIELDED_SUFFIX}"
    return value


def _shield_value(value: Any) -> Any:
    if isinstance(value, str):
        return _shield_str(value)
    if isinstance(value, list):
        return [_shield_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _shield_value(v) for k, v in value.items()}
    return value


def _block_token(value: Any) -> str | None:
    if isinstance(value, dict):
        token = value.get("__pxe_block__") if set(value) == {"__pxe_block__"} else None
        return token if token in _BLOCK_MARKERS else None
    if isinstance(value, str):
        text = value.strip()
        match = _PXE_BLOCK_RE.fullmatch(text) or _TOKEN.fullmatch(text)
        if match and match.group(1) in _BLOCK_MARKERS:
            return match.group(1)
        return None
    if isinstance(value, list) and len(value) == 1:
        return _block_token(value[0])
    return None


def _blockify(value: Any) -> Any:
    """Mapping values that are exactly a block token must be written on their own line."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(item, str):
                match = _PXE_BLOCK_RE.fullmatch(item)
                if match and match.group(1) in _BLOCK_MARKERS:
                    out[key] = f"PXEBLOCK_{match.group(1)}"
                    continue
            out[key] = _blockify(item)
        return out
    if isinstance(value, list):
        return [_blockify(item) for item in value]
    return value


# --------------------------------------------------------------------------- small helpers


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _dump_yaml(value: Any) -> str:
    text = yaml.dump(value, Dumper=_LiteralDumper, default_flow_style=False, sort_keys=False, **_DUMP_OPTS)
    if text.endswith("\n...\n"):
        text = text[: -len("\n...\n")]
    return unshield_scalars(text.rstrip("\n"))


def _load_yaml(text: str, what: str, path: str) -> Any:
    # Keep a final newline: a trailing ``|`` block scalar (clip chomping) would lose its line break.
    try:
        return yaml.safe_load(shield_tokens(_with_final_newline(text)))
    except yaml.YAMLError as exc:
        raise EditorError(f"Invalid YAML in {what}", path=path) from exc


def _with_final_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _scalar_text(value: Any) -> str:
    """Text that ``_parse_scalar`` turns back into exactly ``value``."""
    if isinstance(value, str):
        shown = _unshield_str(value)
        try:
            if _parse_scalar(shown) == value:
                return shown
        except (yaml.YAMLError, ValueError):
            pass
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10**6)
    if text.endswith("\n...\n"):
        text = text[: -len("\n...\n")]
    return unshield_scalars(text.strip())


def _parse_scalar(text: str) -> Any:
    parsed = yaml.safe_load(shield_tokens(text))
    if isinstance(parsed, (dict, list)):
        raise ValueError("not a scalar")
    return parsed


def _same(left: Any, right: Any) -> bool:
    """Deep equality that also compares scalar types (True != 1, "1" != 1)."""
    if type(left) is not type(right) and not (isinstance(left, int) and isinstance(right, int)):
        return False
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, dict):
        return list(left) == list(right) and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _is_password_placeholder(value: Any) -> bool:
    text = str(value)
    if "{{password" in text or "{{password_hash}}" in text:
        return True
    if text.startswith(_SHIELDED_PREFIX) and text.endswith(_SHIELDED_SUFFIX):
        token = text[len(_SHIELDED_PREFIX) : -len(_SHIELDED_SUFFIX)]
        return token.startswith("password")
    return False


def _validate_password_value(value: Any, context: str, *, strict: bool, path: str) -> None:
    if _is_empty(value):
        return
    text = str(value)
    if _is_password_placeholder(text):
        return
    if not strict and CRYPT_HASH_RE.fullmatch(text.strip()):
        return
    raise EditorError(f"Credential fields must use placeholders ({context})", path=path)


# Well-known cloud-init credential keys, checked wherever they appear (Other keys YAML, Advanced YAML,
# YAML-valued fields), not only in the form fields that model them.
_CREDENTIAL_KEYS = frozenset({"password", "passwd", "hashed_passwd", "plain_text_passwd"})
_CREDENTIAL_ALIASES = {"hashed-passwd": "hashed_passwd", "plain-text-passwd": "plain_text_passwd"}
_RANDOM_PASSWORDS = frozenset({"R", "RANDOM"})
_TOP_FIELDS: dict[str, dict] = {}
for _node in NODES:
    for _field in _node.get("fields", []):
        _TOP_FIELDS.setdefault(_field["key"], _field)


def _credential_strict(key: str, field: dict | None) -> bool | None:
    """Placeholder strictness for ``key`` (same rule as ``_emit_string``), or None if it is no credential.

    Only ``hashed_passwd`` also accepts a crypt hash; this matches ``seed_render`` on save.
    """
    if field is not None:
        if not (field.get("credential") or key in _PASSWORD_KEYS or key in _CREDENTIAL_KEYS):
            return None
        return key != "hashed_passwd"
    if key not in _CREDENTIAL_KEYS:
        return None
    return key != "hashed_passwd"


def _credential_text(value: Any) -> Any:
    return _unshield_str(value) if isinstance(value, str) else value


def _check_chpasswd_list(value: Any, where: str) -> None:
    """Legacy ``chpasswd.list``: ``user:password`` lines (string) or items (list)."""
    if isinstance(value, str):
        entries: list = value.splitlines()
    elif isinstance(value, list):
        entries = value
    else:
        entries = [value]
    for entry in entries:
        if _is_empty(entry):
            continue
        text = _credential_text(entry) if isinstance(entry, str) else str(entry)
        _, sep, secret = text.strip().partition(":")
        if not sep or secret.strip() in _RANDOM_PASSWORDS:
            continue
        _validate_password_value(secret.strip(), "chpasswd.list", strict=True, path=where)


def _check_credentials(
    value: Any,
    known: dict[str, dict] | None,
    aliases: dict[str, str] | None,
    data_path: str,
    *,
    parent: str = "",
    where: str | None = None,
) -> None:
    """Reject literal credentials anywhere in ``value``.

    ``known`` / ``aliases`` describe the schema at this nesting level (None when the level is not
    modelled). Credential keys resolve through the schema first (``credential`` fields, same
    strictness as the form), then fall back to the well-known cloud-init credential keys.
    ``where`` is the editor path to report (for example ``users[0].__extra__``); by default the
    data path of the offending key.
    """
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_credentials(item, known, aliases, f"{data_path}[{index}]", parent=parent, where=where)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        item_path = _join(data_path, str(key))
        target = where or item_path
        if not isinstance(key, str):
            _check_credentials(item, None, None, item_path, where=where)
            continue
        canon = (aliases or {}).get(key) or _CREDENTIAL_ALIASES.get(key, key)
        field = (known or {}).get(canon)
        if field is None and canon == "list" and parent == "chpasswd":
            _check_chpasswd_list(item, target)
            continue
        strict = _credential_strict(canon, field)
        if strict is not None:
            _validate_password_value(_credential_text(item), canon, strict=strict, path=target)
            continue
        field_type = field.get("type") if field else None
        if field_type == "object":
            sub = {sub["key"]: sub for sub in field.get("object_fields") or []}
            _check_credentials(item, sub, field.get("aliases"), item_path, parent=canon, where=where)
        elif field_type == "row_list" and field.get("item_fields"):
            sub = {sub["key"]: sub for sub in field.get("item_fields") or []}
            if field.get("emit") == "mapping" and isinstance(item, dict):
                for name, payload in item.items():
                    _check_credentials(
                        payload, sub, field.get("aliases"), _join(item_path, str(name)), parent=canon, where=where
                    )
            else:
                _check_credentials(item, sub, field.get("aliases"), item_path, parent=canon, where=where)
        else:
            _check_credentials(item, None, None, item_path, parent=canon, where=where)


def _check_top_credentials(mapping: dict, *, where: str | None = None) -> None:
    """Credential check for top-level cloud-config keys; the error names the owning node."""
    for key, item in mapping.items():
        try:
            _check_credentials({key: item}, _TOP_FIELDS, ALIASES, "", where=where)
        except EditorError as exc:
            if exc.node is None:
                node = node_for_key(key) if isinstance(key, str) else None
                exc.node = node["id"] if node else "__advanced__"
            raise


def _rename_aliases(value: dict, aliases: dict[str, str] | None, path: str, notes: list[str]) -> dict:
    if not aliases:
        return value
    out: dict = {}
    for key, item in value.items():
        canon = aliases.get(key) if isinstance(key, str) else None
        if canon and canon not in value:
            out[canon] = item
            notes.append(f"Deprecated key '{_join(path, key)}' will be saved as '{canon}'.")
        else:
            out[key] = item
    return out


# --------------------------------------------------------------------------- view (YAML -> state)


def _view(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if value is None:
        raise _Unrepresentable
    viewer = _VIEWERS.get(field.get("type", ""))
    if viewer is None:
        raise _Unrepresentable
    return viewer(field, value, path, notes)


def _view_string(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if field.get("octal") and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"0o{value:o}"
    if not isinstance(value, str) or not value.strip():
        raise _Unrepresentable
    if field["type"] == "string" and "\n" in value:
        raise _Unrepresentable
    return _unshield_str(value)


def _view_bool(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if not isinstance(value, bool):
        raise _Unrepresentable
    return value


def _view_int(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if isinstance(value, bool):
        raise _Unrepresentable
    if isinstance(value, int):
        return value
    if field.get("key") == "uid" and isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        notes.append(f"'{path}' is a numeric string; it will be saved as an integer.")
        return int(value.strip())
    raise _Unrepresentable


def _view_enum(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    choices = field.get("choices") or []
    if isinstance(value, bool):
        text = "true" if value else "false"
        if text in choices:
            return text
        raise _Unrepresentable
    if isinstance(value, str) and value.strip():
        if value.strip() in {"true", "false"} and value.strip() in choices:
            raise _Unrepresentable
        if value != value.strip():
            raise _Unrepresentable
        return _unshield_str(value)
    raise _Unrepresentable


def _emit_flex_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str) or not value.strip():
        return _OMIT
    text = value.strip()
    if text == "true":
        return True
    if text == "false":
        return False
    if _INT_RE.fullmatch(text):
        return int(text)
    return _shield_str(text)


def _view_flex(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        shown = _unshield_str(value)
        if _same(_emit_flex_value(shown), value):
            return shown
    raise _Unrepresentable


def _view_scalar_items(items: list, *, numbers: bool = True) -> list:
    out: list = []
    for item in items:
        if isinstance(item, bool) or item is None:
            raise _Unrepresentable
        if isinstance(item, str):
            if not item.strip() or "\n" in item:
                raise _Unrepresentable
            out.append(_unshield_str(item))
        elif numbers and isinstance(item, (int, float)):
            out.append(item)
        else:
            raise _Unrepresentable
    return out


def _view_string_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if isinstance(value, str):
        token = _block_token(value)
        if token and _PXE_BLOCK_RE.fullmatch(value.strip()):
            return {"__pxe_block__": token}
        if not value.strip() or "\n" in value:
            raise _Unrepresentable
        notes.append(f"'{path}' is a single string; it will be saved as a one-item list.")
        return [_unshield_str(value)]
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    return _view_scalar_items(value)


def _view_string_or_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if value is False and field.get("allow_false"):
        return False
    if isinstance(value, str):
        if not value.strip() or "\n" in value:
            raise _Unrepresentable
        return _unshield_str(value)
    if isinstance(value, list) and value:
        return _view_scalar_items(value)
    raise _Unrepresentable


def _view_enum_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    return _view_scalar_items(value, numbers=False)


def _view_all_or_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if value == "all":
        return {"all": True}
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    return _view_scalar_items(value, numbers=False)


def _view_text(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise _Unrepresentable
    return _unshield_str(value)


def _view_yaml_value(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    yaml_type = field.get("yaml_type", "any")
    if yaml_type == "dict" and not isinstance(value, dict):
        raise _Unrepresentable
    if yaml_type == "list" and not isinstance(value, list):
        raise _Unrepresentable
    if _is_empty(value):
        raise _Unrepresentable
    return _dump_yaml(value)


def _view_command_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if isinstance(value, dict) and value:
        try:
            keys = sorted(value)
        except TypeError:
            keys = sorted(value, key=str)
        notes.append(f"'{path}' uses the map form; it will be saved as a list ordered by key.")
        value = [value[key] for key in keys]
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    out: list = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            if not item.strip():
                raise _Unrepresentable
            out.append(_unshield_str(item))
        elif isinstance(item, list) and item:
            out.append(_view_scalar_items(item))
        else:
            raise _Unrepresentable
    if not out:
        raise _Unrepresentable
    return out


def _view_map(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if not isinstance(value, dict) or not value:
        raise _Unrepresentable
    value_type = field.get("value_type", "scalar")
    rows: list[dict] = []
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise _Unrepresentable
        if value_type == "string":
            if not isinstance(item, str) or not item.strip() or "\n" in item:
                raise _Unrepresentable
            text = _unshield_str(item)
        elif value_type == "yaml":
            if item is None:
                raise _Unrepresentable
            text = _dump_yaml(item)
        else:
            if item is not None and not isinstance(item, _SCALARS):
                raise _Unrepresentable
            if isinstance(item, str) and "\n" in item:
                raise _Unrepresentable
            text = _scalar_text(item)
            try:
                if not _same(_parse_scalar(text), item):
                    raise _Unrepresentable
            except (yaml.YAMLError, ValueError) as exc:
                raise _Unrepresentable from exc
        rows.append({"key": key, "value": text})
    return rows


def _view_package_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if isinstance(value, str):
        token = _block_token(value)
        if token and _PXE_BLOCK_RE.fullmatch(value.strip()):
            return {"__pxe_block__": token}
        raise _Unrepresentable
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    managers = field.get("managers") or []
    rows: list[dict] = []

    def row(item: Any, manager: str) -> dict:
        if isinstance(item, str) and item.strip() and "\n" not in item and item == item.strip():
            return {"name": _unshield_str(item), "version": "", "manager": manager}
        if (
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) and part.strip() and part == part.strip() for part in item)
        ):
            return {"name": _unshield_str(item[0]), "version": _unshield_str(item[1]), "manager": manager}
        raise _Unrepresentable

    for item in value:
        if isinstance(item, dict):
            if not item:
                raise _Unrepresentable
            for manager, pkgs in item.items():
                if manager not in managers or not isinstance(pkgs, list) or not pkgs:
                    raise _Unrepresentable
                rows.extend(row(pkg, manager) for pkg in pkgs)
        else:
            rows.append(row(item, ""))
    if not _same(_emit_package_list(field, rows, path), _shield_value(value)):
        notes.append(f"'{path}' will be saved with one {{manager: [...]}} entry per run of rows.")
    return rows


def _view_disk_layout(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if value is True:
        return {"mode": "true"}
    if value is False:
        return {"mode": "false"}
    if value == "remove":
        return {"mode": "remove"}
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    lines = []
    for item in value:
        if isinstance(item, list):
            lines.append(", ".join(str(part) for part in item))
        elif isinstance(item, (int, str)) and not isinstance(item, bool):
            lines.append(str(item))
        else:
            raise _Unrepresentable
    viewed = {"mode": "custom", "lines": lines}
    if not _same(_emit_layout(viewed), value):
        raise _Unrepresentable
    return viewed


def _view_fields(
    fields: list[dict],
    value: dict,
    path: str,
    notes: list[str],
    *,
    preserve_extra: bool,
    aliases: dict[str, str] | None,
    skip: str | None = None,
) -> dict:
    """View a mapping against sub-fields; unknown or unrepresentable keys go to ``__extra__``."""
    value = _rename_aliases(value, aliases, path, notes)
    known = {field["key"]: field for field in fields}
    out: dict = {}
    extra: dict = {}
    for key, item in value.items():
        field = known.get(key) if isinstance(key, str) and key != skip else None
        if field is None:
            extra[key] = item
            continue
        try:
            out[key] = _view(field, item, _join(path, key), notes)
        except _Unrepresentable:
            extra[key] = item
            notes.append(f"'{_join(path, key)}' is kept in Other keys YAML; the form cannot show this value.")
    if extra:
        if not preserve_extra:
            raise _Unrepresentable
        out["__extra__"] = _dump_yaml(extra)
    return out


def _view_object(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if path == "user" and isinstance(value, str) and value.strip():
        notes.append("'user' is a plain name; it will be saved as {name: ...}.")
        value = {"name": value}
    if not isinstance(value, dict) or not value:
        raise _Unrepresentable
    out = _view_fields(
        field.get("object_fields") or [],
        value,
        path,
        notes,
        preserve_extra=bool(field.get("preserve_extra")),
        aliases=field.get("aliases"),
    )
    required = field.get("require_subkey")
    if required and required not in out:
        raise _Unrepresentable
    if not out:
        raise _Unrepresentable
    return out


def _view_row(field: dict, row: dict, path: str, notes: list[str], *, skip: str | None = None) -> dict:
    viewed = _view_fields(
        field.get("item_fields") or [],
        row,
        path,
        notes,
        preserve_extra=bool(field.get("preserve_extra")),
        aliases=field.get("aliases"),
        skip=skip,
    )
    for item in field.get("item_fields") or []:
        if item.get("required") and item["key"] != skip and item["key"] not in viewed:
            raise _Unrepresentable
    return viewed


def _view_groups(field: dict, value: Any, path: str, notes: list[str]) -> list:
    if isinstance(value, str):
        notes.append(f"'{path}' is a comma-separated string; it will be saved as a list.")
        value = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, dict):
        notes.append(f"'{path}' is a mapping; it will be saved as a list.")
        value = [{key: item} for key, item in value.items()]
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    rows: list[dict] = []
    for item in value:
        if isinstance(item, str) and item.strip() and "\n" not in item:
            rows.append({"name": _unshield_str(item)})
            continue
        if not isinstance(item, dict) or not item:
            raise _Unrepresentable
        for name, members in item.items():
            if not isinstance(name, str) or not name.strip():
                raise _Unrepresentable
            if members is None:
                rows.append({"name": name})
            elif isinstance(members, str) and members.strip():
                rows.append({"name": name, "members": [_unshield_str(members)]})
            elif isinstance(members, list) and members:
                rows.append({"name": name, "members": _view_scalar_items(members, numbers=False)})
            else:
                raise _Unrepresentable
    return rows


def _view_tuple(field: dict, value: Any, path: str, notes: list[str]) -> list:
    columns = [item["key"] for item in field.get("item_fields") or []]
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    rows: list[dict] = []
    for item in value:
        if not isinstance(item, list) or not item or len(item) > len(columns):
            raise _Unrepresentable
        row: dict = {}
        for column, cell in zip(columns, item, strict=False):
            if cell is None or (isinstance(cell, (int, float)) and not isinstance(cell, bool)):
                row[column] = cell
            elif isinstance(cell, str) and "\n" not in cell:
                row[column] = _unshield_str(cell)
            else:
                raise _Unrepresentable
        rows.append(row)
    if not _same(_emit_tuple_rows(field, rows, path), _shield_value(value)):
        raise _Unrepresentable
    return rows


def _view_row_list(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    emit = field.get("emit") or "list"
    key_field = field.get("key_field") or "name"
    if emit == "groups":
        return _view_groups(field, value, path, notes)
    if emit == "tuple":
        return _view_tuple(field, value, path, notes)
    if emit in {"mapping", "mapping_scalar"}:
        if not isinstance(value, dict) or not value:
            raise _Unrepresentable
        rows = []
        for index, (name, payload) in enumerate(value.items()):
            if not isinstance(name, str) or not name.strip():
                raise _Unrepresentable
            if emit == "mapping_scalar":
                if not isinstance(payload, str):
                    raise _Unrepresentable
                rows.append({key_field: name, "value": _unshield_str(payload)})
                continue
            if not isinstance(payload, dict) or key_field in payload:
                raise _Unrepresentable
            viewed = _view_row(field, payload, f"{path}[{index}]", notes, skip=key_field)
            rows.append({key_field: name, **viewed})
        return rows
    if emit == "list_or_map":
        if isinstance(value, dict) and value:
            rows = []
            for name, payload in value.items():
                if not isinstance(name, str) or not name.strip() or not isinstance(payload, str) or not payload:
                    raise _Unrepresentable
                rows.append({key_field: name, "value": _unshield_str(payload)})
            return rows
        if isinstance(value, list) and value:
            rows = []
            for payload in value:
                if not isinstance(payload, str) or not payload.strip():
                    raise _Unrepresentable
                rows.append({"value": _unshield_str(payload)})
            return rows
        raise _Unrepresentable
    if path == "users":
        if isinstance(value, str) and value.strip():
            notes.append("'users' is a single string; it will be saved as a one-item list.")
            value = [value]
        elif isinstance(value, dict) and value:
            notes.append("'users' is a mapping; it will be saved as a list.")
            converted: list = []
            for name, payload in value.items():
                if isinstance(payload, dict) and "name" not in payload:
                    converted.append({"name": name, **payload})
                elif payload is True:
                    converted.append(name)
                else:
                    raise _Unrepresentable
            value = converted
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    rows = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            if not item:
                raise _Unrepresentable
            rows.append(_view_row(field, item, f"{path}[{index}]", notes))
        elif field.get("allow_scalars") and isinstance(item, str) and item.strip() and "\n" not in item:
            rows.append(_unshield_str(item))
        else:
            raise _Unrepresentable
    return rows


def _view_lines(field: dict, value: Any, path: str, notes: list[str]) -> Any:
    if not isinstance(value, list) or not value:
        raise _Unrepresentable
    return _view_scalar_items(value)


_VIEWERS = {
    "string": _view_string,
    "text": _view_text,
    "bool": _view_bool,
    "int": _view_int,
    "enum": _view_enum,
    "flex": _view_flex,
    "string_list": _view_string_list,
    "string_or_list": _view_string_or_list,
    "enum_list": _view_enum_list,
    "all_or_list": _view_all_or_list,
    "yaml_value": _view_yaml_value,
    "command_list": _view_command_list,
    "map": _view_map,
    "package_list": _view_package_list,
    "disk_layout": _view_disk_layout,
    "object": _view_object,
    "row_list": _view_row_list,
    "lines": _view_lines,
}


# --------------------------------------------------------------------------- emit (state -> YAML)


def _emit(field: dict, value: Any, path: str) -> Any:
    emitter = _EMITTERS.get(field.get("type", ""))
    if emitter is None:
        return _OMIT
    return emitter(field, value, path)


def _emit_string(field: dict, value: Any, path: str) -> Any:
    if isinstance(value, bool) or value is None:
        return _OMIT
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str) or _is_empty(value):
        return _OMIT
    key = field.get("key")
    if field.get("credential") or key in _PASSWORD_KEYS:
        _validate_password_value(value, str(key), strict=key != "hashed_passwd", path=path)
    if field.get("octal"):
        match = _OCTAL_RE.fullmatch(value.strip())
        if match:
            return _OctalInt(int(match.group(1), 8))
    return _shield_str(value)


def _emit_text(field: dict, value: Any, path: str) -> Any:
    if not isinstance(value, str) or _is_empty(value):
        return _OMIT
    return _shield_str(value)


def _emit_bool(field: dict, value: Any, path: str) -> Any:
    if value is None or value == "":
        return _OMIT
    if isinstance(value, str):
        if value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise EditorError(f"Expected true or false for {field.get('key')}", path=path)
    return bool(value)


def _emit_int(field: dict, value: Any, path: str) -> Any:
    if _is_empty(value):
        return _OMIT
    if isinstance(value, bool):
        raise EditorError(f"Expected an integer for {field.get('key')}", path=path)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if _INT_RE.fullmatch(text):
        return int(text)
    raise EditorError(f"Expected an integer for {field.get('key')}", path=path)


def _emit_enum(field: dict, value: Any, path: str) -> Any:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str) or not value.strip():
        return _OMIT
    text = value.strip()
    choices = field.get("choices") or []
    if text in {"true", "false"} and text in choices:
        return text == "true"
    return _shield_str(text)


def _emit_flex(field: dict, value: Any, path: str) -> Any:
    return _emit_flex_value(value)


def _emit_scalar_items(items: list) -> list:
    out: list = []
    for item in items:
        if item is None or isinstance(item, (dict, list)):
            continue
        if isinstance(item, str):
            if not item.strip():
                continue
            out.append(_shield_str(item))
        else:
            out.append(item)
    return out


def _emit_string_list(field: dict, value: Any, path: str) -> Any:
    token = _block_token(value)
    if token:
        return f"PXEBLOCK_{token}"
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return _OMIT
    return _emit_scalar_items(value) or _OMIT


def _emit_string_or_list(field: dict, value: Any, path: str) -> Any:
    if value is False:
        return False if field.get("allow_false") else _OMIT
    if isinstance(value, str):
        return _shield_str(value) if value.strip() else _OMIT
    if isinstance(value, list):
        return _emit_scalar_items(value) or _OMIT
    return _OMIT


def _emit_enum_list(field: dict, value: Any, path: str) -> Any:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return _OMIT
    out: list = []
    for item in value:
        if isinstance(item, str) and item.strip() and _shield_str(item.strip()) not in out:
            out.append(_shield_str(item.strip()))
    return out or _OMIT


def _emit_all_or_list(field: dict, value: Any, path: str) -> Any:
    if (isinstance(value, dict) and value.get("all") is True) or value == "all":
        return "all"
    if isinstance(value, list):
        return _emit_scalar_items(value) or _OMIT
    return _OMIT


def _emit_yaml_value(field: dict, value: Any, path: str) -> Any:
    if _is_empty(value):
        return _OMIT
    label = str(field.get("label") or field.get("key"))
    if isinstance(value, str):
        parsed = _load_yaml(value, label, path)
    else:
        parsed = _shield_value(value)
    if parsed is None:
        return _OMIT
    yaml_type = field.get("yaml_type", "any")
    if yaml_type == "dict" and not isinstance(parsed, dict):
        raise EditorError(f"{label} must be a YAML mapping", path=path)
    if yaml_type == "list" and not isinstance(parsed, list):
        raise EditorError(f"{label} must be a YAML list", path=path)
    _check_credentials(parsed, None, None, path, parent=str(field.get("key") or ""), where=path)
    return _blockify(parsed)


def _emit_command_list(field: dict, value: Any, path: str) -> Any:
    if isinstance(value, str):
        value = [line for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        return _OMIT
    out: list = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                out.append(_shield_str(item))
        elif isinstance(item, list):
            argv = _emit_scalar_items(item)
            if argv:
                out.append(argv)
    return out or _OMIT


def _emit_map(field: dict, value: Any, path: str) -> Any:
    if isinstance(value, dict):
        value = [{"key": key, "value": item} for key, item in value.items()]
    if not isinstance(value, list):
        return _OMIT
    value_type = field.get("value_type", "scalar")
    label = str(field.get("label") or field.get("key"))
    out: dict = {}
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") if row.get("key") is not None else "").strip()
        raw = row.get("value")
        text = raw if isinstance(raw, str) else ("" if raw is None else json.dumps(raw))
        if not key or not text.strip():
            continue
        row_path = f"{path}[{index}]"
        if key in out:
            raise EditorError(f"Duplicate key '{key}' in {label}", path=row_path)
        if value_type == "string":
            out[key] = _shield_str(text)
        elif value_type == "yaml":
            out[key] = _blockify(_load_yaml(text, label, row_path))
        else:
            try:
                out[key] = _parse_scalar(text)
            except yaml.YAMLError as exc:
                raise EditorError(f"Invalid value for '{key}' in {label}", path=row_path) from exc
            except ValueError as exc:
                raise EditorError(f"'{key}' in {label} must be a single value", path=row_path) from exc
        _check_credentials({key: out[key]}, None, None, path, parent=str(field.get("key") or ""), where=row_path)
    return out or _OMIT


def _emit_package_list(field: dict, value: Any, path: str) -> Any:
    token = _block_token(value)
    if token:
        return f"PXEBLOCK_{token}"
    if not isinstance(value, list):
        return _OMIT
    managers = field.get("managers") or []
    out: list = []
    group: dict | None = None
    for index, row in enumerate(value):
        if isinstance(row, str):
            row = {"name": row}
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        version = str(row.get("version") or "").strip()
        manager = str(row.get("manager") or "").strip()
        if not name:
            continue
        if manager and manager not in managers:
            raise EditorError(f"Unknown package manager '{manager}'", path=f"{path}[{index}].manager")
        item: Any = [_shield_str(name), _shield_str(version)] if version else _shield_str(name)
        if not manager:
            group = None
            out.append(item)
            continue
        if group is None or manager not in group or len(group) != 1:
            group = {manager: []}
            out.append(group)
        group[manager].append(item)
    return out or _OMIT


def _emit_layout(value: Any) -> Any:
    if not isinstance(value, dict):
        return _OMIT
    mode = str(value.get("mode") or "")
    if mode in {"", "unset"}:
        return _OMIT
    if mode == "true":
        return True
    if mode == "false":
        return False
    if mode == "remove":
        return "remove"
    if mode != "custom":
        return _OMIT
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
    return parsed or _OMIT


def _emit_disk_layout(field: dict, value: Any, path: str) -> Any:
    return _emit_layout(value)


def _merge_extra(target: dict, extra_text: Any, path: str, field: dict, sub_fields: list[dict]) -> None:
    if not isinstance(extra_text, str) or not extra_text.strip():
        return
    extra = _load_yaml(extra_text, f"Other keys of {path or 'the document'}", _join(path, "__extra__"))
    if extra is None:
        return
    if not isinstance(extra, dict):
        raise EditorError(f"Other keys of {path} must be a YAML mapping", path=_join(path, "__extra__"))
    known = {sub["key"]: sub for sub in sub_fields}
    _check_credentials(
        extra, known, field.get("aliases"), path, parent=str(field.get("key") or ""), where=_join(path, "__extra__")
    )
    for key, item in extra.items():
        if key in target:
            raise EditorError(
                f"'{key}' is set in the form and in Other keys of {path}; keep only one.",
                path=_join(path, str(key)),
            )
        target[key] = _blockify(item)


def _emit_fields(fields: list[dict], state: dict, path: str, *, skip: str | None = None) -> dict:
    """Emit known sub-fields in state order (keeps the original key order on a no-op apply)."""
    known = {field["key"]: field for field in fields}
    out: dict = {}
    for key in state:
        field = known.get(key)
        if field is None or key == skip:
            continue
        emitted = _emit(field, state[key], _join(path, key))
        if emitted is not _OMIT:
            out[key] = emitted
    return out


def _emit_object(field: dict, value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        return _OMIT
    inner = _emit_fields(field.get("object_fields") or [], value, path)
    if field.get("preserve_extra"):
        _merge_extra(inner, value.get("__extra__"), path, field, field.get("object_fields") or [])
    required = field.get("require_subkey")
    if required and _is_empty(inner.get(required)):
        return _OMIT
    return inner or _OMIT


def _coerce_rows(field: dict, rows: Any, path: str) -> list:
    label = str(field.get("label") or field.get("key"))
    if isinstance(rows, str):
        if not rows.strip():
            return []
        parsed = _load_yaml(rows, label, path)
        if parsed is None:
            return []
        if not isinstance(parsed, list):
            raise EditorError(f"{label} must be a YAML list", path=path)
        return parsed
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise EditorError(f"{label} must be a list", path=path)
    return rows


def _emit_row_dict(field: dict, row: dict, path: str, *, skip: str | None = None) -> dict:
    item_fields = field.get("item_fields") or []
    emitted = _emit_fields(item_fields, row, path, skip=skip)
    if field.get("preserve_extra"):
        known = {item["key"]: item for item in item_fields}
        for key, item in row.items():
            if key in known or str(key).startswith("__") or key in emitted or _is_empty(item):
                continue
            emitted[key] = _shield_value(item)
            _check_credentials(
                {key: emitted[key]}, known, field.get("aliases"), path, parent=str(field.get("key") or "")
            )
        _merge_extra(emitted, row.get("__extra__"), path, field, item_fields)
    return emitted


def _emit_tuple_rows(field: dict, rows: list, path: str) -> list:
    columns = [item["key"] for item in field.get("item_fields") or []]
    out: list = []
    for row in rows:
        if isinstance(row, list):
            row = dict(zip(columns, row, strict=False))
        if not isinstance(row, dict):
            continue
        values: list = []
        for column in columns:
            cell = row.get(column, "")
            if isinstance(cell, str):
                cell = _shield_str(cell)
            values.append(cell)
        while values and isinstance(values[-1], str) and not values[-1].strip():
            values.pop()
        if values:
            out.append(values)
    return out


def _emit_groups(field: dict, rows: list, path: str) -> list:
    out: list = []
    for index, row in enumerate(rows):
        if isinstance(row, str):
            row = {"name": row}
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        members = _emit_string_list({"key": "members"}, row.get("members"), f"{path}[{index}].members")
        if members is _OMIT or isinstance(members, str):
            out.append(_shield_str(name))
        else:
            out.append({_shield_str(name): members})
    return out


def _emit_row_list(field: dict, value: Any, path: str) -> Any:
    rows = _coerce_rows(field, value, path)
    emit = field.get("emit") or "list"
    if emit == "tuple":
        return _emit_tuple_rows(field, rows, path) or _OMIT
    if emit == "groups":
        return _emit_groups(field, rows, path) or _OMIT
    item_fields = field.get("item_fields") or []
    required = [item["key"] for item in item_fields if item.get("required")]
    key_field = field.get("key_field") or "name"
    label = str(field.get("label") or field.get("key"))
    out_rows: list = []
    mapped: dict = {}
    named: list = []
    for index, row in enumerate(rows):
        row_path = f"{path}[{index}]"
        if not isinstance(row, dict):
            if field.get("allow_scalars") and isinstance(row, str) and row.strip():
                out_rows.append(_shield_str(row))
            continue
        if field.get("entry_rule") == "chpasswd":
            if _is_empty(row.get("name")):
                raise EditorError("chpasswd user name is required", path=f"{row_path}.name")
            kind = str(row.get("type") or "hash")
            if kind != "RANDOM" and _is_empty(row.get("password")):
                raise EditorError("chpasswd user password is required", path=f"{row_path}.password")
        elif emit in {"list", "mapping"} and any(_is_empty(row.get(req)) for req in required):
            continue
        if emit in {"mapping", "mapping_scalar"}:
            name = str(row.get(key_field) or "").strip()
            if not name:
                continue
            if name in mapped:
                raise EditorError(f"Duplicate name '{name}' in {label}", path=f"{row_path}.{key_field}")
            if emit == "mapping_scalar":
                if not _is_empty(row.get("value")):
                    mapped[name] = _shield_value(row.get("value"))
                continue
            mapped[name] = _emit_row_dict(field, row, row_path, skip=key_field)
            continue
        if emit == "list_or_map":
            text = row.get("value")
            if not isinstance(text, str) or not text.strip():
                continue
            name = str(row.get(key_field) or "").strip()
            named.append((name, _shield_str(text)))
            continue
        emitted = _emit_row_dict(field, row, row_path)
        if emitted:
            out_rows.append(emitted)
    if emit in {"mapping", "mapping_scalar"}:
        return mapped or _OMIT
    if emit == "list_or_map":
        if not named:
            return _OMIT
        with_names = [name for name, _ in named if name]
        if with_names and len(with_names) != len(named):
            raise EditorError(f"Give every {label} entry a name, or none of them", path=path)
        if with_names:
            result: dict = {}
            for name, text in named:
                if name in result:
                    raise EditorError(f"Duplicate name '{name}' in {label}", path=path)
                result[name] = text
            return result
        return [text for _, text in named]
    return out_rows or _OMIT


def _emit_lines(field: dict, value: Any, path: str) -> Any:
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return _OMIT
    return _emit_scalar_items(value) or _OMIT


_EMITTERS = {
    "string": _emit_string,
    "text": _emit_text,
    "bool": _emit_bool,
    "int": _emit_int,
    "enum": _emit_enum,
    "flex": _emit_flex,
    "string_list": _emit_string_list,
    "string_or_list": _emit_string_or_list,
    "enum_list": _emit_enum_list,
    "all_or_list": _emit_all_or_list,
    "yaml_value": _emit_yaml_value,
    "command_list": _emit_command_list,
    "map": _emit_map,
    "package_list": _emit_package_list,
    "disk_layout": _emit_disk_layout,
    "object": _emit_object,
    "row_list": _emit_row_list,
    "lines": _emit_lines,
}


# --------------------------------------------------------------------------- documents


def _emit_document(raw: dict) -> tuple[dict, dict[str, list[str]]]:
    cc: dict = {}
    owners: dict[str, list[str]] = {}
    for node in NODES:
        for field in node.get("fields", []):
            key = field["key"]
            if key not in raw:
                continue
            try:
                value = _emit(field, raw.get(key), key)
            except EditorError as exc:
                if exc.node is None:
                    exc.node = node["id"]
                raise
            except SeedError as exc:
                raise EditorError(str(exc), node=node["id"], path=key) from exc
            if value is not _OMIT:
                cc[key] = value
                owners.setdefault(node["id"], []).append(key)
    return cc, owners


def prune_document(raw: dict) -> dict:
    """Return cloud-config keys to emit from posted editor JSON."""
    if not isinstance(raw, dict):
        raise SeedError("Editor document must be a JSON object")
    return _emit_document(raw)[0]


def _parse_seed(seed_text: str) -> tuple[dict, str]:
    text = seed_text or ""
    if not text.strip():
        return {}, "cloud-config"
    try:
        parsed = yaml.safe_load(shield_tokens(text))
    except yaml.YAMLError as exc:
        raise SeedError("user-data is not valid YAML") from exc
    if parsed is None:
        return {}, "cloud-config"
    if not isinstance(parsed, dict):
        raise SeedError("user-data must be a YAML mapping")
    if isinstance(parsed.get("autoinstall"), dict):
        return parsed, "autoinstall"
    return parsed, "cloud-config"


def _cloud_config_mapping(parsed: dict, mode: str) -> dict:
    if mode == "autoinstall":
        user_data = parsed["autoinstall"].get("user-data")
        return dict(user_data) if isinstance(user_data, dict) else {}
    return dict(parsed)


def _rename_top(cc: dict, notes: list[str]) -> dict:
    out: dict = {}
    for key, value in cc.items():
        canon = ALIASES.get(key) if isinstance(key, str) else None
        if canon and canon not in cc:
            out[canon] = value
            notes.append(f"Deprecated key '{key}' will be saved as '{canon}'.")
        else:
            if canon:
                notes.append(f"Both '{key}' and '{canon}' are set; '{key}' stays in Advanced YAML.")
            out[key] = value
    return out


def _header_lines(seed_text: str) -> list[str]:
    lines: list[str] = []
    for line in (seed_text or "").lstrip("﻿").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        lines.append(line.rstrip())
    if not any(line.strip() == "#cloud-config" for line in lines):
        lines.insert(0, "#cloud-config")
    return lines


def _dump(value: Any) -> str:
    dumped = yaml.dump(_blockify(value), Dumper=_LiteralDumper, default_flow_style=False, sort_keys=False, **_DUMP_OPTS)
    if dumped.endswith("\n...\n"):
        dumped = dumped[: -len("...\n")]
    return unshield_scalars(restore_block_lines(dumped))


def _render(parsed: dict, header: list[str]) -> str:
    body = _dump(parsed) if parsed else ""
    text = "\n".join(header) + "\n" + body
    return text if text.endswith("\n") else text + "\n"


def cloud_config_view(seed_text: str) -> dict:
    """Return mode, structured doc, extra_yaml (unmodeled keys), notices, and installer keys."""
    parsed, mode = _parse_seed(seed_text)
    notes: list[str] = []
    cc = _rename_top(_cloud_config_mapping(parsed, mode), notes)
    doc: dict = {}
    consumed: set[str] = set()
    for node in NODES:
        for field in node.get("fields", []):
            key = field["key"]
            if key not in cc:
                continue
            try:
                doc[key] = _view(field, cc[key], key, notes)
                consumed.add(key)
            except _Unrepresentable:
                notes.append(f"'{key}' is shown in Advanced YAML; the form cannot show this value.")
    extra = {key: value for key, value in cc.items() if key not in consumed}
    installer_keys: list[str] = []
    if mode == "autoinstall":
        installer_keys = [str(key) for key in parsed["autoinstall"] if key != "user-data"]
    return {
        "mode": mode,
        "doc": doc,
        "extra_yaml": _dump_yaml(extra) if extra else "",
        "notices": notes,
        "installer_keys": installer_keys,
        "advanced_keys": list(ADVANCED_KEYS),
    }


def safe_cloud_config_view(seed_text: str) -> dict:
    """``cloud_config_view`` for page renders: an unreadable seed gives an empty editor plus ``error``."""
    try:
        return cloud_config_view(seed_text)
    except SeedError as exc:
        return {
            "mode": "cloud-config",
            "doc": {},
            "extra_yaml": "",
            "notices": [],
            "installer_keys": [],
            "advanced_keys": list(ADVANCED_KEYS),
            "error": str(exc),
        }


def _load_extra(extra_yaml: str) -> dict:
    text = extra_yaml or ""
    if not text.strip():
        return {}
    try:
        loaded = yaml.safe_load(shield_tokens(_with_final_newline(text)))
    except yaml.YAMLError as exc:
        raise EditorError("Advanced YAML is not valid YAML", path="__advanced__") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise EditorError("Advanced YAML must be a mapping", path="__advanced__")
    return loaded


def _build(seed_text: str, cc_json: str, extra_yaml: str) -> dict:
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
    pruned, owners = _emit_document(raw_doc)
    # Safety net over everything the form emitted (the targeted checks above report better paths).
    _check_top_credentials(pruned)
    extra = _load_extra(extra_yaml)
    _check_top_credentials(extra, where="__advanced__")
    for key in extra:
        if key in pruned:
            node = node_for_key(key)
            label = node["label"] if node else key
            raise EditorError(
                f"Move '{key}' to the {label} section.", node=node["id"] if node else None, path="__advanced__"
            )
    parsed, mode = _parse_seed(seed_text)
    before = copy.deepcopy(parsed)
    notes: list[str] = []
    original = _rename_top(_cloud_config_mapping(parsed, mode), notes)
    combined = {**_blockify(extra), **pruned}
    merged = {key: combined[key] for key in original if key in combined}
    merged.update({key: value for key, value in combined.items() if key not in merged})
    if mode == "autoinstall":
        auto = parsed["autoinstall"]
        if merged or "user-data" in auto:
            auto["user-data"] = merged
    else:
        parsed = merged
    return {
        "parsed": parsed,
        "before": before,
        "mode": mode,
        "merged": merged,
        "owners": owners,
        "header": _header_lines(seed_text),
    }


# --------------------------------------------------------------------------- minimal rewrites
#
# A full re-dump normalises quoting and flow style across the whole file (installer block included).
# To keep diffs small: a no-op apply returns the seed unchanged, and in autoinstall mode only the
# ``user-data`` lines are rewritten. Every splice is re-parsed and compared; on any doubt the full
# dump is used instead.

_PXEBLOCK_RE = re.compile(r"PXEBLOCK_([a-z_]+)")


def _comparable(value: Any) -> Any:
    """Normalise token shielding so a parsed seed and editor output compare by meaning."""
    if isinstance(value, str):
        match = _PXEBLOCK_RE.fullmatch(value)
        return f"{{{{{match.group(1)}}}}}" if match else _unshield_str(value)
    if isinstance(value, list):
        return [_comparable(item) for item in value]
    if isinstance(value, dict):
        return {(_comparable(key) if isinstance(key, str) else key): _comparable(item) for key, item in value.items()}
    if isinstance(value, _OctalInt):
        return int(value)
    return value


def _equivalent(left: Any, right: Any) -> bool:
    """Deep, type-strict equality that ignores mapping key order."""
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_equivalent(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equivalent(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return False
    return _same(left, right)


def _same_meaning(left: Any, right: Any) -> bool:
    return _equivalent(_comparable(left), _comparable(right))


def _has_cloud_config_header(seed_text: str) -> bool:
    for line in (seed_text or "").lstrip("﻿").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            return False
        if stripped == "#cloud-config":
            return True
    return False


def _mapping_value(node: Any, key: str) -> tuple[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        return None, None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node, value_node
    return None, None


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_end(lines: list[str], start: int, column: int) -> int:
    """Index after the last content line of the block that begins at ``lines[start]`` (key at ``column``).

    Trailing blank and comment lines stay outside the block.
    """
    end = start + 1
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _indent_of(lines[index]) <= column:
            break
        end = index + 1
    return end


def _indented_block(value: Any, key: str, column: int, newline: str) -> list[str]:
    text = _dump({key: value})
    pad = " " * column
    return [(pad + line if line.strip() else "") + newline for line in text.rstrip("\n").split("\n")]


def _splice_user_data(seed_text: str, built: dict) -> str | None:
    """Rewrite only ``autoinstall.user-data`` in the original text, or None when that is not safe."""
    parsed = built["parsed"]
    auto = parsed.get("autoinstall") if isinstance(parsed, dict) else None
    if not isinstance(auto, dict) or "user-data" not in auto or not _has_cloud_config_header(seed_text):
        return None
    before_auto = built["before"].get("autoinstall")
    rest_before = {key: value for key, value in built["before"].items() if key != "autoinstall"}
    rest_after = {key: value for key, value in parsed.items() if key != "autoinstall"}
    if not isinstance(before_auto, dict) or not _same_meaning(rest_before, rest_after):
        return None
    if not _same_meaning(
        {k: v for k, v in before_auto.items() if k != "user-data"},
        {k: v for k, v in auto.items() if k != "user-data"},
    ):
        return None
    try:
        root = yaml.compose(shield_tokens(seed_text))
    except yaml.YAMLError:
        return None
    auto_key, auto_node = _mapping_value(root, "autoinstall")
    if auto_key is None or not isinstance(auto_node, yaml.MappingNode) or auto_node.flow_style or not auto_node.value:
        return None
    lines = seed_text.splitlines(keepends=True)
    ud_key, _ud_node = _mapping_value(auto_node, "user-data")
    if ud_key is not None:
        start, column = ud_key.start_mark.line, ud_key.start_mark.column
        if start >= len(lines) or lines[start][:column].strip():
            return None
        end = _block_end(lines, start, column)
    else:
        column = auto_node.value[0][0].start_mark.column
        start = end = _block_end(lines, auto_key.start_mark.line, auto_key.start_mark.column)
        if column <= auto_key.start_mark.column:
            return None
    head = lines[:start]
    newline = "\r\n" if "\r\n" in seed_text else "\n"
    if head and not head[-1].endswith("\n"):
        head[-1] += newline
    spliced = "".join([*head, *_indented_block(auto["user-data"], "user-data", column, newline), *lines[end:]])
    try:
        reparsed = yaml.safe_load(shield_tokens(spliced))
    except yaml.YAMLError:
        return None
    return spliced if _same_meaning(reparsed, parsed) else None


_BLOCK_TOKEN_TEXT_RE = re.compile(r"\{\{(?:" + "|".join(sorted(_BLOCK_MARKERS)) + r")\}\}")


def _block_tokens_on_own_lines(seed_text: str) -> bool:
    """List placeholders must sit on their own line; the full dump moves inline ones there."""
    for line in seed_text.splitlines():
        if _BLOCK_TOKEN_TEXT_RE.search(line) and not _BLOCK_TOKEN_TEXT_RE.fullmatch(line.strip()):
            return False
    return True


def _render_seed(seed_text: str, built: dict) -> str:
    if not _block_tokens_on_own_lines(seed_text):
        return _render(built["parsed"], built["header"])
    if _has_cloud_config_header(seed_text) and _same_meaning(built["before"], built["parsed"]):
        return seed_text
    if built["mode"] == "autoinstall":
        spliced = _splice_user_data(seed_text, built)
        if spliced is not None:
            return spliced
    return _render(built["parsed"], built["header"])


def apply_cloudinit_editor(seed_text: str, cc_json: str, extra_yaml: str) -> str:
    """Return full seed text with the cloud-config mapping replaced. Raises SeedError."""
    built = _build(seed_text, cc_json, extra_yaml)
    return _render_seed(seed_text, built)


def cloud_config_preview(seed_text: str, cc_json: str, extra_yaml: str) -> dict:
    """Like apply, plus the cloud-config user-data alone and each node's YAML fragment."""
    built = _build(seed_text, cc_json, extra_yaml)
    seed = _render_seed(seed_text, built)
    merged = built["merged"]
    user_data = seed if built["mode"] == "cloud-config" else _render(merged, ["#cloud-config"])
    node_yaml = {
        node_id: _dump({key: merged[key] for key in keys if key in merged}).rstrip("\n")
        for node_id, keys in built["owners"].items()
    }
    return {"seed": seed, "user_data": user_data, "node_yaml": node_yaml}
