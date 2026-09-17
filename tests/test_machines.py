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


def test_deployed_hostname_save_persists(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import mark_deployed, register_machine

    with session_scope() as db:
        image = create_image(
            db,
            name="host-linux",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:61", hostname="", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        mark_deployed(db, machine, actor="admin")
        db.commit()
        mid = int(machine.id)
    saved = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "named-host", "timezone": "UTC"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    row = client.get(f"/api/machines/{mid}").json()
    assert row["hostname"] == "named-host"
    assert row["state"] == "staged"
    page = client.get(f"/machines/{mid}")
    assert "named-host" in page.text
    assert 'form="machine-save"' in page.text
    assert f"/machines/{mid}/delete" in page.text


def test_deployed_hostname_save_without_image(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import mark_deployed, register_machine

    with session_scope() as db:
        machine = register_machine(db, mac="02:00:00:00:00:62", hostname="", actor="admin")
        mark_deployed(db, machine, actor="admin")
        db.commit()
        mid = int(machine.id)
    saved = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "label-only", "timezone": "America/New_York"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    row = client.get(f"/api/machines/{mid}").json()
    assert row["hostname"] == "label-only"
    assert row["state"] == "deployed"
    page = client.get(f"/machines/{mid}")
    assert "label-only" in page.text
    assert "America/New_York" in page.text
    listing = client.get("/machines")
    assert "label-only" in listing.text
    assert f"/machines/{mid}/delete" in listing.text


def test_delete_machine_removes_row_seeds_and_account(client, tmp_path):
    login(client)
    from src.db import session_scope
    from src.inventory.service import register_machine, upsert_local_account
    from src.models import AccountKind, LocalAccount, OsFamily
    from src.seed_store import write_machine_seed

    with session_scope() as db:
        machine = register_machine(db, mac="02:00:00:00:00:63", hostname="gone", actor="admin")
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="delete-secret",
        )
        db.commit()
        mid = int(machine.id)
    write_machine_seed(mid, OsFamily.linux, "#cloud-config\nhostname: {{hostname}}\n")
    seed_dir = tmp_path / "seeds" / str(mid)
    assert (seed_dir / "user-data").is_file()
    deleted = client.post(f"/machines/{mid}/delete", follow_redirects=False)
    assert deleted.status_code in {302, 303}
    assert deleted.headers["location"] == "/machines"
    assert client.get(f"/api/machines/{mid}").status_code == 404
    listing = client.get("/api/machines").json()
    assert all(row["id"] != mid for row in listing)
    assert not seed_dir.exists()
    with session_scope() as db:
        accounts = db.exec(select(LocalAccount).where(LocalAccount.machine_id == mid)).all()
        assert accounts == []
    page = client.get("/machines")
    assert "gone" not in page.text
