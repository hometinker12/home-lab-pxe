import yaml
from tests.conftest import login

from src.inventory.service import (
    LAB_DEFAULT_MACHINE_ID,
    create_image,
    deploy_machine,
    touch_machine,
    upsert_local_account,
)
from src.models import AccountKind, OsFamily
from src.seed_store import factory_seed_text, read_image_seed, write_machine_seed


def test_cloud_init_injects_root_and_bumps_instance_id(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:11", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "web1"
        image = create_image(
            db,
            name="u24",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="root-secret",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
        first_id = machine.instance_id
        image_id = int(image.id)
    user_data = client.get(f"/cloud-init/{mid}/user-data").text
    meta = client.get(f"/cloud-init/{mid}/meta-data").text
    parsed = yaml.safe_load(user_data)
    assert parsed["autoinstall"]["identity"]["username"] == "ubuntu"
    assert parsed["autoinstall"]["user-data"]["chpasswd"]["users"][0]["name"] == "root"
    assert "web1" in user_data
    assert "root" in user_data
    assert "root-secret" not in user_data
    assert "$6$" in user_data
    assert "storage:" in user_data
    assert "locale:" in user_data
    assert f"/api/machines/{mid}/events" in user_data
    assert first_id in meta
    disk = read_image_seed(image_id, OsFamily.linux)
    assert "{{password_hash}}" in disk
    assert "root-secret" not in disk
    assert client.get(f"/cloud-init/{mid}/vendor-data").status_code == 200
    detail = client.get(f"/api/machines/{mid}").json()
    assert "root-secret" not in str(detail)


def test_cloud_init_uses_image_source_id(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:16", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "src1"
        image = create_image(
            db,
            name="u24-source",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        image.source_id = "ubuntu-server-minimal"
        db.add(image)
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="root-secret",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    parsed = yaml.safe_load(client.get(f"/cloud-init/{mid}/user-data").text)
    assert parsed["autoinstall"]["source"]["id"] == "ubuntu-server-minimal"
    assert parsed["autoinstall"]["source"]["search_drivers"] is False


def test_machine_seed_override_replaces_image(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:15", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "web2"
        image = create_image(
            db,
            name="u24-override",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="root-secret",
        )
        write_machine_seed(
            int(machine.id),
            OsFamily.linux,
            "#cloud-config\n# machine-override-token\nhostname: {{hostname}}\n",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    user_data = client.get(f"/cloud-init/{mid}/user-data").text
    assert "machine-override-token" in user_data
    assert "autoinstall" not in user_data
    assert "autoinstall:" in factory_seed_text(OsFamily.linux)


def test_guest_init_hidden_until_deploy_and_after_phone_home(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        upsert_local_account(
            db,
            machine_id=LAB_DEFAULT_MACHINE_ID,
            kind=AccountKind.linux_root,
            username="root",
            password="lab-default-secret",
        )
        machine = touch_machine(db, mac="02:00:00:00:00:13", uuid=None, client_ip="10.0.0.10")
        db.commit()
        mid = machine.id
    pending = client.get(f"/cloud-init/{mid}/user-data")
    assert pending.status_code == 404
    assert "lab-default-secret" not in pending.text
    assert client.get(f"/windows/{mid}/unattend.xml").status_code == 404

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:13", uuid=None, client_ip="10.0.0.10")
        image = create_image(
            db,
            name="u24-gate",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
    installing = client.get(f"/cloud-init/{mid}/user-data")
    assert installing.status_code == 200
    assert "lab-default-secret" not in installing.text
    assert "root" in installing.text
    assert "$6$" in installing.text
    assert "event=imaging" in installing.text

    imaging = client.post(f"/api/machines/{mid}/events?event=imaging")
    assert imaging.status_code == 200
    assert imaging.json()["state"] == "imaging"
    still = client.get(f"/cloud-init/{mid}/user-data")
    assert still.status_code == 200
    assert "event=imaging" in still.text

    phone = client.post(f"/api/machines/{mid}/events", json={"event": "deployed"})
    assert phone.status_code == 200
    closed = client.get(f"/cloud-init/{mid}/user-data")
    assert closed.status_code == 404
    assert "lab-default-secret" not in closed.text


def test_phone_home_marks_deployed(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import create_image, deploy_machine, touch_machine
    from src.models import OsFamily

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:12", uuid=None, client_ip="10.0.0.9")
        image = create_image(
            db,
            name="u24b",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    response = client.post(f"/api/machines/{mid}/events", json={"event": "deployed"})
    assert response.status_code == 200
    assert response.json()["state"] == "deployed"
    ipxe = client.get("/ipxe/02-00-00-00-00-12").text
    assert "exit" in ipxe
