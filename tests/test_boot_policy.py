from tests.conftest import login

from src.boot.policy import ScriptKind, decide_script
from src.inventory.service import create_image, deploy_machine, mark_deployed, stage_machine
from src.models import OsFamily


def test_colon_mac_path_registers(client):
    response = client.get("/ipxe/02:00:00:00:00:aa")
    assert response.status_code == 200
    assert "Waiting for operator" in response.text
    login(client)
    machines = client.get("/api/machines").json()
    assert machines[0]["mac"] == "02:00:00:00:00:aa"


def test_unknown_mac_registers_and_waits(client):
    response = client.get("/ipxe/02-00-00-00-00-01")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("#!ipxe")
    assert "Waiting for operator" in body
    assert "sleep 5" in body
    assert "secret" not in body.lower()
    assert "password" not in body.lower()
    login(client)
    machines = client.get("/api/machines").json()
    assert machines[0]["mac"] == "02:00:00:00:00:01"
    assert machines[0]["state"] == "pending"


def test_deployed_skips_menu(client):
    client.get("/ipxe/02-00-00-00-00-02")
    login(client)
    from src.db import session_scope
    from src.inventory.service import find_by_mac

    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = find_by_mac(db, "02:00:00:00:00:02")
        assert machine is not None
        deploy_machine(db, machine, image=image, actor="admin")
        mark_deployed(db, machine, actor="admin")
        db.commit()
    response = client.get("/ipxe/02-00-00-00-00-02")
    assert "Waiting" not in response.text
    assert "exit" in response.text
    assert "kernel" not in response.text


def test_staged_serves_linux_install(client):
    client.get("/ipxe/02-00-00-00-00-03")
    login(client)
    from src.db import session_scope
    from src.inventory.service import find_by_mac

    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu-staged",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = find_by_mac(db, "02:00:00:00:00:03")
        assert machine is not None
        deploy_machine(db, machine, image=image, actor="admin")
        mark_deployed(db, machine, actor="admin")
        stage_machine(db, machine, actor="admin", image=image)
        db.commit()
        kind = decide_script(db, machine)
        assert kind == ScriptKind.install_linux
    response = client.get("/ipxe/02-00-00-00-00-03")
    assert "ds=nocloud-net" in response.text
    assert r"\;s=" in response.text
    assert ";s=" not in response.text.replace(r"\;s=", "")
    assert response.text.endswith("boot\n") or "boot\n" in response.text


def test_windows_install_script_has_unattend_url_not_password(client):
    client.get("/ipxe/02-00-00-00-00-04")
    login(client)
    from src.db import session_scope
    from src.inventory.service import find_by_mac, upsert_local_account
    from src.models import AccountKind

    with session_scope() as db:
        image = create_image(
            db, name="ws2022", os_family=OsFamily.windows, boot_wim_path="windows/boot.wim", actor="admin"
        )
        machine = find_by_mac(db, "02:00:00:00:00:04")
        assert machine is not None
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.windows_administrator,
            username="Administrator",
            password="SuperSecret!",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    response = client.get("/ipxe/02-00-00-00-00-04")
    assert "unattend.xml" in response.text
    assert "winpeshl.ini" in response.text
    assert "startnet.cmd" in response.text
    assert "install.wim" not in response.text
    assert "SuperSecret" not in response.text
    assert "wimboot" in response.text
    seed = client.get(f"/windows/{mid}/unattend.xml")
    assert "SuperSecret" in seed.text
    assert "windowsPE" in seed.text
    startnet = client.get(f"/windows/{mid}/startnet.cmd")
    assert startnet.status_code == 200
    assert r"\pxe-media" in startnet.text
    assert "SuperSecret" not in startnet.text
    api = client.get(f"/api/machines/{mid}").json()
    assert "SuperSecret" not in str(api)
    assert api["account_password_set"] is True


def test_extracting_image_waits_without_attempt(client):
    client.get("/ipxe/02-00-00-00-00-06")
    login(client)
    from src.db import session_scope
    from src.inventory.service import find_by_mac
    from src.models import ExtractStatus, MachineState

    with session_scope() as db:
        image = create_image(db, name="extracting", os_family=OsFamily.linux, actor="admin")
        machine = find_by_mac(db, "02:00:00:00:00:06")
        assert machine is not None
        machine.assigned_image_id = image.id
        machine.state = MachineState.deploying.value
        image.extract_status = ExtractStatus.extracting.value
        db.add(machine)
        db.add(image)
        db.commit()
        kind = decide_script(db, machine)
        assert kind == ScriptKind.image_not_ready
    response = client.get("/ipxe/02-00-00-00-00-06")
    assert "not ready" in response.text.lower()
    assert "kernel" not in response.text
    assert "sanboot" not in response.text


def test_linux_kernel_iso_uses_url_and_autoinstall(client, tmp_path):
    login(client)
    from src.db import session_scope
    from src.inventory.service import deploy_machine, register_machine

    iso = tmp_path / "images" / "ubuntu" / "live.iso"
    iso.parent.mkdir(parents=True)
    iso.write_bytes(b"iso-bytes")
    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu-http",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            iso_path="ubuntu/live.iso",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:07", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
    response = client.get("/ipxe/02-00-00-00-00-07")
    text = response.text
    assert "url=" in text
    assert "autoinstall" in text
    assert "sanboot" not in text
    assert r"\;s=" in text
    assert ";s=" not in text.replace(r"\;s=", "")


def test_linux_iso_image_uses_sanboot(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import create_image, deploy_machine, register_machine
    from src.models import OsFamily

    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu-live",
            os_family=OsFamily.linux,
            iso_path="ubuntu/live.iso",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:05", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
    response = client.get("/ipxe/02-00-00-00-00-05")
    assert "sanboot" in response.text
    assert "/boot-files/" in response.text
    assert "iso" in response.text
    assert "kernel" not in response.text
