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


def test_complete_linux_user_data_marks_match_nics_optional():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  network:\n    version: 2\n"
        "    ethernets:\n      zz-all-en:\n        match:\n          name: en*\n"
        "        dhcp4: true\n"
    )
    assert "optional: true" in filled


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
    assert "ubuntu-server-minimal" not in text
    assert "optional: true" in text
    assert 'name: "en*"' in text
    assert 'name: "eth*"' in text
    assert "sysrq-trigger" in text


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
    assert auto["user-data"]["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAA lab@host"]
    validate_seed_template(factory_seed_text(OsFamily.linux), OsFamily.linux)


def test_complete_linux_user_data_skips_non_autoinstall():
    from src.seed_render import complete_linux_user_data

    text = "#cloud-config\nhostname: stay\n"
    assert complete_linux_user_data(text) == text


def test_complete_linux_user_data_appends_force_reboot_once():
    from src.seed_render import complete_linux_user_data

    filled = complete_linux_user_data(
        "#cloud-config\nautoinstall:\n  version: 1\n  late-commands:\n"
        "    - wget -q --post-data= -O /dev/null {{phone_home_url}} || true\n"
    )
    assert filled.count("sysrq-trigger") >= 3
    again = complete_linux_user_data(filled)
    assert again.count("sysrq-trigger") == filled.count("sysrq-trigger")


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
