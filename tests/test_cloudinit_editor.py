from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from tests.conftest import login

from src.cloudinit.editor import (
    EditorError,
    apply_cloudinit_editor,
    cloud_config_preview,
    cloud_config_view,
    shield_tokens,
)
from src.inventory.service import create_image, deploy_machine, register_machine
from src.models import OsFamily
from src.seed_store import SeedError, factory_seed_text


def _factory_seed() -> str:
    return factory_seed_text(OsFamily.linux)


def _parsed_autoinstall(seed_text: str) -> dict:
    return yaml.safe_load(shield_tokens(seed_text))["autoinstall"]


def _user_data(seed_text: str) -> dict:
    auto = _parsed_autoinstall(seed_text)
    return auto.get("user-data") or {}


def test_clear_fqdn_keeps_hostname_and_autoinstall_siblings():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc.pop("fqdn", None)
    doc.setdefault("hostname", "{{hostname}}")
    result = apply_cloudinit_editor(seed, json.dumps(doc), view["extra_yaml"])
    before = _parsed_autoinstall(seed)
    after = _parsed_autoinstall(result)
    ud = _user_data(result)
    assert "fqdn" not in ud
    user_data_text = result.split("user-data:", 1)[1]
    assert "hostname: {{hostname}}" in user_data_text
    assert "fqdn:" not in user_data_text
    for key in ("early-commands", "storage", "identity"):
        assert before[key] == after[key]


def test_write_files_drops_blank_path_and_omits_empty_content():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["write_files"] = [
        {"path": "", "content": "ignored"},
        {"path": "/etc/motd", "content": ""},
    ]
    result = apply_cloudinit_editor(seed, json.dumps(doc), "")
    ud = _user_data(result)
    assert ud["write_files"] == [{"path": "/etc/motd"}]


def test_package_update_false_and_null_upgrade():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["package_update"] = False
    doc["package_upgrade"] = None
    result = apply_cloudinit_editor(seed, json.dumps(doc), "")
    ud = _user_data(result)
    assert ud["package_update"] is False
    assert "package_upgrade" not in ud


def test_empty_runcmd_omitted():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["runcmd"] = []
    result = apply_cloudinit_editor(seed, json.dumps(doc), "")
    ud = _user_data(result)
    assert "runcmd" not in ud


def test_packages_block_token_dumps_as_child_line():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["packages"] = {"__pxe_block__": "packages"}
    result = apply_cloudinit_editor(seed, json.dumps(doc), "")
    in_user_data = False
    child_line = ""
    for line in result.splitlines():
        if line.strip() == "user-data:":
            in_user_data = True
            continue
        if in_user_data and line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            if line.strip() != "packages:":
                in_user_data = False
        if in_user_data and line.strip() == "packages:":
            continue
        if in_user_data and line.lstrip().startswith("{{packages}}"):
            child_line = line.strip()
            break
    assert child_line == "{{packages}}"
    assert 'packages: "{{packages}}"' not in result.split("user-data:", 1)[-1]


def test_advanced_yaml_custom_key_and_hostname_conflict():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    result = apply_cloudinit_editor(seed, json.dumps(doc), "custom_key: 1\n")
    ud = _user_data(result)
    assert ud["custom_key"] == 1
    with pytest.raises(SeedError, match="Hostname"):
        apply_cloudinit_editor(seed, json.dumps(doc), "hostname: x\n")


def test_plain_cloud_config_no_autoinstall():
    seed = "#cloud-config\nhostname: plain\npackage_update: true\n"
    view = cloud_config_view(seed)
    assert view["mode"] == "cloud-config"
    doc = view["doc"]
    doc["package_update"] = False
    result = apply_cloudinit_editor(seed, json.dumps(doc), "")
    parsed = yaml.safe_load(result)
    assert "autoinstall" not in parsed
    assert parsed["hostname"] == "plain"
    assert parsed["package_update"] is False


def test_literal_password_rejected_by_validate_seed_template():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["chpasswd"] = {"users": [{"name": "root", "password": "hunter2", "type": "text"}]}
    with pytest.raises(SeedError, match="Credential fields must use placeholders"):
        apply_cloudinit_editor(seed, json.dumps(doc), "")


def test_cloud_config_view_factory_seed():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    assert view["mode"] == "autoinstall"
    doc = view["doc"]
    assert doc["hostname"] == "{{hostname}}"
    packages = doc.get("packages")
    assert packages in (None, {"__pxe_block__": "packages"})
    assert doc["users"][0]["name"] == "{{username}}"
    assert doc["users"][0]["ssh_authorized_keys"] == {"__pxe_block__": "ssh_keys"}
    assert "early-commands" not in view["extra_yaml"]


def test_factory_view_round_trip_keeps_installer_block():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), view["extra_yaml"])
    before = yaml.safe_load(shield_tokens(seed))
    after = yaml.safe_load(shield_tokens(out))
    for key in ("early-commands", "late-commands", "storage", "identity", "source"):
        assert before["autoinstall"][key] == after["autoinstall"][key]
    assert before["autoinstall"]["user-data"] == after["autoinstall"]["user-data"]
    assert view["doc"]["ssh_pwauth"] is True


def test_manage_etc_hosts_round_trip():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    assert doc["manage_etc_hosts"] == "true"
    result = apply_cloudinit_editor(seed, json.dumps(doc), "")
    ud = _user_data(result)
    assert ud["manage_etc_hosts"] is True


def test_cc_json_size_limit():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    huge = json.dumps({**view["doc"], "padding": "x" * (256 * 1024)})
    with pytest.raises(SeedError, match="256 KiB"):
        apply_cloudinit_editor(seed, huge, "")


def test_machine_detail_includes_cc_editor(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="cc-linux",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:71", hostname="cc-node", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = int(machine.id)
    page = client.get(f"/machines/{mid}")
    assert page.status_code == 200
    assert 'name="user_data"' in page.text
    assert ">Editor</button>" in page.text
    assert "data-cc-editor" in page.text
    assert 'name="cc_json"' not in page.text


def test_image_detail_linux_includes_cc_editor(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="cc-image",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        db.commit()
        image_id = int(image.id)
    page = client.get(f"/images/{image_id}")
    assert page.status_code == 200
    assert 'name="user_data"' in page.text
    assert ">Editor</button>" in page.text
    assert "data-cc-editor" in page.text


def test_image_detail_windows_excludes_cc_editor(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="cc-win",
            os_family=OsFamily.windows,
            boot_wim_path="win/boot.wim",
            install_wim_path="win/install.wim",
            actor="admin",
        )
        db.commit()
        image_id = int(image.id)
    page = client.get(f"/images/{image_id}")
    assert page.status_code == 200
    assert "data-cc-editor" not in page.text
    assert ">Editor</button>" not in page.text


def test_editor_preview_view_and_apply(client):
    login(client)
    seed = _factory_seed()
    view = client.post("/api/cloud-init/editor", json={"action": "view", "seed": seed})
    assert view.status_code == 200
    body = view.json()
    assert body["mode"] == "autoinstall"
    assert body["doc"]["hostname"] == "{{hostname}}"
    applied = client.post(
        "/api/cloud-init/editor",
        json={"action": "apply", "seed": seed, "cc_json": json.dumps(body["doc"]), "cc_extra_yaml": body["extra_yaml"]},
    )
    assert applied.status_code == 200
    before = _parsed_autoinstall(seed)
    after = _parsed_autoinstall(applied.json()["seed"])
    assert before["early-commands"] == after["early-commands"]
    assert before["user-data"] == after["user-data"]


def test_disk_setup_example_round_trip():
    seed = """#cloud-config
device_aliases:
  my_alias: /dev/sdb
disk_setup:
  /dev/sdd:
    layout: true
    overwrite: true
    table_type: mbr
  my_alias:
    layout: [50, 50]
    overwrite: true
    table_type: gpt
  swap_disk:
    layout:
      - [100, 82]
    overwrite: true
    table_type: gpt
fs_setup:
  - label: fs1
    filesystem: ext4
    device: my_alias.1
mounts:
  - [my_alias.1, /mnt1]
  - [swap_disk.1, none, swap, sw, "0", "0"]
"""
    view = cloud_config_view(seed)
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), "")
    parsed = yaml.safe_load(shield_tokens(out))
    assert parsed["device_aliases"]["my_alias"] == "/dev/sdb"
    assert parsed["disk_setup"]["/dev/sdd"]["layout"] is True
    assert parsed["disk_setup"]["my_alias"]["layout"] == [50, 50]
    assert parsed["disk_setup"]["swap_disk"]["layout"] == [[100, 82]]
    assert parsed["fs_setup"][0]["device"] == "my_alias.1"
    assert parsed["mounts"][0] == ["my_alias.1", "/mnt1"]
    assert parsed["mounts"][1][0] == "swap_disk.1"


def test_fan_apt_yum_ansible_round_trip():
    seed = """#cloud-config
fan:
  config: "10.0.0.0/8 eth0/16 dhcp\\n"
  config_path: /etc/network/fan
apt:
  primary:
    - uri: http://archive.ubuntu.com/ubuntu
      arches: [default]
yum_repos:
  epel:
    baseurl: http://example.test/epel
    name: EPEL
    enabled: true
ansible:
  install_method: pip
  pull:
    url: https://example.test/playbooks.git
"""
    view = cloud_config_view(seed)
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), "")
    parsed = yaml.safe_load(out)
    assert "dhcp" in parsed["fan"]["config"]
    assert parsed["apt"]["primary"][0]["uri"] == "http://archive.ubuntu.com/ubuntu"
    assert parsed["yum_repos"]["epel"]["baseurl"] == "http://example.test/epel"
    assert parsed["ansible"]["pull"]["url"] == "https://example.test/playbooks.git"


def test_hyphen_alias_and_extra_disk_key():
    seed = """#cloud-config
ca_certs:
  remove-defaults: true
  trusted: []
disk_setup:
  /dev/sdb:
    table_type: gpt
    custom_flag: keep-me
"""
    view = cloud_config_view(seed)
    ca = view["doc"]["ca_certs"]
    assert ca["remove_defaults"] is True
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), "")
    parsed = yaml.safe_load(out)
    assert parsed["ca_certs"]["remove_defaults"] is True
    assert "remove-defaults" not in parsed["ca_certs"]
    assert parsed["disk_setup"]["/dev/sdb"]["custom_flag"] == "keep-me"


def test_users_scalar_and_block_token_round_trip():
    seed = """#cloud-config
users:
  - default
  - name: {{username}}
    ssh_authorized_keys: {{ssh_keys}}
"""
    view = cloud_config_view(seed)
    assert view["doc"]["users"][0] == "default"
    assert view["doc"]["users"][1]["ssh_authorized_keys"] == {"__pxe_block__": "ssh_keys"}
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), "")
    assert "\n  - default\n" in out or "\n- default\n" in out
    user_data = out.split("users:", 1)[1]
    assert "{{ssh_keys}}" in user_data
    assert "{{ssh_keys}}" not in user_data.split("ssh_authorized_keys:", 1)[1].split("\n", 1)[0]


def test_chpasswd_yaml_list_and_random():
    seed = "#cloud-config\nhostname: box\n"
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["chpasswd"] = {"users": "- name: root\n  password: {{password}}\n  type: hash\n"}
    out = apply_cloudinit_editor(seed, json.dumps(doc), "")
    parsed = yaml.safe_load(shield_tokens(out))
    assert parsed["chpasswd"]["users"][0]["name"] == "root"
    doc["chpasswd"] = {"users": [{"name": "root", "type": "RANDOM"}]}
    out = apply_cloudinit_editor(seed, json.dumps(doc), "")
    parsed = yaml.safe_load(out)
    assert parsed["chpasswd"]["users"][0]["type"] == "RANDOM"
    assert "password" not in parsed["chpasswd"]["users"][0]
    doc["chpasswd"] = {"users": [{"type": "hash", "password": "{{password}}"}]}
    with pytest.raises(SeedError, match="name is required"):
        apply_cloudinit_editor(seed, json.dumps(doc), "")
    doc["chpasswd"] = {"users": [{"name": "root", "password": "hunter2", "type": "text"}]}
    with pytest.raises(SeedError, match="Credential fields must use placeholders"):
        apply_cloudinit_editor(seed, json.dumps(doc), "")


def test_ubuntu_pro_zypper_and_grub_key():
    seed = """#cloud-config
ubuntu_advantage:
  token: secret-token
zypper:
  repos:
    - id: oss
      baseurl: http://example.test/oss
grub_dpkg:
  grub-pc/install_devices: /dev/sda
"""
    view = cloud_config_view(seed)
    assert "ubuntu_pro" in view["doc"]
    assert "ubuntu_advantage" not in view["doc"]
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), "")
    parsed = yaml.safe_load(out)
    assert "ubuntu_pro" in parsed
    assert "ubuntu_advantage" not in parsed
    assert parsed["zypper"]["repos"][0]["id"] == "oss"
    assert parsed["grub_dpkg"]["grub-pc/install_devices"] == "/dev/sda"


def test_placeholders_are_not_emitted_when_empty():
    from src.cloudinit.schema import NODES

    examples = json.loads(
        Path(__file__).parents[1].joinpath("src/cloudinit/module_examples.json").read_text(encoding="utf-8")
    )
    assert examples["version"] == "26.2"
    assert "disk_setup" in examples["placeholders"]
    assert "fan.config" in examples["placeholders"]
    disk = next(node for node in NODES if node["id"] == "disk_setup")
    fan = next(node for node in NODES if node["id"] == "fan")
    assert all(field["type"] != "yaml" for field in disk["fields"])
    assert all(field["type"] != "yaml" for field in fan["fields"])
    seed = "#cloud-config\nhostname: box\n"
    view = cloud_config_view(seed)
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), "")
    assert "disk_setup" not in out
    assert "fan:" not in out


def test_editor_preview_requires_login(client):
    response = client.post("/api/cloud-init/editor", json={"action": "view", "seed": ""})
    assert response.status_code == 401


# --------------------------------------------------------------------------- overhaul regressions

_CREDENTIAL_POLICY_KEYS = {"password", "rh_subscription"}
_CREDENTIAL_POLICY_EXAMPLES = {
    ("cc_rh_subscription", 0),
    ("cc_set_passwords", 0),
    ("cc_set_passwords", 1),
}


def _examples_payload() -> dict:
    path = Path(__file__).parents[1] / "src" / "cloudinit" / "module_examples.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _no_op_apply(seed: str) -> tuple[dict, str]:
    view = cloud_config_view(seed)
    return view, apply_cloudinit_editor(seed, json.dumps(view["doc"]), view["extra_yaml"])


def _loaded(text: str):
    return yaml.safe_load(shield_tokens(text))


def _snap_commands_as_list(doc: dict) -> dict:
    snap = doc.get("snap")
    if isinstance(snap, dict) and isinstance(snap.get("commands"), dict):
        commands = snap["commands"]
        snap = {**snap, "commands": [commands[key] for key in sorted(commands)]}
        return {**doc, "snap": snap}
    return doc


def _same_mapping(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return sorted(left, key=str) == sorted(right, key=str) and all(_same_mapping(left[k], right[k]) for k in left)
    return left == right


def test_every_top_level_doc_example_round_trips():
    placeholders = _examples_payload()["placeholders"]
    top = [key for key in placeholders if "." not in key]
    assert len(top) == 71
    checked = 0
    for key in top:
        if key in _CREDENTIAL_POLICY_KEYS:
            continue
        seed = "#cloud-config\n" + yaml.safe_dump({key: yaml.safe_load(placeholders[key])}, sort_keys=False)
        _view, out = _no_op_apply(seed)
        assert _same_mapping(_loaded(out), _snap_commands_as_list(_loaded(seed))), key
        checked += 1
    assert checked == 69


def test_every_module_doc_example_round_trips():
    examples = _examples_payload()["examples"]
    checked = 0
    for module, texts in examples.items():
        for index, text in enumerate(texts):
            if (module, index) in _CREDENTIAL_POLICY_EXAMPLES:
                continue
            seed = "#cloud-config\n" + text
            _view, out = _no_op_apply(seed)
            expected = _snap_commands_as_list(_loaded(seed) or {})
            assert _same_mapping(_loaded(out) or {}, expected), f"{module} example {index + 1}"
            checked += 1
    assert checked == 125


def test_credential_policy_examples_still_rejected():
    for seed in (
        "#cloud-config\npassword: password1\n",
        "#cloud-config\nrh_subscription:\n  username: joe\n  password: '1234abcd'\n",
    ):
        view = cloud_config_view(seed)
        with pytest.raises(SeedError, match="Credential fields must use placeholders"):
            apply_cloudinit_editor(seed, json.dumps(view["doc"]), view["extra_yaml"])
    seed = "#cloud-config\nchpasswd:\n  users:\n    - name: a\n      password: {{password_hash}}\n      type: hash\n"
    view = cloud_config_view(seed)
    out = apply_cloudinit_editor(seed, json.dumps(view["doc"]), view["extra_yaml"])
    assert _loaded(out) == _loaded(seed)


_P0_SEED = """#cloud-config
user: {name: bob, gecos: Bob, sudo: null, uid: 1500}
apt_pipelining: os
keyboard: {layout: us}
users:
  - default
  - name: alice
    sudo: ['ALL=(ALL) NOPASSWD:ALL', 'alice ALL=(ALL) /bin/ls']
    uid: 1001
    groups: [adm, sudo]
  - name: carol
    sudo: false
    groups: wheel
runcmd:
  - [ls, -l, /]
  - echo hi
bootcmd:
  - [cloud-init-per, once, mymkfs, mkfs, /dev/vdb]
snap:
  commands:
    - [snap, install, vlc]
packages:
  - pwgen
  - [libpython3.8, 3.8.10-0ubuntu1]
  - snap: [certbot, [juju, --edge]]
  - apt: [mg]
rsyslog:
  configs:
    - '*.* @@192.0.2.1'
    - {filename: 01-example.conf, content: '*.* @@192.0.2.2:10514'}
resolv_conf:
  options: {rotate: true, timeout: 1}
chef:
  initial_attributes:
    apache: {prefork: {maxclients: 100}, keepalive: false}
salt_minion:
  conf: {master: salt.example.com, fileserver_backend: [gitfs]}
  grains: {role: [web]}
write_files:
  - {path: /a, encoding: gzip+base64, content: H4sI}
  - {path: /b, encoding: text/plain, content: hello}
  - {path: /c, permissions: 0644}
growpart: {mode: gpart}
ssh_pwauth: unchanged
locale: false
power_state: {mode: reboot, condition: true, delay: now}
chpasswd:
  expire: false
  list: |
    root:{{password}}
vendor_data: {prefix: /usr/bin/ltrace}
mounts:
  - [sdb, null]
  - [my_alias.1, /mnt1]
fs_setup:
  - {device: /dev/sdb, filesystem: ext4, cmd: mkfs -t %(filesystem)s %(device)s, partition: auto}
rpi:
  interfaces: {serial: '1'}
"""


def test_p0_no_op_apply_keeps_every_value():
    view, out = _no_op_apply(_P0_SEED)
    before = _loaded(_P0_SEED)
    after = _loaded(out)
    assert after == before
    doc = view["doc"]
    # top-level flex / object / enum values are now in the form, not dropped
    assert doc["apt_pipelining"] == "os"
    assert doc["keyboard"] == {"layout": "us"}
    assert doc["user"]["name"] == "bob" and doc["user"]["uid"] == 1500
    assert "sudo: null" in doc["user"]["__extra__"]
    assert doc["locale"] == "false"
    assert doc["growpart"] == {"mode": "gpart"}
    assert doc["power_state"]["condition"] == "true"
    # users: scalar rows, sudo list/false, uid int, groups string or list
    assert doc["users"][0] == "default"
    assert doc["users"][1]["sudo"] == ["ALL=(ALL) NOPASSWD:ALL", "alice ALL=(ALL) /bin/ls"]
    assert doc["users"][1]["uid"] == 1001
    assert doc["users"][2]["sudo"] is False
    assert doc["users"][2]["groups"] == "wheel"
    # commands keep argv lists
    assert doc["runcmd"] == [["ls", "-l", "/"], "echo hi"]
    assert doc["snap"]["commands"] == [["snap", "install", "vlc"]]
    # packages, rsyslog configs, maps, yaml_value dicts
    assert doc["packages"][1] == {"name": "libpython3.8", "version": "3.8.10-0ubuntu1", "manager": ""}
    assert doc["packages"][3] == {"name": "juju", "version": "--edge", "manager": "snap"}
    assert doc["rsyslog"]["configs"][0] == "*.* @@192.0.2.1"
    assert doc["resolv_conf"]["options"] == [{"key": "rotate", "value": "true"}, {"key": "timeout", "value": "1"}]
    assert "maxclients: 100" in doc["chef"]["initial_attributes"]
    # write_files: unknown/new encodings kept, int permissions shown as octal
    assert doc["write_files"][0]["encoding"] == "gzip+base64"
    assert doc["write_files"][1]["encoding"] == "text/plain"
    assert doc["write_files"][2]["permissions"] == "0o644"
    assert after["write_files"][2]["permissions"] == 0o644
    assert "permissions: 0644" in out
    # string-or-list, preserved nulls, deprecated keys kept in Other keys
    assert doc["vendor_data"]["prefix"] == "/usr/bin/ltrace"
    assert doc["fs_setup"][0]["cmd"] == "mkfs -t %(filesystem)s %(device)s"
    assert doc["mounts"][0] == {"fs_spec": "sdb", "fs_file": None}
    assert after["mounts"][0] == ["sdb", None]
    assert "list:" in doc["chpasswd"]["__extra__"]
    assert doc["rpi"]["interfaces"]["serial"] == "'1'"
    assert "ssh_pwauth: unchanged" in view["extra_yaml"]
    assert "...\n" not in json.dumps(doc)


def test_p0_normalized_forms_are_equivalent():
    cases = {
        "#cloud-config\nuser: bob\n": {"user": {"name": "bob"}},
        "#cloud-config\nusers: default\n": {"users": ["default"]},
        "#cloud-config\ngroups: {admins: [a, b]}\n": {"groups": [{"admins": ["a", "b"]}]},
        "#cloud-config\ngroups: 'a, b'\n": {"groups": ["a", "b"]},
        "#cloud-config\ngroups:\n  - admingroup: [root, sys]\n  - cloud-users\n": {
            "groups": [{"admingroup": ["root", "sys"]}, "cloud-users"]
        },
        "#cloud-config\nusers:\n  - {name: a, uid: '1002'}\n": {"users": [{"name": "a", "uid": 1002}]},
        "#cloud-config\nsnap:\n  commands:\n    1: b\n    0: [a, x]\n": {"snap": {"commands": [["a", "x"], "b"]}},
    }
    for seed, expected in cases.items():
        view, out = _no_op_apply(seed)
        assert _loaded(out) == expected, seed
        assert bool(view["notices"]) == (expected != _loaded(seed)), seed


def test_autoinstall_without_user_data_stays_without_it():
    seed = "#cloud-config\nautoinstall:\n  version: 1\n  locale: en_US.UTF-8\n"
    view, out = _no_op_apply(seed)
    assert view["mode"] == "autoinstall"
    assert view["installer_keys"] == ["version", "locale"]
    assert "user-data" not in _loaded(out)["autoinstall"]
    doc = {"hostname": "{{hostname}}"}
    out = apply_cloudinit_editor(seed, json.dumps(doc), "")
    assert _loaded(out)["autoinstall"]["user-data"] == {"hostname": "__PXE_hostname__"}


def test_keyboard_layout_is_plain_string_and_disk_layout_only_under_disk_setup():
    view, out = _no_op_apply("#cloud-config\nkeyboard: {layout: de, variant: nodeadkeys}\n")
    assert view["doc"]["keyboard"] == {"layout": "de", "variant": "nodeadkeys"}
    doc = {"keyboard": {"model": "pc105"}}
    assert _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")) is None
    doc = {"disk_setup": [{"name": "/dev/sdb", "layout": {"mode": "custom", "lines": ["50", "50, 82"]}}]}
    out = apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")
    assert _loaded(out)["disk_setup"]["/dev/sdb"]["layout"] == [50, [50, 82]]


def test_ssh_emit_keys_to_console_lives_under_ssh():
    seed = "#cloud-config\nssh:\n  emit_keys_to_console: false\n"
    view, out = _no_op_apply(seed)
    assert view["doc"]["ssh"] == {"emit_keys_to_console": False}
    assert _loaded(out) == {"ssh": {"emit_keys_to_console": False}}
    out = apply_cloudinit_editor("#cloud-config\n", json.dumps({"ssh": {"emit_keys_to_console": True}}), "")
    assert _loaded(out) == {"ssh": {"emit_keys_to_console": True}}
    view = cloud_config_view("#cloud-config\nemit_keys_to_console: false\n")
    assert "emit_keys_to_console" not in view["doc"]
    assert "emit_keys_to_console: false" in view["extra_yaml"]


def test_enum_unknown_values_are_preserved():
    seed = "#cloud-config\nwrite_files:\n  - {path: /x, encoding: future-enc}\nresize_rootfs: grow-later\n"
    view, out = _no_op_apply(seed)
    assert view["doc"]["write_files"][0]["encoding"] == "future-enc"
    assert view["doc"]["resize_rootfs"] == "grow-later"
    assert _loaded(out) == _loaded(seed)
    out = apply_cloudinit_editor("#cloud-config\n", json.dumps({"resize_rootfs": "false"}), "")
    assert _loaded(out) == {"resize_rootfs": False}


def test_alias_notices_and_path_scoped_aliases():
    seed = "#cloud-config\napt_update: true\nlock-passwd: true\nusers:\n  - {name: a, lock-passwd: false}\n"
    view, out = _no_op_apply(seed)
    assert view["doc"]["package_update"] is True
    assert view["doc"]["users"][0]["lock_passwd"] is False
    assert "lock-passwd: true" in view["extra_yaml"]
    assert any("apt_update" in note and "package_update" in note for note in view["notices"])
    assert any("users[0].lock-passwd" in note for note in view["notices"])
    parsed = _loaded(out)
    assert parsed["package_update"] is True and "apt_update" not in parsed
    assert parsed["users"] == [{"name": "a", "lock_passwd": False}]
    assert parsed["lock-passwd"] is True
    view = cloud_config_view("#cloud-config\napt_update: true\npackage_update: false\n")
    assert view["doc"]["package_update"] is False
    assert "apt_update: true" in view["extra_yaml"]


def test_modeled_key_allowed_in_advanced_yaml_when_form_does_not_set_it():
    out = apply_cloudinit_editor("#cloud-config\n", json.dumps({"hostname": "a"}), "keyboard:\n  layout: us\n")
    assert _loaded(out) == {"hostname": "a", "keyboard": {"layout": "us"}}
    with pytest.raises(SeedError, match="Move 'hostname' to the Hostname section") as info:
        apply_cloudinit_editor("#cloud-config\n", json.dumps({"hostname": "a"}), "hostname: b\n")
    assert info.value.node == "hostname" and info.value.path == "__advanced__"
    doc = {"ntp": {"enabled": True, "__extra__": "enabled: false\n"}}
    with pytest.raises(SeedError, match="set in the form and in Other keys") as info:
        apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")
    assert info.value.node == "ntp" and info.value.path == "ntp.enabled"


def test_map_type_scalar_typing_and_duplicates():
    doc = {
        "resolv_conf": {
            "options": [
                {"key": "rotate", "value": "true"},
                {"key": "timeout", "value": "1"},
                {"key": "note", "value": "a b"},
                {"key": "", "value": "dropped"},
                {"key": "empty", "value": ""},
            ]
        },
        "rsyslog": {"remotes": [{"key": "juju", "value": "10.0.4.1"}, {"key": "port", "value": "1"}]},
    }
    out = _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), ""))
    assert out["resolv_conf"]["options"] == {"rotate": True, "timeout": 1, "note": "a b"}
    assert out["rsyslog"]["remotes"] == {"juju": "10.0.4.1", "port": "1"}
    doc = {"resolv_conf": {"options": [{"key": "x", "value": "1"}, {"key": "x", "value": "2"}]}}
    with pytest.raises(SeedError, match="Duplicate key 'x'") as info:
        apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")
    assert info.value.node == "resolv_conf" and info.value.path == "resolv_conf.options[1]"
    doc = {"resolv_conf": {"options": [{"key": "x", "value": "[1, 2]"}]}}
    with pytest.raises(SeedError, match="single value"):
        apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")


def test_string_or_list_type():
    doc = {
        "users": [
            {"name": "a", "sudo": "ALL=(ALL) ALL", "groups": ["adm", "", "sudo"]},
            {"name": "b", "sudo": False, "groups": []},
            {"name": "c", "sudo": ["", ""]},
        ],
        "fs_setup": [{"device": "/dev/sdb", "cmd": False}],
    }
    out = _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), ""))
    assert out["users"] == [
        {"name": "a", "sudo": "ALL=(ALL) ALL", "groups": ["adm", "sudo"]},
        {"name": "b", "sudo": False},
        {"name": "c"},
    ]
    assert out["fs_setup"] == [{"device": "/dev/sdb"}]


def test_command_list_type():
    doc = {"runcmd": ["echo hi", ["ls", "-l", "/"], "multi\nline", "", None, ["", ""], "{{hostname}}"]}
    out = apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")
    assert _loaded(out)["runcmd"] == ["echo hi", ["ls", "-l", "/"], "multi\nline", "__PXE_hostname__"]
    assert "- {{hostname}}" in out
    assert _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps({"runcmd": ["", None]}), "")) is None


def test_enum_list_type():
    doc = {"updates": {"network": {"when": ["hotplug", "boot", "hotplug", "future", ""]}}, "ssh_genkeytypes": []}
    out = _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), ""))
    assert out == {"updates": {"network": {"when": ["hotplug", "boot", "future"]}}}


def test_package_list_type():
    rows = [
        {"name": "a", "version": "", "manager": ""},
        {"name": "b", "version": "1.0", "manager": ""},
        {"name": "c", "manager": "snap"},
        {"name": "d", "version": "--edge", "manager": "snap"},
        {"name": "e", "manager": "apt"},
        {"name": "", "manager": "apt"},
    ]
    out = _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps({"packages": rows}), ""))
    assert out["packages"] == ["a", ["b", "1.0"], {"snap": ["c", ["d", "--edge"]]}, {"apt": ["e"]}]
    with pytest.raises(SeedError, match="Unknown package manager") as info:
        apply_cloudinit_editor("#cloud-config\n", json.dumps({"packages": [{"name": "x", "manager": "yum"}]}), "")
    assert info.value.path == "packages[0].manager"
    view = cloud_config_view("#cloud-config\npackages:\n  {{packages}}\n")
    assert view["doc"]["packages"] == {"__pxe_block__": "packages"}


def test_groups_row_list_emit():
    rows = [{"name": "admins", "members": ["a", "b"]}, {"name": "cloud-users"}, {"name": ""}, "ops"]
    out = _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps({"groups": rows}), ""))
    assert out["groups"] == [{"admins": ["a", "b"]}, "cloud-users", "ops"]


def test_int_and_bool_state_accept_legacy_strings():
    doc = {"phone_home": {"url": "http://x/", "tries": "5"}, "package_update": "true"}
    out = _loaded(apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), ""))
    assert out == {"phone_home": {"url": "http://x/", "tries": 5}, "package_update": True}
    with pytest.raises(SeedError, match="Expected an integer") as info:
        apply_cloudinit_editor("#cloud-config\n", json.dumps({"phone_home": {"url": "u", "tries": "x"}}), "")
    assert info.value.node == "phone_home" and info.value.path == "phone_home.tries"


def test_jinja_header_is_kept():
    seed = "## template: jinja\n#cloud-config\nhostname: x\n"
    _view, out = _no_op_apply(seed)
    assert out.startswith("## template: jinja\n#cloud-config\n")


def test_editor_api_preview_no_store_and_quiet_logs(client, caplog):
    login(client)
    seed = _factory_seed()
    view = client.post("/api/cloud-init/editor", json={"action": "view", "seed": seed})
    assert view.status_code == 200
    assert view.headers["cache-control"] == "no-store"
    body = view.json()
    assert body["installer_keys"][:2] == ["version", "locale"]
    assert isinstance(body["notices"], list)
    doc = body["doc"]
    doc["runcmd"] = ["echo preview-marker-xyz"]
    caplog.clear()
    with caplog.at_level("DEBUG"):
        preview = client.post(
            "/api/cloud-init/editor",
            json={"action": "preview", "seed": seed, "cc_json": json.dumps(doc), "cc_extra_yaml": body["extra_yaml"]},
        )
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    result = preview.json()
    assert set(result) == {"seed", "user_data", "node_yaml"}
    assert result["user_data"].startswith("#cloud-config\n")
    assert "early-commands" in result["seed"] and "early-commands" not in result["user_data"]
    assert result["node_yaml"]["runcmd"] == "runcmd:\n  - echo preview-marker-xyz"
    assert result["node_yaml"]["hostname"].startswith("hostname: {{hostname}}")
    assert "{{password_hash}}" in result["node_yaml"]["passwords"]
    for record in caplog.records:
        message = record.getMessage()
        assert "preview-marker-xyz" not in message
        assert "password_hash" not in message
        assert "early-commands" not in message


def test_editor_api_structured_errors(client):
    login(client)
    seed = _factory_seed()
    doc = cloud_config_view(seed)["doc"]
    doc["chpasswd"] = {"users": [{"name": "root", "password": "hunter2", "type": "text"}]}
    for action in ("apply", "preview"):
        response = client.post(
            "/api/cloud-init/editor", json={"action": action, "seed": seed, "cc_json": json.dumps(doc)}
        )
        assert response.status_code == 400
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["detail"].startswith("Credential fields must use placeholders")
        assert body["node"] == "passwords"
        assert body["path"] == "chpasswd.users[0].password"
        assert "hunter2" not in response.text
    unknown = client.post("/api/cloud-init/editor", json={"action": "nope", "seed": seed})
    assert unknown.status_code == 400 and unknown.json() == {"detail": "Unknown action"}
    bad = client.post("/api/cloud-init/editor", json={"action": "view", "seed": "a: [unclosed"})
    assert bad.status_code == 400 and bad.json() == {"detail": "user-data is not valid YAML"}


def test_editor_preview_action_requires_login(client):
    response = client.post(
        "/api/cloud-init/editor", json={"action": "preview", "seed": "#cloud-config\n", "cc_json": "{}"}
    )
    assert response.status_code == 401


def test_detail_pages_render_with_invalid_seed(client):
    login(client)
    from src.db import session_scope
    from src.seed_store import write_image_seed, write_machine_seed

    with session_scope() as db:
        image = create_image(
            db,
            name="cc-bad",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:72", hostname="cc-bad", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    write_machine_seed(mid, OsFamily.linux, "#cloud-config\nusers: [unclosed\n")
    write_image_seed(image_id, OsFamily.linux, "- just\n- a list\n")
    page = client.get(f"/machines/{mid}")
    assert page.status_code == 200
    assert "data-cc-editor" in page.text
    page = client.get(f"/images/{image_id}")
    assert page.status_code == 200
    assert "data-cc-editor" in page.text


# --------------------------------------------------------------------------- credential policy: YAML side doors

_LITERAL = "SuperSecret123"
_USERS_SEED = "#cloud-config\nusers:\n  - name: alice\n"


def _both_actions(seed: str, doc: dict, extra: str = ""):
    """Run apply and preview; both must fail the same way without echoing the literal."""
    errors = []
    for action in (apply_cloudinit_editor, cloud_config_preview):
        with pytest.raises(EditorError, match="Credential fields must use placeholders") as info:
            action(seed, json.dumps(doc), extra)
        assert _LITERAL not in str(info.value)
        errors.append(info.value)
    assert (errors[0].node, errors[0].path) == (errors[1].node, errors[1].path)
    return errors[0]


@pytest.mark.parametrize(
    "extra",
    [
        f"plain_text_passwd: {_LITERAL}\n",
        f"plain-text-passwd: {_LITERAL}\n",
        f"passwd: {_LITERAL}\n",
        f"hashed_passwd: {_LITERAL}\n",
        f"hashed-passwd: {_LITERAL}\n",
        f"password: {_LITERAL}\n",
        f"nested:\n  password: {_LITERAL}\n",
    ],
)
def test_row_extra_credential_literal_rejected(extra):
    doc = cloud_config_view(_USERS_SEED)["doc"]
    doc["users"][0]["__extra__"] = extra
    error = _both_actions(_USERS_SEED, doc)
    assert error.node == "users"
    assert error.path == "users[0].__extra__"


def test_row_direct_unmodelled_credential_key_rejected():
    doc = cloud_config_view(_USERS_SEED)["doc"]
    doc["users"][0]["plain-text-passwd"] = _LITERAL
    error = _both_actions(_USERS_SEED, doc)
    assert (error.node, error.path) == ("users", "users[0].plain-text-passwd")


def test_object_extra_credential_literal_rejected():
    doc = {"user": {"name": "bob", "__extra__": f"plain_text_passwd: {_LITERAL}\n"}}
    error = _both_actions("#cloud-config\n", doc)
    assert (error.node, error.path) == ("default_user", "user.__extra__")


@pytest.mark.parametrize(
    "extra",
    [
        f"list: |\n  root:{_LITERAL}\n",
        f"list:\n  - root:{_LITERAL}\n",
        f"list: ['bob:RANDOM', 'root:{_LITERAL}']\n",
    ],
)
def test_chpasswd_legacy_list_literal_rejected(extra):
    doc = {"chpasswd": {"expire": False, "__extra__": extra}}
    error = _both_actions("#cloud-config\n", doc)
    assert (error.node, error.path) == ("passwords", "chpasswd.__extra__")


def test_chpasswd_row_extra_password_literal_rejected():
    doc = {"chpasswd": {"users": [{"name": "root", "type": "RANDOM", "__extra__": f"password: {_LITERAL}\n"}]}}
    error = _both_actions("#cloud-config\n", doc)
    assert (error.node, error.path) == ("passwords", "chpasswd.users[0].__extra__")


@pytest.mark.parametrize(
    ("extra", "node"),
    [
        (f"passwd: {_LITERAL}\n", "__advanced__"),
        (f"plain_text_passwd: {_LITERAL}\n", "__advanced__"),
        (f"users:\n  - name: bob\n    plain_text_passwd: {_LITERAL}\n", "users"),
        (f"user: {{name: bob, hashed-passwd: {_LITERAL}}}\n", "default_user"),
        (f"chpasswd:\n  users:\n    - {{name: root, password: {_LITERAL}, type: text}}\n", "passwords"),
        (f"chpasswd:\n  list: |\n    root:{_LITERAL}\n", "passwords"),
        (f"autoinstall:\n  identity:\n    password: {_LITERAL}\n", "__advanced__"),
    ],
)
def test_advanced_yaml_credential_literal_rejected(extra, node):
    doc = {"hostname": "{{hostname}}"}
    error = _both_actions("#cloud-config\n", doc, extra)
    assert (error.node, error.path) == (node, "__advanced__")


def test_yaml_value_field_credential_literal_rejected():
    doc = {"chef": {"initial_attributes": f"db:\n  password: {_LITERAL}\n"}}
    error = _both_actions("#cloud-config\n", doc)
    assert (error.node, error.path) == ("chef", "chef.initial_attributes")


def test_node_yaml_tab_never_echoes_rejected_literal(client):
    # The per-node YAML tab only renders preview's node_yaml; posted __yaml__ state is ignored.
    smuggled = {"users": f"users:\n  - {{name: a, plain_text_passwd: {_LITERAL}}}\n"}
    doc = {"hostname": "{{hostname}}", "__yaml__": smuggled}
    preview = cloud_config_preview("#cloud-config\n", json.dumps(doc), "")
    assert _LITERAL not in json.dumps(preview)
    login(client)
    doc = cloud_config_view(_USERS_SEED)["doc"]
    doc["users"][0]["__extra__"] = f"plain_text_passwd: {_LITERAL}\n"
    response = client.post(
        "/api/cloud-init/editor",
        json={"action": "preview", "seed": _USERS_SEED, "cc_json": json.dumps(doc), "cc_extra_yaml": ""},
    )
    assert response.status_code == 400
    assert "node_yaml" not in response.json()
    assert _LITERAL not in response.text


def test_credential_placeholders_still_accepted_in_yaml_side_doors():
    doc = cloud_config_view(_USERS_SEED)["doc"]
    doc["users"][0]["__extra__"] = (
        "plain_text_passwd: {{password}}\npasswd: {{password_hash}}\nhashed_passwd: $6$rounds=4096$salt$hash\n"
    )
    doc["chpasswd"] = {
        "__extra__": "list: |\n  root:{{password}}\n  bob:RANDOM\n  carol:R\n",
        "users": [{"name": "dave", "type": "RANDOM", "__extra__": "password: {{password}}\n"}],
    }
    extra = "user: {name: bob, plain-text-passwd: {{password}}}\npassword: {{password}}\n"
    out = apply_cloudinit_editor(_USERS_SEED, json.dumps(doc), extra)
    loaded = _loaded(out)
    assert loaded["users"][0]["plain_text_passwd"] == "__PXE_password__"
    assert loaded["users"][0]["passwd"] == "__PXE_password_hash__"
    assert loaded["user"]["plain-text-passwd"] == "__PXE_password__"
    assert "root:{{password}}" in out
    assert cloud_config_preview(_USERS_SEED, json.dumps(doc), extra)["seed"] == out


def test_editor_hash_rule_matches_save_validation():
    """Only hashed_passwd takes a crypt hash, in the editor and on save alike."""
    from src.seed_render import validate_seed_template

    hashed = "$6$rounds=4096$salt$hash"
    accepted = {"users": [{"name": "a", "hashed_passwd": hashed}]}
    out = apply_cloudinit_editor("#cloud-config\n", json.dumps(accepted), "")
    validate_seed_template(out, OsFamily.linux)
    for doc in (
        {"password": hashed},
        {"users": [{"name": "a", "plain_text_passwd": hashed}]},
        {"users": [{"name": "a", "passwd": hashed}]},
        {"users": [{"name": "a", "hashed_passwd": "$notahash"}]},
    ):
        with pytest.raises(EditorError, match="Credential fields must use placeholders"):
            apply_cloudinit_editor("#cloud-config\n", json.dumps(doc), "")


def test_pre_existing_literal_views_but_does_not_apply():
    """Existing policy: a seed that already holds a literal opens in the editor; Apply/Preview refuse it."""
    seeds = {
        f"#cloud-config\nusers:\n  - name: a\n    plain_text_passwd: {_LITERAL}\n": (
            "users",
            "users[0].plain_text_passwd",
        ),
        f"#cloud-config\nusers:\n  - name: a\n    password: {_LITERAL}\n": ("users", "users[0].__extra__"),
        f"#cloud-config\nchpasswd:\n  list: |\n    root:{_LITERAL}\n": ("passwords", "chpasswd.__extra__"),
        f"#cloud-config\nplain_text_passwd: {_LITERAL}\n": ("__advanced__", "__advanced__"),
    }
    for seed, (node, path) in seeds.items():
        view = cloud_config_view(seed)
        with pytest.raises(EditorError) as info:
            apply_cloudinit_editor(seed, json.dumps(view["doc"]), view["extra_yaml"])
        assert (info.value.node, info.value.path) == (node, path), seed


def test_editor_api_apply_rejects_smuggled_literal(client):
    login(client)
    doc = cloud_config_view(_USERS_SEED)["doc"]
    doc["users"][0]["__extra__"] = f"plain_text_passwd: {_LITERAL}\n"
    response = client.post(
        "/api/cloud-init/editor",
        json={"action": "apply", "seed": _USERS_SEED, "cc_json": json.dumps(doc), "cc_extra_yaml": ""},
    )
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["detail"].startswith("Credential fields must use placeholders")
    assert body["node"] == "users"
    assert body["path"] == "users[0].__extra__"
    assert _LITERAL not in response.text
    advanced = client.post(
        "/api/cloud-init/editor",
        json={
            "action": "apply",
            "seed": _USERS_SEED,
            "cc_json": json.dumps({"hostname": "h"}),
            "cc_extra_yaml": f"passwd: {_LITERAL}\n",
        },
    )
    assert advanced.status_code == 400
    assert advanced.json()["path"] == "__advanced__"
    assert _LITERAL not in advanced.text


# --------------------------------------------------------------------------- minimal rewrites


def test_no_op_apply_returns_seed_byte_for_byte():
    seeds = [
        _factory_seed(),
        _factory_seed().replace("\n", "\r\n"),
        '#cloud-config\n# note\nhostname: "quoted"\nusers:\n  - {name: a, sudo: "ALL=(ALL) NOPASSWD:ALL"}\n',
    ]
    for seed in seeds:
        view = cloud_config_view(seed)
        assert apply_cloudinit_editor(seed, json.dumps(view["doc"]), view["extra_yaml"]) == seed
        assert cloud_config_preview(seed, json.dumps(view["doc"]), view["extra_yaml"])["seed"] == seed


def _without_user_data(text: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "user-data:")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent
        ),
        len(lines),
    )
    return lines[:start] + lines[end:]


def test_user_data_edit_leaves_installer_block_lines_identical():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    doc = view["doc"]
    doc["runcmd"] = ["echo splice-marker"]
    out = apply_cloudinit_editor(seed, json.dumps(doc), view["extra_yaml"])
    assert _without_user_data(out) == _without_user_data(seed)
    assert 'name: "en*"' in out and "arches: [amd64, i386]" in out
    assert _user_data(out)["runcmd"] == ["echo splice-marker"]
    assert cloud_config_preview(seed, json.dumps(doc), view["extra_yaml"])["seed"] == out
    crlf = seed.replace("\n", "\r\n")
    assert apply_cloudinit_editor(crlf, json.dumps(doc), view["extra_yaml"]) == out.replace("\n", "\r\n")


def test_user_data_splice_inserts_missing_block_and_falls_back_when_unsafe():
    seed = '#cloud-config\n# keep\nautoinstall:\n  version: 1\n  apt:\n    arches: [amd64, i386]\n  x: "q"\n\n# tail\n'
    out = apply_cloudinit_editor(seed, json.dumps({"hostname": "{{hostname}}"}), "")
    assert out == (
        '#cloud-config\n# keep\nautoinstall:\n  version: 1\n  apt:\n    arches: [amd64, i386]\n  x: "q"\n'
        "  user-data:\n    hostname: {{hostname}}\n\n# tail\n"
    )
    flow = '#cloud-config\nautoinstall: {version: 1, x: "q", user-data: {hostname: a}}\n'
    out = apply_cloudinit_editor(flow, json.dumps({"hostname": "b"}), "")
    assert _loaded(out) == {"autoinstall": {"version": 1, "x": "q", "user-data": {"hostname": "b"}}}
    assert "autoinstall:\n" in out  # full dump, not a splice into the flow mapping


# --------------------------------------------------------------------------- installer (autoinstall) section


def _installer_apply(seed: str, installer_doc: dict, installer_extra: str = "") -> str:
    view = cloud_config_view(seed)
    return apply_cloudinit_editor(
        seed, json.dumps(view["doc"]), view["extra_yaml"], json.dumps(installer_doc), installer_extra
    )


def _yaml_paths(value, prefix: str = ""):
    """Every mapping key path in a parsed YAML value (list items share their parent's path)."""
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path
            yield from _yaml_paths(item, path)
    elif isinstance(value, list):
        for item in value:
            yield from _yaml_paths(item, prefix)


def _state_paths(value, prefix: str = ""):
    """Key paths the editor state models (block placeholders are leaves; __extra__ is excluded)."""
    if isinstance(value, dict):
        if set(value) == {"__pxe_block__"}:
            return
        for key, item in value.items():
            if str(key).startswith("__"):
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path
            yield from _state_paths(item, path)
    elif isinstance(value, list):
        for item in value:
            yield from _state_paths(item, prefix)


def test_default_seed_every_key_has_a_form_card():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    assert view["mode"] == "autoinstall"
    assert view["notices"] == []
    assert view["extra_yaml"] == ""
    assert view["installer_extra_yaml"] == ""
    auto = _parsed_autoinstall(seed)
    installer = {key: value for key, value in auto.items() if key != "user-data"}
    assert list(view["installer_doc"]) == list(installer)
    assert set(view["doc"]) == set(auto["user-data"])
    # Nested keys too. Netplan ethernets become cards whose mapping key is the card's "id".
    ethernets = installer["network"]["ethernets"]
    network = {**installer["network"], "ethernets": [{"id": key, **value} for key, value in ethernets.items()]}
    renamed = {**installer, "network": network}
    assert set(_yaml_paths(renamed)) <= set(_state_paths(view["installer_doc"]))
    assert set(_yaml_paths(auto["user-data"])) <= set(_state_paths(view["doc"]))
    user = view["doc"]["users"][0]
    assert {"name", "lock_passwd", "sudo", "shell", "ssh_authorized_keys"} <= set(user)
    assert view["doc"]["chpasswd"]["expire"] is False
    assert view["installer_nodes"][0]["id"] == "ai_version"


def test_installer_view_model_placeholders_and_commands():
    view = cloud_config_view(_factory_seed())
    idoc = view["installer_doc"]
    assert idoc["locale"] == "en_US.UTF-8"
    assert idoc["source"]["id"] == "{{source_id}}"
    assert idoc["identity"]["password"] == "{{password_hash}}"
    assert idoc["ssh"]["authorized-keys"] == {"__pxe_block__": "ssh_keys"}
    assert idoc["packages"] == {"__pxe_block__": "packages"}
    assert [row["id"] for row in idoc["network"]["ethernets"]] == ["zz-all-en", "zz-all-eth"]
    assert idoc["network"]["ethernets"][0]["match"] == {"name": "en*"}
    assert idoc["apt"]["mirror-selection"]["primary"][0]["arches"] == ["amd64", "i386"]
    assert idoc["storage"]["layout"] == {"name": "direct", "match": {"size": "largest"}}
    early = idoc["early-commands"]
    assert early[0].endswith("\n") and "phy80211" in early[0]
    assert "{{imaging_url}}" in early[1]
    assert "{{install_log_url}}" in idoc["error-commands"][0]
    assert "{{phone_home_url}}" in idoc["late-commands"][0]
    fields = {field["key"]: field for node in view["installer_nodes"] for field in node["fields"]}
    for key in ("early-commands", "late-commands", "error-commands"):
        assert fields[key]["type"] == "command_list" and fields[key]["locked"] is True
        assert fields[key]["managed_commands"] and fields[key]["lock_warning"]


def test_installer_cloud_config_seed_has_no_installer():
    view = cloud_config_view("#cloud-config\nhostname: a\n")
    assert view["installer_doc"] == {} and view["installer_extra_yaml"] == ""
    assert "installer_nodes" not in view
    with pytest.raises(EditorError):
        apply_cloudinit_editor("#cloud-config\nhostname: a\n", "{}", "", json.dumps({"locale": "C"}), "")


def test_installer_noop_is_byte_for_byte():
    seed = _factory_seed()
    assert _installer_apply(seed, cloud_config_view(seed)["installer_doc"]) == seed
    crlf = seed.replace("\n", "\r\n")
    assert _installer_apply(crlf, cloud_config_view(crlf)["installer_doc"]) == crlf


def test_installer_edit_locale_and_storage_layout_rewrites_only_those_keys():
    seed = _factory_seed()
    idoc = cloud_config_view(seed)["installer_doc"]
    idoc["locale"] = "de_DE.UTF-8"
    idoc["storage"]["layout"]["name"] = "lvm"
    out = _installer_apply(seed, idoc)
    changed = [(a, b) for a, b in zip(seed.splitlines(), out.splitlines(), strict=True) if a != b]
    assert changed == [("  locale: en_US.UTF-8", "  locale: de_DE.UTF-8"), ("      name: direct", "      name: lvm")]
    auto = _parsed_autoinstall(out)
    assert auto["storage"]["layout"] == {"name": "lvm", "match": {"size": "largest"}}
    # Block scalars, placeholders and list placeholders survive untouched.
    assert "  early-commands:\n    - |\n      sh -c 'for p in" in out
    assert "    authorized-keys:\n      {{ssh_keys}}\n" in out
    assert "  packages:\n    {{packages}}\n" in out


def test_installer_edit_late_commands_keeps_block_scalars():
    seed = _factory_seed()
    idoc = cloud_config_view(seed)["installer_doc"]
    idoc["late-commands"].insert(1, "echo one\necho two\n")
    out = _installer_apply(seed, idoc)
    auto = _parsed_autoinstall(out)
    assert auto["late-commands"][1] == "echo one\necho two\n"
    assert "    - |\n      echo one\n      echo two\n" in out
    assert "__PXE_phone_home_url__" in auto["late-commands"][0]
    assert "sysrq-trigger" in auto["late-commands"][2]
    head = out.partition("  late-commands:")[0]
    assert seed.startswith(head)
    assert _installer_apply(out, cloud_config_view(out)["installer_doc"]) == out


def test_installer_unmodeled_key_kept_with_notice():
    seed = _factory_seed().replace("  shutdown: reboot\n", "  shutdown: reboot\n  zz-custom:\n    flag: true\n")
    view = cloud_config_view(seed)
    assert any("zz-custom" in note for note in view["notices"])
    assert "zz-custom" not in view["installer_doc"]
    assert "zz-custom:" in view["installer_extra_yaml"]
    idoc = view["installer_doc"]
    idoc["locale"] = "fr_FR.UTF-8"
    out = _installer_apply(seed, idoc, view["installer_extra_yaml"])
    auto = _parsed_autoinstall(out)
    assert auto["zz-custom"] == {"flag": True}
    assert auto["locale"] == "fr_FR.UTF-8"
    out = _installer_apply(seed, cloud_config_view(seed)["installer_doc"], "zz-custom:\n  flag: false\n")
    assert _parsed_autoinstall(out)["zz-custom"] == {"flag": False}
    with pytest.raises(EditorError) as err:
        _installer_apply(seed, cloud_config_view(seed)["installer_doc"], "locale: C.UTF-8\n")
    assert err.value.path == "__installer_extra__"


def test_installer_removing_a_section_drops_the_key():
    seed = _factory_seed()
    idoc = cloud_config_view(seed)["installer_doc"]
    del idoc["updates"]
    out = _installer_apply(seed, idoc)
    assert "updates" not in _parsed_autoinstall(out)
    assert "  shutdown: reboot\n  early-commands:" in out


def test_installer_identity_password_literal_rejected():
    seed = _factory_seed()
    idoc = cloud_config_view(seed)["installer_doc"]
    idoc["identity"]["password"] = "hunter2-literal"
    with pytest.raises(EditorError) as err:
        _installer_apply(seed, idoc)
    assert err.value.node == "ai_identity"
    assert err.value.path == "identity.password"
    assert "hunter2-literal" not in str(err.value)
    idoc["identity"]["password"] = "$6$rounds=5000$salt$hash"
    with pytest.raises(EditorError):
        _installer_apply(seed, idoc)  # placeholder-only, the rule seed_render applies on save
    idoc["identity"]["password"] = "{{password}}"
    assert _parsed_autoinstall(_installer_apply(seed, idoc))["identity"]["password"] == "__PXE_password__"


def test_installer_literal_secrets_in_extra_and_commands_rejected():
    seed = _factory_seed()
    view = cloud_config_view(seed)
    with pytest.raises(EditorError) as err:
        _installer_apply(seed, view["installer_doc"], "active-directory:\n  password: s3cret-literal\n")
    assert err.value.path == "__installer_extra__" and "s3cret-literal" not in str(err.value)
    idoc = view["installer_doc"]
    idoc["late-commands"].append("cat > /target/etc/x.conf <<END\npassword: s3cret-literal\nEND\n")
    with pytest.raises(EditorError) as err:
        _installer_apply(seed, idoc)
    assert err.value.node == "ai_late_commands"
    assert err.value.path == "late-commands[2]"
    assert "s3cret-literal" not in str(err.value)


def test_installer_api_and_old_payload(client):
    login(client)
    seed = _factory_seed()
    view = client.post("/api/cloud-init/editor", json={"action": "view", "seed": seed}).json()
    assert "early-commands" in view["installer_doc"]
    old = client.post(
        "/api/cloud-init/editor",
        json={"action": "apply", "seed": seed, "cc_json": json.dumps(view["doc"]), "cc_extra_yaml": ""},
    )
    assert old.status_code == 200 and old.json()["seed"] == seed
    idoc = view["installer_doc"]
    idoc["locale"] = "en_GB.UTF-8"
    body = {
        "seed": seed,
        "cc_json": json.dumps(view["doc"]),
        "cc_extra_yaml": "",
        "installer_json": json.dumps(idoc),
        "installer_extra_yaml": "",
    }
    preview = client.post("/api/cloud-init/editor", json={"action": "preview", **body})
    assert preview.status_code == 200
    assert preview.json()["node_yaml"]["ai_locale"] == "locale: en_GB.UTF-8"
    applied = client.post("/api/cloud-init/editor", json={"action": "apply", **body}).json()["seed"]
    assert "  locale: en_GB.UTF-8\n" in applied and "late-commands:" in applied
    idoc["identity"]["password"] = "LiteralPw-xyz"
    bad = client.post("/api/cloud-init/editor", json={"action": "apply", **body, "installer_json": json.dumps(idoc)})
    assert bad.status_code == 400
    assert bad.json()["node"] == "ai_identity" and bad.json()["path"] == "identity.password"
    assert "LiteralPw-xyz" not in bad.text
