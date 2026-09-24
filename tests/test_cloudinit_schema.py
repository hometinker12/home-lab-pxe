"""Contract tests for the generated cloud-init editor schema (src/cloudinit/schema.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.cloudinit.generated_nodes import GENERATED_NODES
from src.cloudinit.schema import ADVANCED_KEYS, ALIASES, MODELED_KEYS, NODES, SCHEMA_KEYS, node_label_for_key

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "src" / "static" / "cloudinit-editor.js"
EXTRACTOR = ROOT / "scripts" / "extract_cloudinit_examples.py"

GROUP_ORDER = [
    "Identity",
    "Users and SSH",
    "Packages",
    "Files and commands",
    "Disks",
    "Network",
    "Power and phone home",
    "System",
    "Ubuntu",
    "Config management",
    "Other distros",
]


def _walk(fields: list[dict], prefix: str = ""):
    for field in fields:
        path = f"{prefix}.{field['key']}" if prefix else field["key"]
        yield path, field
        yield from _walk(field.get("object_fields") or [], path)
        yield from _walk(field.get("item_fields") or [], f"{path}[]")


def _all_fields():
    for node in NODES:
        yield from _walk(node["fields"])


def _frontend_field_types() -> set[str]:
    text = JS_PATH.read_text(encoding="utf-8")
    match = re.search(r"const\s+FIELD_TYPES\s*=\s*Object\.freeze\(\s*\[(.*?)\]\s*\)", text, flags=re.S)
    assert match, "cloudinit-editor.js must declare const FIELD_TYPES = Object.freeze([...])"
    return set(re.findall(r"""["']([a-z_]+)["']""", match.group(1)))


def test_field_types_match_frontend_contract():
    used = {field["type"] for _path, field in _all_fields()}
    declared = _frontend_field_types()
    assert used <= declared, f"types missing from FIELD_TYPES: {sorted(used - declared)}"


def test_field_shapes_are_complete():
    for path, field in _all_fields():
        assert field.get("key") and field.get("label"), path
        ftype = field["type"]
        if ftype == "enum":
            assert field.get("choices") and field["choices"][0] == "", path
        if ftype == "enum_list":
            assert field.get("choices") and "" not in field["choices"], path
        if ftype == "row_list":
            assert field.get("item_fields"), path
            assert field.get("emit", "list") in {"list", "mapping", "mapping_scalar", "tuple", "list_or_map", "groups"}
            assert field.get("item_label"), path
        if ftype == "object":
            assert field.get("object_fields"), path
            assert field.get("preserve_extra") is True, path
        if ftype == "map":
            assert field.get("value_type") in {"string", "scalar", "yaml"}, path
        if ftype == "package_list":
            assert field.get("managers") == ["apt", "snap"], path
        if "help" in field:
            assert len(field["help"]) <= 201 and "``" not in field["help"] and "**" not in field["help"], path


def test_modeled_top_level_keys_are_real_schema_keys():
    schema_keys = set(SCHEMA_KEYS)
    top = [field["key"] for node in NODES for field in node["fields"]]
    assert len(top) == len(set(top)), "each top-level key must have one owner node"
    assert set(top) <= schema_keys
    assert set(ALIASES) <= schema_keys and set(ALIASES.values()) <= set(top)
    assert set(ADVANCED_KEYS) <= schema_keys
    assert schema_keys == set(top) | set(ALIASES) | set(ADVANCED_KEYS)
    assert MODELED_KEYS == frozenset(top) | frozenset(ALIASES)
    assert "emit_keys_to_console" not in MODELED_KEYS
    assert "zypper_repos" not in MODELED_KEYS
    assert "lock-passwd" not in MODELED_KEYS


def test_installer_nodes_share_the_field_contract():
    from src.cloudinit.installer_schema import INSTALLER_GROUP, INSTALLER_NODES
    from src.seed_render import FORCE_REBOOT_CMD, QUIET_WIFI_CMD

    declared = _frontend_field_types()
    ids = [node["id"] for node in INSTALLER_NODES]
    assert len(ids) == len(set(ids)) and all(node_id.startswith("ai_") for node_id in ids)
    assert not set(ids) & {node["id"] for node in NODES}
    top = [field["key"] for node in INSTALLER_NODES for field in node["fields"]]
    assert len(top) == len(set(top)) and "user-data" not in top
    for key in ("version", "early-commands", "locale", "network", "apt", "storage", "identity", "ssh", "late-commands"):
        assert key in top
    for node in INSTALLER_NODES:
        assert node["group"] == INSTALLER_GROUP and node["doc_url"].startswith("https://")
        for path, field in _walk(node["fields"]):
            assert field["type"] in declared, path
            if field["type"] == "enum":
                assert field["choices"][0] == "", path
            if field["type"] == "object":
                assert field.get("object_fields") and field.get("preserve_extra") is True, path
            if field["type"] == "row_list":
                assert field.get("item_fields") and field.get("item_label"), path
            if "help" in field:
                assert len(field["help"]) <= 201, path
    commands = {field["key"]: field for node in INSTALLER_NODES for field in node["fields"] if field.get("locked")}
    assert set(commands) == {"early-commands", "late-commands", "error-commands"}
    # Managed-command markers must match the commands seed_render injects.
    early = {spec["label"]: spec for spec in commands["early-commands"]["managed_commands"]}
    assert any(needle in QUIET_WIFI_CMD for needle in early["Wi-Fi quieting"]["match"])
    late = {spec["label"]: spec for spec in commands["late-commands"]["managed_commands"]}
    assert any(needle in FORCE_REBOOT_CMD for needle in late["Forced reboot"]["match"])
    assert late["Phone-home"]["readded"] is False


def test_nodes_are_grouped_in_display_order():
    groups: list[str] = []
    for node in NODES:
        if not groups or groups[-1] != node["group"]:
            assert node["group"] not in groups, f"group {node['group']} is split"
            groups.append(node["group"])
    assert groups == GROUP_ORDER
    ids = [node["id"] for node in NODES]
    assert len(ids) == len(set(ids))
    assert "zypper_repos" not in ids
    network = [node["id"] for node in NODES if node["group"] == "Network"]
    assert network == ["hosts", "resolv_conf", "ntp", "wireguard", "fan", "install_hotplug"]
    system = [node["id"] for node in NODES if node["group"] == "System"]
    assert system == [
        "ca_certs",
        "rsyslog",
        "random_seed",
        "disable_ec2_metadata",
        "byobu",
        "grub_dpkg",
        "keys_to_console",
    ]
    assert node_label_for_key("hostname") == "Hostname"
    assert node_label_for_key("apt_update") == "Packages"


def test_nodes_carry_module_docs_and_example():
    for node in NODES:
        assert node["module"].startswith("cc_"), node["id"]
        anchor = node["module"].replace("_", "-")
        assert node["doc_url"] == f"https://docs.cloud-init.io/en/latest/reference/modules.html#mod-{anchor}"
        assert isinstance(node.get("example"), str) and node["example"].strip(), node["id"]
        assert "#cloud-config" not in node["example"]
    users = next(node for node in NODES if node["id"] == "users")
    assert users["doc_url"].endswith("#mod-cc-users-groups")
    assert json.dumps(NODES)  # the page embeds NODES as JSON
    assert len(NODES) == len(GENERATED_NODES)


def test_user_fields_are_common_first_and_typed():
    users = next(field for node in NODES for field in node["fields"] if field["key"] == "users")
    user = next(field for node in NODES for field in node["fields"] if field["key"] == "user")
    head = ["name", "gecos", "groups", "sudo", "shell", "lock_passwd", "ssh_authorized_keys"]
    assert [item["key"] for item in users["item_fields"][: len(head)]] == head
    assert [item["key"] for item in user["object_fields"][: len(head)]] == head
    assert user["type"] == "object"
    for fields in (users["item_fields"], user["object_fields"]):
        by_key = {item["key"]: item for item in fields}
        assert by_key["sudo"] == {**by_key["sudo"], "type": "string_or_list", "allow_false": True}
        assert by_key["groups"]["type"] == "string_or_list"
        assert by_key["uid"]["type"] == "int"
        assert by_key["passwd"]["credential"] is True
        assert "credential" not in by_key["hashed_passwd"]
        assert len(fields) == 25
    assert users["aliases"]["lock-passwd"] == "lock_passwd"


def test_blob_fields_became_structured():
    fields = dict(_all_fields())
    expected = {
        "keyboard.layout": "string",
        "groups": "row_list",
        "runcmd": "command_list",
        "bootcmd": "command_list",
        "snap.commands": "command_list",
        "ansible.galaxy.actions": "command_list",
        "packages": "package_list",
        "resolv_conf.options": "map",
        "zypper.config": "map",
        "rsyslog.remotes": "map",
        "write_files[].source.headers": "map",
        "puppet.conf.agent": "map",
        "puppet.csr_attributes.extension_requests": "map",
        "device_aliases": "map",
        "chef.initial_attributes": "yaml_value",
        "salt_minion.conf": "yaml_value",
        "power_state.condition": "yaml_value",
        "rsyslog.configs": "row_list",
        "rsyslog.service_reload_command": "string_or_list",
        "ansible.setup_controller.repositories": "row_list",
        "ansible.setup_controller.run_ansible": "row_list",
        "ansible.pull": "row_list",
        "apt.sources": "row_list",
        "apt.primary[].arches": "string_list",
        "apt.primary[].search": "string_list",
        "updates.network.when": "enum_list",
        "ssh_genkeytypes": "enum_list",
        "ntp.config.packages": "string_list",
        "fs_setup[].partition": "flex",
        "fs_setup[].cmd": "string_or_list",
        "vendor_data.prefix": "string_or_list",
        "locale": "flex",
        "phone_home.post": "all_or_list",
        "salt_minion.private_key": "text",
        "mcollective.conf.private-cert": "text",
        "apt.sources[].key": "text",
        "ssh.emit_keys_to_console": "bool",
        "disk_setup[].layout": "disk_layout",
    }
    for path, ftype in expected.items():
        assert fields[path]["type"] == ftype, path
    assert fields["salt_minion.private_key"]["secret"] is True
    assert "gpart" in fields["growpart.mode"]["choices"]
    assert fields["write_files[].encoding"]["choices"] == [
        "",
        "gz",
        "gzip",
        "gz+base64",
        "gzip+base64",
        "gz+b64",
        "gzip+b64",
        "b64",
        "base64",
        "text/plain",
    ]
    sources = {item["key"] for item in fields["apt.sources"]["item_fields"]}
    assert sources == {"name", "source", "keyid", "key", "keyserver", "filename", "append"}
    assert fields["ansible.pull[].playbook_name"]["deprecated"].startswith("Deprecated in")
    assert "list" not in {item["key"] for item in fields["chpasswd"]["object_fields"]}


def _load_extractor():
    spec = importlib.util.spec_from_file_location("extract_cloudinit_examples", EXTRACTOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extractor_reads_generated_module_semantically():
    extractor = _load_extractor()
    current = extractor.read_nodes_module(extractor.NODES_PATH)
    from src.cloudinit import generated_nodes

    assert current["GENERATED_NODES"] == generated_nodes.GENERATED_NODES
    assert current["ALIASES"] == generated_nodes.ALIASES
    rendered = extractor.render_nodes_module(current)
    reparsed = {}
    import ast

    for stmt in ast.parse(rendered).body:
        if isinstance(stmt, ast.AnnAssign):
            reparsed[stmt.target.id] = ast.literal_eval(stmt.value)
    assert reparsed == current


@pytest.mark.skipif(not os.environ.get("CLOUDINIT_SRC"), reason="set CLOUDINIT_SRC to a cloud-init 26.2 checkout")
def test_extractor_check_is_up_to_date_and_non_destructive():
    before = {path: path.read_bytes() for path in (ROOT / "src" / "cloudinit").glob("*") if path.is_file()}
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--src", os.environ["CLOUDINIT_SRC"], "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    after = {path: path.read_bytes() for path in (ROOT / "src" / "cloudinit").glob("*") if path.is_file()}
    assert before == after
    assert result.returncode == 0, result.stderr
