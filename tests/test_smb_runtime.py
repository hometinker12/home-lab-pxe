from tests.conftest import login, seed_url

from src.inventory.service import create_image, deploy_machine, touch_machine
from src.models import AccountKind, OsFamily
from src.smb_runtime import cmd_path, effective_smb_password, generate_smb_password, password_path
from src.windows.render import render_startnet


def test_generate_smb_password_is_url_safe():
    from src.settings import validate_smb_password

    secret = generate_smb_password()
    assert validate_smb_password(secret) == secret
    assert 20 <= len(secret) <= 128


def test_settings_shows_smb_rotate_control(client):
    login(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Windows installation media (SMB)" in page.text
    assert 'action="/settings/smb/rotate"' in page.text
    assert "Rotate password" in page.text
    assert "badge-deployed" in page.text
    assert "test-smb-password-ok" not in page.text


def test_smb_rotate_requires_login(client):
    response = client.post(
        "/settings/smb/rotate",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert "/login" in response.headers.get("location", "")


def test_rotate_smb_password_overrides_env_and_is_write_only(client):
    login(client)
    old = effective_smb_password()
    assert old == "test-smb-password-ok"
    response = client.post("/settings/smb/rotate", follow_redirects=False)
    assert response.status_code in {302, 303}
    location = response.headers.get("location", "")
    assert "section=smb" in location
    assert "notice=smb-rotated" in location
    new = effective_smb_password()
    assert new
    assert new != old
    assert password_path().is_file()
    stored = password_path().read_text(encoding="utf-8")
    assert old not in stored
    assert new not in stored
    assert cmd_path().is_file()
    page = client.get(location)
    assert page.status_code == 200
    assert "Windows SMB password rotated" in page.text
    assert "badge-deployed" in page.text
    assert new not in page.text
    assert old not in page.text
    activity = client.get("/activity")
    assert "Rotated the SMB password" in activity.text
    assert "pxe-media share password rotated" in activity.text
    assert new not in activity.text
    assert old not in activity.text


def test_rotate_smb_password_without_env(client, monkeypatch):
    login(client)
    monkeypatch.delenv("PXE_SMB_PASSWORD", raising=False)
    from src.settings import clear_settings_cache

    clear_settings_cache()
    persisted = password_path()
    if persisted.exists():
        persisted.unlink()
    assert effective_smb_password() == ""
    page = client.get("/settings")
    smb = page.text.split("Windows installation media (SMB)", 1)[1].split("Ubuntu installation media", 1)[0]
    assert "not set" in smb
    response = client.post("/settings/smb/rotate", follow_redirects=False)
    assert response.status_code in {302, 303}
    secret = effective_smb_password()
    assert secret
    again = client.get("/settings?section=smb")
    smb = again.text.split("Windows installation media (SMB)", 1)[1].split("Ubuntu installation media", 1)[0]
    assert ">set</span>" in smb
    assert "not set" not in smb
    assert secret not in again.text


def test_windows_startnet_uses_rotated_smb_password(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import upsert_local_account

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:24", uuid=None, client_ip="10.0.0.8")
        image = create_image(
            db,
            name="ws-smb-rotate",
            os_family=OsFamily.windows,
            boot_wim_path="smb/1/3/sources/boot.wim",
            install_wim_path="smb/1/3/sources/install.wim",
            actor="admin",
        )
        image.extract_generation = "1/3"
        image.extract_status = "ready"
        db.add(image)
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.windows_administrator,
            username="Administrator",
            password="WinSecret!",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        before = render_startnet(db, machine)
        mid = machine.id
    assert "test-smb-password-ok" in before
    client.post("/settings/smb/rotate", follow_redirects=False)
    new = effective_smb_password()
    with session_scope() as db:
        from src.inventory.service import get_machine

        machine = get_machine(db, mid)
        assert machine is not None
        after = render_startnet(db, machine)
    assert new in after
    assert "test-smb-password-ok" not in after
    assert "WinSecret!" not in after
    page = client.get(seed_url(client, mid, "windows", "startnet.cmd"))
    assert page.status_code == 200
    assert new in page.text
    assert "test-smb-password-ok" not in page.text
