from sqlmodel import select
from tests.conftest import login

from src.inventory.service import create_image, deploy_machine
from src.models import OsFamily, StagedJob


def test_add_machine_by_mac(client):
    login(client)
    created = client.post(
        "/machines",
        data={"mac": "DE-AD-BE-EF-10-01", "hostname": "lab-node"},
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    assert created.headers["location"].startswith("/machines/")
    listing = client.get("/api/machines").json()
    assert listing[0]["mac"] == "de:ad:be:ef:10:01"
    assert listing[0]["hostname"] == "lab-node"
    assert listing[0]["state"] == "pending"
    page = client.get("/machines")
    assert "Add machine" in page.text
    assert "lab-node" in page.text


def test_add_machine_rejects_duplicate_and_invalid_mac(client):
    login(client)
    first = client.post(
        "/machines",
        data={"mac": "02:00:00:00:00:aa"},
        follow_redirects=False,
    )
    assert first.status_code in {302, 303}
    duplicate = client.post("/machines", data={"mac": "02:00:00:00:00:aa"})
    assert duplicate.status_code == 200
    assert "already exists" in duplicate.text
    invalid = client.post("/machines", data={"mac": "not-a-mac"})
    assert invalid.status_code == 200
    assert "12 hex digits" in invalid.text


def test_deployed_seed_edit_stages_once(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import mark_deployed, register_machine

    with session_scope() as db:
        image = create_image(
            db,
            name="stage-linux",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:51", hostname="n1", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        mark_deployed(db, machine, actor="admin")
        db.commit()
        mid = int(machine.id)
    body = "#cloud-config\n# staged-token\nhostname: {{hostname}}\n"
    saved = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "n1", "timezone": "UTC", "user_data": body},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    from src.db import session_scope as scope2

    with scope2() as db:
        jobs = db.exec(select(StagedJob)).all()
        assert len(jobs) == 1
    detail = client.get(f"/machines/{mid}")
    assert "staged" in detail.text
    saved_again = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "n1", "timezone": "UTC", "user_data": body + "# again\n"},
        follow_redirects=False,
    )
    assert saved_again.status_code in {302, 303}
    with scope2() as db:
        jobs = db.exec(select(StagedJob)).all()
        assert len(jobs) == 1


def test_deploying_edit_rejected(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import register_machine

    with session_scope() as db:
        image = create_image(
            db,
            name="d-linux",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:52", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = int(machine.id)
    response = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "nope", "user_data": "#cloud-config\nhostname: {{hostname}}\n"},
    )
    assert response.status_code == 200
    assert "deploy is in progress" in response.text


def test_image_seed_edit_does_not_autostage(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import mark_deployed, register_machine
    from src.seed_store import factory_seed_text

    with session_scope() as db:
        image = create_image(
            db,
            name="no-auto",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:53", hostname="n2", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        mark_deployed(db, machine, actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    client.post(
        f"/images/{image_id}",
        data={
            "name": "no-auto",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
            "user_data": factory_seed_text(OsFamily.linux).replace("#cloud-config", "#cloud-config\n# edited-image\n"),
        },
        follow_redirects=False,
    )
    page = client.get(f"/machines/{mid}")
    assert "deployed" in page.text
    from src.db import session_scope as scope2

    with scope2() as db:
        jobs = [j for j in db.exec(select(StagedJob)).all() if j.applied_at is None]
        assert jobs == []
