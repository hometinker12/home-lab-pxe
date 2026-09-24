import pytest
import yaml

from src.models import OsFamily
from src.seed_render import SeedError, validate_seed_template, validate_tokens
from src.seed_store import factory_seed_text, read_image_seed, write_image_seed
from src.settings import clear_settings_cache


def test_factory_starters_validate():
    validate_seed_template(factory_seed_text(OsFamily.linux), OsFamily.linux)
    validate_seed_template(factory_seed_text(OsFamily.windows), OsFamily.windows)


def test_unknown_token_rejected():
    with pytest.raises(SeedError, match="Unknown placeholder"):
        validate_tokens("hostname: {{not_a_token}}")


def test_literal_password_rejected():
    body = "#cloud-config\nautoinstall:\n  version: 1\n  user-data:\n    password: hunter2\n"
    with pytest.raises(SeedError, match="placeholders"):
        validate_seed_template(body, OsFamily.linux)


def test_complete_linux_user_data_fills_unattended_keys():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data("#cloud-config\nautoinstall:\n  version: 1\n  ssh:\n    install-server: true\n")
    assert "locale:" in filled
    assert "storage:" in filled
    assert "search_drivers:" in filled
    assert "offline-install" in filled
    assert "{{imaging_url}}" in filled
    assert "|| true" in filled
    assert "optional: true" in filled
    assert "en*" in filled
    assert "sizing-policy:" in filled
    assert "sysrq-trigger" in filled
    assert "phy80211" in filled


def test_complete_linux_user_data_marks_match_nics_optional():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  network:\n    version: 2\n"
        "    ethernets:\n      zz-all-en:\n        match:\n          name: en*\n"
        "        dhcp4: true\n"
    )
    assert "optional: true" in filled


def test_complete_linux_user_data_answers_autoinstall_prompt():
    import ast

    from src.seed_render import autoinstall_confirm_program, complete_linux_user_data, silence_subiquity_client_source

    sample = (
        "class Client:\n"
        "    async def noninteractive_confirmation(self):\n"
        "        print(_(\"Add 'autoinstall' to your kernel command line to avoid this\"))\n"
        "        answer = await run_in_thread(input)\n"
        "\n"
        "    async def _status_get(self, cur=None):\n"
        "        return cur\n"
    )
    patched = silence_subiquity_client_source(sample)
    assert patched is not None
    assert "kernel command line" not in patched
    assert "await self.confirm_install()" in patched
    assert "async def _status_get" in patched

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n",
        public_url="http://192.168.2.223:8080",
    )
    parsed = yaml.safe_load(filled)
    cmds = parsed["autoinstall"]["early-commands"]
    assert "autoinstall-confirm.py" in cmds[0]
    assert "python3 - << 'PY'" not in cmds[0]
    assert "silence_subiquity" not in filled
    program = autoinstall_confirm_program()
    ast.parse(program)
    assert "/meta/confirm" in program
    assert "noninteractive_confirmation" in program
    again = complete_linux_user_data(filled, public_url="http://192.168.2.223:8080")
    again_cmds = yaml.safe_load(again)["autoinstall"]["early-commands"]
    assert sum("autoinstall-confirm.py" in str(cmd) for cmd in again_cmds) == 1

    legacy = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  early-commands:\n"
        "    - |\n      python3 - << 'PY' || true\n      /meta/confirm\n      PY\n"
    )
    parsed_legacy = yaml.safe_load(legacy)
    legacy_cmds = parsed_legacy["autoinstall"]["early-commands"]
    assert sum("autoinstall-confirm.py" in str(cmd) for cmd in legacy_cmds) == 1
    assert "python3 - << 'PY'" not in legacy


def test_autoinstall_confirm_script_is_fetchable(client):
    import ast

    response = client.get("/boot-files/autoinstall-confirm.py")
    assert response.status_code == 200
    ast.parse(response.text)
    assert "/meta/confirm" in response.text
    assert "noninteractive_confirmation" in response.text


def test_complete_linux_user_data_unbinds_wifi_once():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data("#cloud-config\nautoinstall:\n  version: 1\n  early-commands:\n    - echo hi\n")
    assert filled.count("phy80211") == 1
    parsed = yaml.safe_load(filled)
    wifi = [cmd for cmd in parsed["autoinstall"]["early-commands"] if "phy80211" in cmd]
    assert len(wifi) == 1
    assert wifi[0].startswith("sh -c 'for p in /sys/class/net")
    again = complete_linux_user_data(filled)
    assert again.count("phy80211") == 1


def test_complete_linux_user_data_uses_live_imaging_url():
    from src.seed_render import complete_linux_user_data

    url = "http://pxe.test/api/machines/4/events?event=imaging"
    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  early-commands:\n    - echo hi\n",
        imaging_url=url,
    )
    assert url in filled
    assert filled.count("event=imaging") == 1


def test_factory_linux_autoinstall_identity():
    text = factory_seed_text(OsFamily.linux)
    assert "\n  identity:\n" in text
    assert "hostname: {{hostname}}" in text
    assert "username: {{username}}" in text
    assert "password: {{password_hash}}" in text
    assert "\n  timezone:" not in text
    assert "timezone: {{timezone}}" in text
    validate_seed_template(text, OsFamily.linux)


def test_complete_linux_user_data_strips_root_timezone_and_fills_identity():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  timezone: UTC\n  user-data:\n"
        "    hostname: pxe-1\n    timezone: UTC\n    chpasswd:\n      users:\n"
        "        - name: labadmin\n          password: $6$rounds=5000$abc$def\n"
    )
    parsed = yaml.safe_load(filled)
    auto = parsed["autoinstall"]
    assert "timezone" not in auto
    assert auto["user-data"]["timezone"] == "UTC"
    assert auto["identity"]["hostname"] == "pxe-1"
    assert auto["identity"]["username"] == "labadmin"
    assert auto["identity"]["password"].startswith("$6$")


def test_complete_linux_user_data_remaps_reserved_identity_username():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  identity:\n"
        "    hostname: pxe-1\n    username: root\n    password: $6$abc$def\n"
        "  user-data:\n    hostname: pxe-1\n    chpasswd:\n      users:\n"
        "        - name: root\n          password: $6$abc$def\n"
    )
    parsed = yaml.safe_load(filled)
    auto = parsed["autoinstall"]
    assert auto["identity"]["username"] == "ubuntu"
    assert auto["user-data"]["chpasswd"]["users"][0]["name"] == "root"


def test_factory_linux_seed_has_imaging_callback():
    text = factory_seed_text(OsFamily.linux)
    assert "{{imaging_url}}" in text
    assert "{{phone_home_url}}" in text
    assert "{{ssh_keys}}" in text
    assert "{{packages}}" in text
    assert "|| true" in text
    assert "cdrom.list" not in text
    assert "{{source_id}}" in text
    assert "ubuntu-server-minimal" not in text
    assert "optional: true" in text
    assert 'name: "en*"' in text
    assert 'name: "eth*"' in text
    assert "sysrq-trigger" in text
    assert "phy80211" in text


def test_factory_linux_seed_substitutes_lists():
    from src.seed_render import dummy_values, substitute_yaml

    values = dummy_values()
    values["ssh_keys"] = ["ssh-ed25519 AAAA lab@host"]
    values["packages"] = ["qemu-guest-agent"]
    rendered = substitute_yaml(factory_seed_text(OsFamily.linux), values)
    parsed = yaml.safe_load(rendered)
    auto = parsed["autoinstall"]
    assert auto["ssh"]["authorized-keys"] == ["ssh-ed25519 AAAA lab@host"]
    assert auto["packages"] == ["qemu-guest-agent"]
    assert auto["source"]["id"] == "ubuntu-server"
    assert auto["source"]["search_drivers"] is False
    assert auto["user-data"]["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAA lab@host"]
    validate_seed_template(factory_seed_text(OsFamily.linux), OsFamily.linux)


def test_factory_linux_seed_omits_empty_ssh_keys():
    from src.seed_render import dummy_values, substitute_yaml

    rendered = substitute_yaml(factory_seed_text(OsFamily.linux), dummy_values())
    parsed = yaml.safe_load(rendered)
    auto = parsed["autoinstall"]
    assert "authorized-keys" not in auto["ssh"]
    assert "ssh_authorized_keys" not in auto["user-data"]["users"][0]
    assert "packages" not in auto
    assert "[] is too short" not in rendered
    assert "ssh_authorized_keys: []" not in rendered
    assert "authorized-keys: []" not in rendered


def test_complete_linux_user_data_drops_empty_ssh_authorized_keys():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  ssh:\n    install-server: true\n"
        "    authorized-keys: []\n  user-data:\n    users:\n      - name: ubuntu\n"
        "        ssh_authorized_keys: []\n"
    )
    parsed = yaml.safe_load(filled)
    assert "authorized-keys" not in parsed["autoinstall"]["ssh"]
    assert "ssh_authorized_keys" not in parsed["autoinstall"]["user-data"]["users"][0]


def test_complete_linux_user_data_rewrites_empty_post_data_wget():
    from src.seed_render import complete_linux_user_data

    url = "http://pxe.test/api/machines/4/events?event=imaging"
    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  early-commands:\n"
        f"    - wget -q --tries=3 --timeout=10 --post-data= -O /dev/null {url} || true\n"
    )
    assert "--post-file=/dev/null" in filled
    assert "--post-data=" not in filled


def test_complete_linux_user_data_skips_non_autoinstall():
    from src.seed_render import complete_linux_user_data

    text = "#cloud-config\nhostname: stay\n"
    assert complete_linux_user_data(text) == text


def test_complete_linux_user_data_appends_force_reboot_once():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  late-commands:\n"
        "    - wget -q --post-file=/dev/null -O /dev/null {{phone_home_url}} || true\n"
    )
    assert "sync; sync;" in filled
    assert "echo b > /proc/sysrq-trigger" in filled
    assert "echo s >" not in filled
    again = complete_linux_user_data(filled)
    assert again.count("sysrq-trigger") == filled.count("sysrq-trigger")
    assert "echo s >" not in again
    legacy = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  late-commands:\n"
        "    - sh -c 'echo 1 > /proc/sys/kernel/sysrq; echo s > /proc/sysrq-trigger; "
        "echo u > /proc/sysrq-trigger; echo b > /proc/sysrq-trigger'\n"
    )
    assert "sync; sync;" in legacy
    assert "echo s >" not in legacy


def test_complete_linux_user_data_injects_source_id():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  ssh:\n    install-server: true\n",
        source_id="ubuntu-server-minimal",
    )
    parsed = yaml.safe_load(filled)
    assert parsed["autoinstall"]["source"]["id"] == "ubuntu-server-minimal"
    assert parsed["autoinstall"]["source"]["search_drivers"] is False


def test_complete_linux_user_data_strips_empty_source_id():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  source:\n    id: ''\n    search_drivers: false\n"
    )
    parsed = yaml.safe_load(filled)
    assert "id" not in parsed["autoinstall"]["source"]


def test_complete_linux_user_data_keeps_seed_comments():
    from src.seed_render import complete_linux_user_data

    token = "pxe-smoke-token-keep"
    filled = complete_linux_user_data(
        f"#cloud-config\n# {token}\nautoinstall:\n  version: 1\n  ssh:\n    install-server: true\n"
    )
    assert token in filled
    assert "locale:" in filled


def test_seed_path_confinement(tmp_path, monkeypatch):
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path))
    clear_settings_cache()
    write_image_seed(3, OsFamily.linux, factory_seed_text(OsFamily.linux))
    assert "{{hostname}}" in read_image_seed(3, OsFamily.linux)
    assert (tmp_path / "uploads" / "3" / "user-data").is_file()


def test_seed_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("PXE_MAX_SEED_BYTES", "32")
    clear_settings_cache()
    with pytest.raises(SeedError, match="PXE_MAX_SEED_BYTES"):
        write_image_seed(1, OsFamily.linux, "x" * 40)


def _factory_linux_rendered() -> str:
    from src.seed_render import dummy_values, substitute_yaml

    return substitute_yaml(factory_seed_text(OsFamily.linux), dummy_values())


def _late_commands(text: str) -> list:
    return [str(cmd) for cmd in yaml.safe_load(text)["autoinstall"]["late-commands"]]


def test_complete_linux_user_data_orders_uefi_before_reboot():
    from src.seed_render import complete_linux_user_data

    for policy in ("pxe", "disk"):
        filled = complete_linux_user_data(
            _factory_linux_rendered(),
            public_url="http://192.168.100.250:8080",
            next_boot_device=policy,
            machine_id="12",
        )
        cmds = _late_commands(filled)
        index = next(i for i, cmd in enumerate(cmds) if "uefi-boot-order.py" in cmd)
        assert "echo b > /proc/sysrq-trigger" in cmds[index + 1]
        assert any("/events" in cmd and "--post-file" in cmd for cmd in cmds[:index])
        order_cmd = cmds[index]
        assert f"python3 /run/pxe-uefi-boot-order.py {policy} 12 || true" in order_cmd
        assert '"http://192.168.100.250:8080/boot-files/uefi-boot-order.py"' in order_cmd
        assert "sysrq-trigger" not in order_cmd
        assert "reboot -f" not in order_cmd


def test_complete_linux_user_data_uefi_order_idempotent():
    from src.seed_render import complete_linux_user_data

    kwargs = {"public_url": "http://pxe.test:8080", "next_boot_device": "pxe", "machine_id": "12"}
    filled = complete_linux_user_data(_factory_linux_rendered(), **kwargs)
    again = complete_linux_user_data(filled, **kwargs)
    assert sum("uefi-boot-order.py" in cmd for cmd in _late_commands(again)) == 1


def test_complete_linux_user_data_skips_uefi_order_without_policy():
    from src.seed_render import complete_linux_user_data

    for policy, machine_id in (("", "12"), ("pxe", ""), ("cdrom", "12"), ("pxe", "12; reboot")):
        filled = complete_linux_user_data(
            _factory_linux_rendered(),
            public_url="http://pxe.test:8080",
            next_boot_device=policy,
            machine_id=machine_id,
        )
        assert "uefi-boot-order" not in filled


def test_complete_linux_user_data_uefi_order_before_reboot_f():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  late-commands:\n    - echo done\n    - reboot -f\n",
        next_boot_device="disk",
        machine_id="7",
    )
    cmds = _late_commands(filled)
    assert cmds[0] == "echo done"
    assert "uefi-boot-order.py disk 7" in cmds[1]
    assert "{{public_url}}/boot-files/uefi-boot-order.py" in cmds[1]
    assert cmds[2] == "reboot -f"
    assert len(cmds) == 3


def test_render_selected_seed_uses_machine_next_boot_device(client):
    from src.db import session_scope
    from src.inventory.service import create_image, deploy_machine, touch_machine
    from src.seed_render import render_selected_seed

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:51", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "boot1"
        machine.next_boot_device = "disk"
        image = create_image(
            db,
            name="u24-order",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        rendered = render_selected_seed(db, machine)
        mid = int(machine.id)
    order = [cmd for cmd in _late_commands(rendered) if "uefi-boot-order.py" in cmd]
    assert len(order) == 1
    assert f"uefi-boot-order.py disk {mid} || true" in order[0]
    assert '"http://pxe.test:8080/boot-files/uefi-boot-order.py"' in order[0]
