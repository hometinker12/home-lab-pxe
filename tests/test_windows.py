import xml.etree.ElementTree as ET

from tests.conftest import login, seed_url

from src.boot.uefi_order import inject_uefi_order_command
from src.inventory.service import create_image, deploy_machine, touch_machine
from src.models import AccountKind, OsFamily
from src.seed_store import factory_seed_text
from src.windows.render import render_startnet, render_unattend_legacy


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
    body = client.get(seed_url(client, mid, "windows", "unattend.xml")).text
    assert "windowsPE" in body
    assert "WinSecret!" in body
    assert "WINBOX1" in body
    assert "/IMAGE/INDEX" in body
    assert "/IMAGE/NAME" not in body
    pending = client.get("/windows/99999/unattend.xml")
    assert pending.status_code == 404


def test_windows_unattend_includes_source_name(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import upsert_local_account

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:23", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "WINBOX2"
        image = create_image(
            db, name="ws-src", os_family=OsFamily.windows, boot_wim_path="windows/boot.wim", actor="admin"
        )
        image.source_id = "Windows Server 2022 SERVERSTANDARDCORE"
        image.wim_index = 2
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
        mid = machine.id
    body = client.get(seed_url(client, mid, "windows", "unattend.xml")).text
    assert "/IMAGE/NAME" in body
    assert "Windows Server 2022 SERVERSTANDARDCORE" in body
    assert "<Value>2</Value>" in body


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
    assert "event=imaging" in body
    assert "WinSecret!" not in body
    assert "&" not in body.split("net use", 1)[-1].splitlines()[0]
    gated = client.get(seed_url(client, mid, "windows", "startnet.cmd"))
    assert gated.status_code == 200
    client.post(f"/api/machines/{mid}/events", json={"event": "deployed"})
    assert client.get(seed_url(client, mid, "windows", "startnet.cmd")).status_code == 404
    assert client.get(seed_url(client, mid, "windows", "winpeshl.ini")).status_code == 404


_NS = "{urn:schemas-microsoft-com:unattend}"


def _order_commands(xml_text: str) -> list:
    root = ET.fromstring(xml_text)
    return [
        cmd
        for cmd in root.iter(f"{_NS}RunSynchronousCommand")
        if "uefi-boot-order.ps1" in (cmd.findtext(f"{_NS}Path") or "")
    ]


def _specialize(root: ET.Element) -> list:
    return [s for s in root.findall(f"{_NS}settings") if s.get("pass") == "specialize"]


def test_uefi_order_injected_into_factory_unattend():
    text = factory_seed_text(OsFamily.windows)
    out = inject_uefi_order_command(text, "http://192.168.100.250:8080", "12", "pxe")
    assert out.startswith('<?xml version="1.0" encoding="utf-8"?>\n')
    assert "ns0:" not in out
    assert out.count('wcm:action="add"') == text.count('wcm:action="add"') + 1
    root = ET.fromstring(out)
    specialize = _specialize(root)
    assert len(specialize) == 1
    deployment = [c for c in specialize[0] if c.get("name") == "Microsoft-Windows-Deployment"]
    assert len(deployment) == 1
    cmds = _order_commands(out)
    assert len(cmds) == 1
    assert cmds[0].findtext(f"{_NS}Order") == "2"
    path = cmds[0].findtext(f"{_NS}Path")
    assert path.endswith('pxe-bo.ps1 pxe 12 & exit /b 0"')
    assert "http://192.168.100.250:8080/boot-files/uefi-boot-order.ps1" in path
    assert len(path) <= 255
    assert inject_uefi_order_command(out, "http://192.168.100.250:8080", "12", "pxe") == out


def test_uefi_order_creates_specialize_pass_and_keeps_comments():
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<unattend xmlns="urn:schemas-microsoft-com:unattend">\n'
        "  <!-- operator note -->\n"
        '  <settings pass="oobeSystem">\n'
        '    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="arm64" '
        'publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
        "      <TimeZone>UTC</TimeZone>\n"
        "    </component>\n"
        "  </settings>\n"
        "</unattend>\n"
    )
    out = inject_uefi_order_command(text, "http://pxe.test:8080", "5", "disk")
    assert "<!-- operator note -->" in out
    assert "ns0:" not in out
    assert 'wcm:action="add"' in out
    assert 'xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"' in out
    root = ET.fromstring(out)
    specialize = _specialize(root)
    assert len(specialize) == 1
    component = specialize[0].find(f"{_NS}component")
    assert component.get("name") == "Microsoft-Windows-Deployment"
    assert component.get("processorArchitecture") == "arm64"
    cmds = _order_commands(out)
    assert len(cmds) == 1
    assert cmds[0].findtext(f"{_NS}Order") == "1"
    assert "pxe-bo.ps1 disk 5 & exit /b 0" in cmds[0].findtext(f"{_NS}Path")


def test_uefi_order_skips_invalid_inputs():
    text = factory_seed_text(OsFamily.windows)
    assert inject_uefi_order_command(text, "http://pxe.test:8080", "12", "") == text
    assert inject_uefi_order_command(text, "http://pxe.test:8080", "", "pxe") == text
    assert inject_uefi_order_command(text, "http://pxe.test:8080", "1 & calc", "pxe") == text
    assert inject_uefi_order_command(text, "http://pxe.test:8080/a b&c", "12", "pxe") == text
    assert inject_uefi_order_command(text, "http://" + "a" * 200, "12", "pxe") == text
    assert inject_uefi_order_command("<unattend", "http://pxe.test:8080", "12", "pxe") == "<unattend"


def test_windows_unattend_includes_uefi_order(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import upsert_local_account

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:24", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "WINBOX3"
        machine.next_boot_device = "disk"
        image = create_image(
            db, name="ws-order", os_family=OsFamily.windows, boot_wim_path="windows/boot.wim", actor="admin"
        )
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
        legacy = render_unattend_legacy(db, machine)
    body = client.get(seed_url(client, mid, "windows", "unattend.xml")).text
    assert "ns0:" not in body
    assert len(_order_commands(body)) == 1
    assert f"pxe-bo.ps1 disk {mid} &amp; exit /b 0" in body
    assert "http://pxe.test:8080/boot-files/uefi-boot-order.ps1" in body
    assert "WinSecret!" in body
    assert "ns0:" not in legacy
    assert len(_order_commands(legacy)) == 1
    assert f"pxe-bo.ps1 disk {mid} &amp; exit /b 0" in legacy
