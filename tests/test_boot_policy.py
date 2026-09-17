from tests.conftest import login

from src.boot.policy import ScriptKind, decide_script
from src.inventory.service import create_image, deploy_machine, mark_deployed, stage_machine
from src.models import OsFamily


def test_colon_mac_path_registers(client):
    response = client.get("/ipxe/02:00:00:00:00:aa")
    assert response.status_code == 200
    assert "Continuing to next boot device" in response.text
    login(client)
    machines = client.get("/api/machines").json()
    assert machines[0]["mac"] == "02:00:00:00:00:aa"


def test_unknown_mac_registers_and_waits(client):
    response = client.get("/ipxe/02-00-00-00-00-01")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("#!ipxe")
    assert "Continuing to next boot device" in body
    assert "sleep 5" in body
    assert "exit" in body
    assert "choose" not in body
    assert "secret" not in body.lower()
    assert "password" not in body.lower()
    login(client)
    machines = client.get("/api/machines").json()
    assert machines[0]["mac"] == "02:00:00:00:00:01"
    assert machines[0]["state"] == "pending"


def test_deployed_shows_folder_menu(client):
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
    assert "Waiting for operator" not in response.text
    assert "menu " in response.text
    assert "choose" in response.text
    assert "Continue to next boot device" in response.text
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
    assert "ds=nocloud" in response.text
    assert "nocloud-net" not in response.text
    assert r"\;s=" in response.text
    assert ";s=" not in response.text.replace(r"\;s=", "")
    kernel_line = next(line for line in response.text.splitlines() if line.startswith("kernel "))
    assert kernel_line.split()[1].startswith("--name=")
    assert "autoinstall" in kernel_line.split()
    assert kernel_line.split()[3] == "autoinstall"
    assert kernel_line.endswith(" ---") or " ---" in kernel_line
    assert "imgargs vmlinuz " in response.text
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
    assert "event=imaging" in startnet.text
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


def test_imaging_still_serves_linux_install(client):
    client.get("/ipxe/02-00-00-00-00-09")
    login(client)
    from src.db import session_scope
    from src.inventory.service import find_by_mac
    from src.models import MachineState

    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu-imaging",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = find_by_mac(db, "02:00:00:00:00:09")
        assert machine is not None
        deploy_machine(db, machine, image=image, actor="admin")
        machine.state = MachineState.imaging.value
        db.add(machine)
        db.commit()
    response = client.get("/ipxe/02-00-00-00-00-09")
    assert "kernel" in response.text
    assert "autoinstall" in response.text
    assert "Waiting" not in response.text


def test_imaging_timeout_serves_wait_menu(client):
    client.get("/ipxe/02-00-00-00-00-10")
    login(client)
    from datetime import UTC, datetime, timedelta

    from src.db import session_scope
    from src.inventory.service import find_by_mac
    from src.models import MachineState

    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu-timeout",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = find_by_mac(db, "02:00:00:00:00:10")
        assert machine is not None
        deploy_machine(db, machine, image=image, actor="admin")
        machine.state = MachineState.imaging.value
        machine.imaging_started_at = datetime.now(UTC) - timedelta(minutes=16)
        db.add(machine)
        db.commit()
        kind = decide_script(db, machine)
        assert kind == ScriptKind.install_linux
    response = client.get("/ipxe/02-00-00-00-00-10")
    assert "menu " in response.text
    assert "choose" in response.text
    assert "kernel" not in response.text
    assert client.get("/api/machines").json()[0]["state"] == "timeout_error"


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
    assert "iso-url=" in text
    assert "image.iso" in text
    assert not any(
        token.endswith("/iso") for token in text.split() if token.startswith("url=") or token.startswith("iso-url=")
    )
    iso = client.get("/install-files/1/image.iso")
    assert iso.status_code == 200
    assert iso.content == b"iso-bytes"
    assert "root=/dev/ram0" in text
    assert "ramdisk_size=1500000" in text
    assert "autoinstall" in text
    assert "cloud-config-url=/dev/null" in text
    assert "sanboot" not in text
    assert r"\;s=" in text
    assert ";s=" not in text.replace(r"\;s=", "")
    assert "ds=nocloud" in text
    assert "nocloud-net" not in text
    kernel_line = next(line for line in text.splitlines() if line.startswith("kernel "))
    assert kernel_line.split()[3] == "autoinstall"
    assert kernel_line.rstrip().endswith("---")
    assert "reboot=force" in text


def test_linux_nfs_casper_skips_iso_url(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import deploy_machine, register_machine

    with session_scope() as db:
        image = create_image(
            db,
            name="ubuntu-nfs",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            iso_path="ubuntu/live.iso",
            actor="admin",
        )
        image.extract_generation = "nfs/1/1"
        db.add(image)
        machine = register_machine(db, mac="02:00:00:00:00:08", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
    text = client.get("/ipxe/02-00-00-00-00-08").text
    assert "netboot=nfs" in text
    assert "nfsroot=pxe.test:/var/lib/pxe/images/nfs/1/1" in text
    assert "live-media-path=" not in text
    assert "NFSOPTS=vers=3,tcp,port=2049" in text
    assert ",vers=" not in text
    assert "mountport=" not in text
    assert "iso-url=" not in text
    assert "ramdisk_size" not in text
    assert "boot=casper" in text
    assert "noprompt" in text
    assert "quickreboot" in text
    assert "reboot=force" in text
    assert "secret" not in text.lower()
    assert r"\;s=" in text
    assert "cloud-config-url=${seed-url}user-data" in text
    assert "cloud-config-url=/dev/null" not in text
    assert "ds=nocloud" in text
    assert "nocloud-net" not in text
    kernel_line = next(line for line in text.splitlines() if line.startswith("kernel "))
    assert kernel_line.split()[3] == "autoinstall"
    assert kernel_line.rstrip().endswith("---")
    assert "imgargs vmlinuz " in text


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
