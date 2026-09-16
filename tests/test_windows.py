from tests.conftest import login

from src.inventory.service import create_image, deploy_machine, touch_machine
from src.models import AccountKind, OsFamily
from src.windows.render import render_startnet


def test_windows_unattend_has_setup_and_password(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import upsert_local_account

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:21", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "WINBOX1"
        image = create_image(db, name="ws", os_family=OsFamily.windows, boot_wim_path="windows/boot.wim", actor="admin")
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.windows_administrator,
            username="Administrator",
            password="WinSecret!",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    body = client.get(f"/windows/{mid}/unattend.xml").text
    assert "windowsPE" in body
    assert "WinSecret!" in body
    assert "WINBOX1" in body
    pending = client.get("/windows/99999/unattend.xml")
    assert pending.status_code == 404


def test_windows_startnet_uses_generated_share(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import upsert_local_account

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:22", uuid=None, client_ip="10.0.0.8")
        image = create_image(
            db,
            name="ws-media",
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
        body = render_startnet(db, machine)
        mid = machine.id
    assert r"\pxe-media" in body
    assert r"1\3\setup.exe" in body
    assert "WinSecret!" not in body
    assert "&" not in body.split("net use", 1)[-1].splitlines()[0]
    gated = client.get(f"/windows/{mid}/startnet.cmd")
    assert gated.status_code == 200
    client.post(f"/api/machines/{mid}/events", json={"event": "deployed"})
    assert client.get(f"/windows/{mid}/startnet.cmd").status_code == 404
    assert client.get(f"/windows/{mid}/winpeshl.ini").status_code == 404
