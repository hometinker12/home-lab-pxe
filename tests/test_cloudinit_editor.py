from __future__ import annotations

import json

import pytest
import yaml
from tests.conftest import login

from src.cloudinit.editor import apply_cloudinit_editor, cloud_config_view, shield_tokens
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
    assert "data-cc-editor" in page.text


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
