"""Build module examples and structured editor nodes from a cloud-init checkout.

Usage:
  python scripts/extract_cloudinit_examples.py --src PATH [--check]

PATH is a cloud-init 26.2 tree (doc/module-docs and
cloudinit/config/schemas/schema-cloud-config-v1.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

DOCS_VERSION = "26.2"
ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_PATH = ROOT / "src" / "cloudinit" / "module_examples.json"
NODES_PATH = ROOT / "src" / "cloudinit" / "generated_nodes.py"

CREDENTIAL_HASH = {"passwd", "hashed_passwd"}
CREDENTIAL_PASSWORD = {"password", "plain_text_passwd"}
SECRET_TEXT = {
    "token",
    "registration_key",
    "trust_password",
    "activation_key",
    "validation_cert",
}
TEXT_KEYS = {
    "content",
    "config",
    "template",
    "preseed",
    "conf",
    "sources_list",
    "validation_cert",
    "ca_cert",
}
PRIVATE_KEY_SUFFIX = "_private"

GROUP_FOR = {
    "cc_disk_setup": "Disks",
    "cc_mounts": "Disks",
    "cc_fan": "Network",
    "cc_wireguard": "Network",
    "cc_ntp": "Network",
    "cc_install_hotplug": "Agents and distro",
    "cc_keyboard": "Identity",
    "cc_apt_configure": "Packages",
    "cc_apt_pipelining": "Packages",
    "cc_snap": "Packages",
    "cc_apk_configure": "Packages",
    "cc_yum_add_repo": "Packages",
    "cc_zypper_add_repo": "Packages",
    "cc_ca_certs": "Agents and distro",
    "cc_rsyslog": "Agents and distro",
    "cc_ansible": "Agents and distro",
    "cc_puppet": "Agents and distro",
    "cc_chef": "Agents and distro",
    "cc_salt_minion": "Agents and distro",
    "cc_mcollective": "Agents and distro",
    "cc_landscape": "Agents and distro",
    "cc_ubuntu_pro": "Agents and distro",
    "cc_byobu": "Agents and distro",
    "cc_lxd": "Agents and distro",
    "cc_ubuntu_drivers": "Agents and distro",
    "cc_grub_dpkg": "Agents and distro",
    "cc_rh_subscription": "Agents and distro",
    "cc_spacewalk": "Agents and distro",
    "cc_raspberry_pi": "Agents and distro",
    "cc_seed_random": "Agents and distro",
    "cc_scripts_vendor": "Files and commands",
    "cc_users_groups": "Users and SSH",
    "cc_ssh": "Users and SSH",
    "cc_set_passwords": "Users and SSH",
    "cc_write_files": "Files and commands",
    "cc_resolv_conf": "Network",
    "cc_power_state_change": "Power and phone home",
    "cc_phone_home": "Power and phone home",
    "cc_keys_to_console": "Agents and distro",
    "cc_ssh_authkey_fingerprints": "Users and SSH",
}

NODE_META = {
    "disk_setup": ("disk_setup", "Disk setup", "Partition tables, device aliases, and filesystems."),
    "mounts": ("mounts", "Mounts", "fstab entries and swap files."),
    "fan": ("fan", "Fan", "Ubuntu Fan networking."),
    "wireguard": ("wireguard", "WireGuard", "WireGuard interfaces."),
    "updates": ("install_hotplug", "Install hotplug", "When to apply network hotplug updates."),
    "keyboard": ("keyboard", "Keyboard", "Keyboard layout. The object is omitted without a layout."),
    "apt": ("apt", "Apt", "Apt mirrors, sources, and pipelining."),
    "snap": ("snap", "Snap", "Snap assertions and commands."),
    "apk_repos": ("apk_repos", "APK", "Alpine APK repositories."),
    "yum_repos": ("yum_repos", "Yum repos", "Yum repository files."),
    "zypper": ("zypper", "Zypper", "Zypper repositories."),
    "ca_certs": ("ca_certs", "CA certificates", "Trusted CA certificates."),
    "rsyslog": ("rsyslog", "Rsyslog", "Rsyslog configuration."),
    "ansible": ("ansible", "Ansible", "Ansible install and pull."),
    "puppet": ("puppet", "Puppet", "Puppet agent configuration."),
    "chef": ("chef", "Chef", "Chef client configuration."),
    "salt_minion": ("salt_minion", "Salt", "Salt minion configuration."),
    "mcollective": ("mcollective", "MCollective", "MCollective server configuration."),
    "landscape": ("landscape", "Landscape", "Landscape client registration."),
    "ubuntu_pro": ("ubuntu_pro", "Ubuntu Pro", "Ubuntu Pro attach token and services."),
    "byobu_by_default": ("byobu", "Byobu", "Byobu on login."),
    "lxd": ("lxd", "LXD", "LXD init. Preseed is opaque YAML passed to lxd init."),
    "drivers": ("drivers", "Ubuntu drivers", "Ubuntu driver packages."),
    "grub_dpkg": ("grub_dpkg", "GRUB dpkg", "GRUB install device debconf settings."),
    "rh_subscription": ("rh_subscription", "Red Hat subscription", "Red Hat subscription-manager."),
    "spacewalk": ("spacewalk", "Spacewalk", "Spacewalk registration."),
    "rpi": ("rpi", "Raspberry Pi", "Raspberry Pi interfaces."),
    "random_seed": ("random_seed", "Seed random", "Kernel random seed data."),
    "vendor_data": ("vendor_data", "Vendor data", "Whether vendor scripts run."),
    "user": ("default_user", "Default user", "Override the default user object."),
}

SKIP_TOP = {
    "autoinstall",
    "reporting",
    "ubuntu_advantage",
    "ca-certs",
    "grub-dpkg",
    "cloud_config_modules",
    "cloud_final_modules",
    "cloud_init_modules",
    "merge_how",
    "merge_type",
    "output",
    "system_info",
    "version",
    "launch-index",
    "migrate",
}

TOP_ALIASES = {
    "ubuntu_advantage": "ubuntu_pro",
    "ca-certs": "ca_certs",
    "grub-dpkg": "grub_dpkg",
}


def _label(key: str) -> str:
    text = key.replace("_", " ").replace("-", " ")
    return text[:1].upper() + text[1:] if text else key


def _resolve(node: object, root: dict) -> object:
    if isinstance(node, dict) and "$ref" in node:
        ref = str(node["$ref"])
        if ref.startswith("#/$defs/"):
            return root["$defs"][ref.split("/", 2)[-1]]
    return node


def _merge(node: object, root: dict) -> dict:
    node = _resolve(node, root)
    if not isinstance(node, dict):
        return {}
    if "allOf" in node:
        merged: dict = {}
        for part in node["allOf"]:
            piece = _merge(part, root)
            for key, value in piece.items():
                if key == "properties" and isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
        for key, value in node.items():
            if key != "allOf":
                merged[key] = value
        return merged
    return node


def _types(schema: dict) -> set[str]:
    raw = schema.get("type")
    if isinstance(raw, list):
        return set(raw)
    if isinstance(raw, str):
        return {raw}
    return set()


def _credential(key: str) -> str | None:
    if key in CREDENTIAL_HASH:
        return "{{password_hash}}"
    if key in CREDENTIAL_PASSWORD:
        return "{{password}}"
    return None


def _string_field(key: str, schema: dict) -> dict:
    field: dict = {"key": key, "label": schema.get("label") or _label(key), "type": "string"}
    hint = _credential(key)
    if hint:
        field["credential"] = True
        field["placeholder"] = hint
    elif key in SECRET_TEXT or key.endswith(PRIVATE_KEY_SUFFIX):
        field["secret"] = True
        field["type"] = "text" if key.endswith(PRIVATE_KEY_SUFFIX) or key in TEXT_KEYS else "string"
    elif key in TEXT_KEYS or key.endswith("_private"):
        field["type"] = "text"
    if "enum" in schema and field["type"] == "string":
        field["type"] = "enum"
        field["choices"] = [""] + [str(item) for item in schema["enum"]]
    if "default" in schema and not isinstance(schema["default"], (dict, list)):
        field["default"] = schema["default"]
    return field


def _expand_ssh_keys() -> dict:
    keys = []
    for kind in ("rsa", "ecdsa", "ed25519"):
        for part in ("private", "public", "certificate"):
            key = f"{kind}_{part}"
            field = {
                "key": key,
                "label": _label(key),
                "type": "text" if part == "private" else "string",
            }
            if part == "private":
                field["secret"] = True
            keys.append(field)
    return {
        "key": "ssh_keys",
        "label": "Host keys",
        "type": "object",
        "object_fields": keys,
        "preserve_extra": True,
    }


def _union_kind(schema: dict, root: dict) -> str:
    options = schema.get("oneOf") or schema.get("anyOf") or []
    kinds: set[str] = set()
    for option in options:
        kinds |= _types(_merge(option, root))
    return ",".join(sorted(kinds))


def convert_field(key: str, schema: object, root: dict, depth: int = 0) -> dict:
    schema = _merge(schema, root)
    if key == "layout":
        return {"key": key, "label": "Layout", "type": "disk_layout"}
    if key == "ssh_keys":
        return _expand_ssh_keys()
    if key == "apt_pipelining":
        return {"key": key, "label": "Apt pipelining", "type": "flex"}
    if key in {"assertions", "commands"} and depth >= 1:
        return {
            "key": key,
            "label": _label(key),
            "type": "row_list",
            "emit": "list_or_map",
            "key_field": "name",
            "item_fields": [
                {"key": "name", "label": "Name", "type": "string"},
                {"key": "value", "label": "Value", "type": "text"},
            ],
        }
    kinds = _union_kind(schema, root)
    if kinds and "object" not in kinds and "array" not in kinds and kinds != "string":
        field = _string_field(key, schema)
        field["type"] = "flex"
        return field
    if "enum" in schema and _types(schema) <= {"string"} | set():
        return _string_field(key, schema)
    types = _types(schema)
    if depth >= 2 and (types & {"object", "array"} or "object" in kinds or "array" in kinds):
        return {"key": key, "label": _label(key), "type": "yaml_value"}
    if types == {"boolean"}:
        field = {"key": key, "label": _label(key), "type": "bool"}
        if "default" in schema:
            field["default"] = schema["default"]
        return field
    if types <= {"integer", "number"} and types:
        return {"key": key, "label": _label(key), "type": "int"}
    if types == {"string"} or types == {"string", "null"}:
        return _string_field(key, schema)
    if "array" in types:
        return _array_field(key, schema, root, depth)
    if "object" in types or "properties" in schema or "patternProperties" in schema:
        return _object_field(key, schema, root, depth)
    return {"key": key, "label": _label(key), "type": "yaml_value"}


def _array_field(key: str, schema: dict, root: dict, depth: int) -> dict:
    items = _merge(schema.get("items") or {}, root)
    item_types = _types(items)
    if key == "post":
        return {"key": key, "label": "POST fields", "type": "all_or_list"}
    if "array" in item_types:
        cols = ["fs_spec", "fs_file", "fs_vfstype", "fs_mntops", "fs_freq", "fs_passno"]
        if key != "mounts":
            cols = [f"col{i}" for i in range(6)]
        return {
            "key": key,
            "label": _label(key),
            "type": "row_list",
            "emit": "tuple",
            "item_fields": [{"key": col, "label": _label(col), "type": "string"} for col in cols],
        }
    if "object" in item_types or "properties" in items:
        props, _aliases = _canonical_props(items.get("properties") or {})
        required = set(items.get("required") or [])
        item_fields = []
        for prop, prop_schema in props.items():
            field = convert_field(prop, prop_schema, root, depth + 1)
            if prop in required:
                field["required"] = True
            item_fields.append(field)
        field = {
            "key": key,
            "label": _label(key),
            "type": "row_list",
            "item_fields": item_fields,
            "preserve_extra": True,
        }
        if key == "users" and depth >= 1:
            field["entry_rule"] = "chpasswd"
        return field
    field = {"key": key, "label": _label(key), "type": "string_list"}
    if key == "packages":
        field["block_token"] = "packages"
    if key == "ssh_authorized_keys":
        field["block_token"] = "ssh_keys"
    return field


def _canonical_props(properties: dict) -> tuple[dict, dict]:
    aliases: dict[str, str] = {}
    skip: set[str] = set()
    for key in properties:
        if "-" in key and "/" not in key:
            underscored = key.replace("-", "_")
            if underscored in properties and underscored != key:
                skip.add(key)
                aliases[key] = underscored
    kept = {key: value for key, value in properties.items() if key not in skip}
    return kept, aliases


def _object_field(key: str, schema: dict, root: dict, depth: int) -> dict:
    pattern = schema.get("patternProperties") or {}
    properties = schema.get("properties") or {}
    if pattern and not properties:
        value_schema = _merge(next(iter(pattern.values())), root)
        if _types(value_schema) <= {"string", "null"} and "object" not in _types(value_schema):
            return {
                "key": key,
                "label": _label(key),
                "type": "row_list",
                "emit": "mapping_scalar",
                "key_field": "name",
                "item_fields": [
                    {"key": "name", "label": "Name", "type": "string", "required": True},
                    {"key": "value", "label": "Value", "type": "string"},
                ],
            }
        inner = convert_field("value", value_schema, root, depth + 1)
        key_name = (
            "_key"
            if inner.get("type") == "object"
            and any(item.get("key") == "name" for item in inner.get("object_fields") or [])
            else "name"
        )
        item_fields = [{"key": key_name, "label": "Key", "type": "string", "required": True}]
        if inner.get("type") == "object":
            item_fields.extend(inner.get("object_fields") or [])
        else:
            item_fields.append(inner)
        return {
            "key": key,
            "label": _label(key),
            "type": "row_list",
            "emit": "mapping",
            "key_field": key_name,
            "item_fields": item_fields,
            "preserve_extra": True,
        }
    props, _aliases = _canonical_props(properties)
    if not props:
        return {"key": key, "label": _label(key), "type": "lines", "parser": "options"}
    required = set(schema.get("required") or [])
    object_fields = []
    for prop, prop_schema in props.items():
        field = convert_field(prop, prop_schema, root, depth + 1)
        if prop in required:
            field["required"] = True
        object_fields.append(field)
    field = {
        "key": key,
        "label": _label(key),
        "type": "object",
        "object_fields": object_fields,
        "preserve_extra": True,
    }
    if key == "keyboard":
        field["require_subkey"] = "layout"
    return field


def _collect_aliases(schema: dict, root: dict, found: dict[str, str]) -> None:
    schema = _merge(schema, root)
    _props, aliases = _canonical_props(schema.get("properties") or {})
    found.update(aliases)
    for prop_schema in _props.values():
        _collect_aliases(prop_schema if isinstance(prop_schema, dict) else {}, root, found)
    items = schema.get("items")
    if isinstance(items, dict):
        _collect_aliases(items, root, found)


def build_nodes(root_schema: dict) -> tuple[list[dict], dict[str, str], dict]:
    defs = root_schema["$defs"]
    aliases = dict(TOP_ALIASES)
    patches: dict[str, list] = {
        "users": [],
        "write_files": [],
        "ntp": [],
        "resolv_conf": [],
        "power_state": [],
        "phone_home": [],
        "ssh": [],
        "keys_to_console": [],
    }
    nodes: list[dict] = []
    seen: set[str] = set()
    for name, group in GROUP_FOR.items():
        module = defs.get(name) or {}
        props = module.get("properties") or {}
        _collect_aliases(module, root_schema, aliases)
        for key, prop_schema in props.items():
            if key in SKIP_TOP or key in seen:
                continue
            if key in {"device_aliases", "fs_setup"}:
                field = convert_field(key, prop_schema, root_schema, 0)
                _append_field(nodes, "disk_setup", group, field)
                seen.add(key)
                continue
            if key in {"mount_default_fields", "swap"}:
                field = convert_field(key, prop_schema, root_schema, 0)
                _append_field(nodes, "mounts", group, field)
                seen.add(key)
                continue
            if key in {"users", "groups", "ssh_authorized_keys", "ssh_deletekeys", "ssh_genkeytypes"}:
                continue
            field = convert_field(key, prop_schema, root_schema, 0)
            if key == "user":
                nodes.append(_node_for("user", group, [field]))
                seen.add(key)
                continue
            if key in {"ssh_keys", "ssh_publish_hostkeys"}:
                patches["ssh"].append(field)
                seen.add(key)
                continue
            if key in {"emit_keys_to_console"}:
                patches["ssh"].append(field)
                seen.add(key)
                continue
            if key == "ssh_key_console_blacklist":
                patches["keys_to_console"].append(field)
                seen.add(key)
                continue
            if key == "authkey_hash":
                patches["ssh"].append(field)
                seen.add(key)
                continue
            if key == "yum_repo_dir":
                field_list = patches.setdefault("yum_repos", [])
                field_list.append(field)
                seen.add(key)
                continue
            meta_key = "apt" if key == "apt_pipelining" else key
            if meta_key not in NODE_META and key not in NODE_META:
                if key in {"peers", "allow"}:
                    patches["ntp"].append(field)
                    seen.add(key)
                    continue
                if key == "sortlist":
                    patches["resolv_conf"].append(field)
                    seen.add(key)
                    continue
                if key == "delay":
                    patches["power_state"].append(field)
                    seen.add(key)
                    continue
                continue
            node_key = meta_key if meta_key in NODE_META else key
            existing = next((node for node in nodes if node["id"] == NODE_META[node_key][0]), None)
            if existing:
                existing["fields"].append(field)
            else:
                nodes.append(_node_for(node_key, group, [field]))
            seen.add(key)
    users = defs["cc_users_groups"]["properties"]["users"]
    items = _object_branch(users.get("items") or {}, root_schema)
    _collect_aliases(items, root_schema, aliases)
    props, _aliases = _canonical_props(items.get("properties") or {})
    have = {
        "name",
        "gecos",
        "homedir",
        "shell",
        "primary_group",
        "sudo",
        "groups",
        "lock_passwd",
        "system",
        "uid",
        "hashed_passwd",
        "ssh_authorized_keys",
    }
    for prop, prop_schema in props.items():
        if prop in have:
            continue
        field = convert_field(prop, prop_schema, root_schema, 1)
        if prop in {"passwd", "plain_text_passwd", "hashed_passwd"}:
            field["credential"] = True
            field["placeholder"] = "{{password_hash}}" if prop != "plain_text_passwd" else "{{password}}"
        patches["users"].append(field)
    write_items = _merge(defs["cc_write_files"]["properties"]["write_files"].get("items") or {}, root_schema)
    if "source" in (write_items.get("properties") or {}):
        patches["write_files"].append(convert_field("source", write_items["properties"]["source"], root_schema, 1))
    ntp = defs["cc_ntp"]["properties"]["ntp"]
    ntp_props = _merge(ntp, root_schema).get("properties") or {}
    if "config" in ntp_props:
        patches["ntp"].append(convert_field("config", ntp_props["config"], root_schema, 1))
    chpasswd = _merge(defs["cc_set_passwords"]["properties"]["chpasswd"], root_schema)
    user_items = _merge((chpasswd.get("properties") or {}).get("users", {}).get("items") or {}, root_schema)
    type_schema = (user_items.get("properties") or {}).get("type") or {}
    patches["chpasswd_type_choices"] = [""] + [
        str(item) for item in type_schema.get("enum") or ["hash", "text", "RANDOM"]
    ]
    return nodes, aliases, patches


def _append_field(nodes: list[dict], node_key: str, group: str, field: dict) -> None:
    node_id = NODE_META[node_key][0]
    existing = next((node for node in nodes if node["id"] == node_id), None)
    if existing:
        existing["fields"].append(field)
        return
    nodes.append(_node_for(node_key, group, [field]))


def _object_branch(schema: object, root: dict) -> dict:
    schema = _merge(schema, root)
    for key in ("oneOf", "anyOf"):
        if key in schema:
            for option in schema[key]:
                merged = _merge(option, root)
                if "properties" in merged or "object" in _types(merged):
                    return merged
    return schema


def _node_for(key: str, group: str, fields: list[dict]) -> dict:
    node_id, label, help_text = NODE_META[key]
    return {"id": node_id, "group": group, "label": label, "help": help_text, "fields": fields}


def _dump_snippet(value: object) -> str:
    text = yaml.safe_dump(value, default_flow_style=False, sort_keys=False).rstrip()
    if text.endswith("\n..."):
        text = text[: -len("\n...")]
    return text.strip()


def _walk_examples(value: object, prefix: str, out: dict[str, str]) -> None:
    snippet = _dump_snippet(value)
    if prefix and snippet:
        out.setdefault(prefix, snippet)
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _walk_examples(child, path, out)
    elif isinstance(value, list) and value and isinstance(value[0], (dict, list)):
        _walk_examples(value[0], prefix, out)


def build_examples(src: Path) -> dict:
    docs = src / "doc" / "module-docs"
    placeholders: dict[str, str] = {}
    for data_path in sorted(docs.glob("*/data.yaml")):
        meta = yaml.safe_load(data_path.read_text(encoding="utf-8")) or {}
        module = next(iter(meta.values()), {})
        examples = module.get("examples") if isinstance(module, dict) else None
        if not examples:
            continue
        example = examples[0]
        file_name = example.get("file") if isinstance(example, dict) else None
        if not file_name:
            continue
        example_path = docs / file_name
        if not example_path.is_file():
            continue
        loaded = yaml.safe_load(example_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            _walk_examples(loaded, "", placeholders)
    return {"version": DOCS_VERSION, "placeholders": placeholders}


def _py_literal(value: object) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False)
    text = text.replace(": true", ": True").replace(": false", ": False").replace(": null", ": None")
    return text.replace("[\n  true", "[\n  True").replace("[\n  false", "[\n  False")


def write_outputs(nodes: list[dict], aliases: dict[str, str], patches: dict, examples: dict) -> None:
    type_choices = patches.pop("chpasswd_type_choices", [])
    body = (
        '"""Generated from cloud-init '
        + DOCS_VERSION
        + '. Regenerate with scripts/extract_cloudinit_examples.py."""\n\n'
        "from __future__ import annotations\n\n"
        f"ALIASES: dict[str, str] = {_py_literal(aliases)}\n\n"
        f"CHPASSWD_TYPE_CHOICES: list[str] = {_py_literal(type_choices)}\n\n"
        f"PATCHES: dict = {_py_literal(patches)}\n\n"
        f"GENERATED_NODES: list[dict] = {_py_literal(nodes)}\n"
    )
    NODES_PATH.write_text(body, encoding="utf-8")
    EXAMPLES_PATH.write_text(json.dumps(examples, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schema_path = args.src / "cloudinit" / "config" / "schemas" / "schema-cloud-config-v1.json"
    root_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    nodes, aliases, patches = build_nodes(root_schema)
    examples = build_examples(args.src)
    if args.check:
        current_nodes = NODES_PATH.read_text(encoding="utf-8") if NODES_PATH.is_file() else ""
        current_examples = EXAMPLES_PATH.read_text(encoding="utf-8") if EXAMPLES_PATH.is_file() else ""
        write_outputs(nodes, aliases, patches, examples)
        ok = (
            NODES_PATH.read_text(encoding="utf-8") == current_nodes
            and EXAMPLES_PATH.read_text(encoding="utf-8") == current_examples
        )
        if not ok:
            print("module examples or generated nodes are stale", file=sys.stderr)
            return 1
        return 0
    write_outputs(nodes, aliases, patches, examples)
    print(f"wrote {len(nodes)} nodes, {len(examples['placeholders'])} placeholders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
