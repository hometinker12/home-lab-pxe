import pytest

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
    assert "ubuntu-server-minimal" in filled
    assert "offline-install" in filled
    assert "proxy:" in filled


def test_complete_linux_user_data_skips_non_autoinstall():
    from src.seed_render import complete_linux_user_data

    text = "#cloud-config\nhostname: stay\n"
    assert complete_linux_user_data(text) == text


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
