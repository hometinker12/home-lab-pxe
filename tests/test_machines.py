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
    assert 'data-open-dialog="add-machine"' in page.text
    assert 'id="add-machine"' in page.text
    assert '<section class="card">' not in page.text
    assert 'href="/machines"' in page.text
    assert ">Refresh<" in page.text
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
    assert 'id="add-machine" open' in duplicate.text
    assert "02:00:00:00:00:aa" in duplicate.text
    invalid = client.post("/machines", data={"mac": "not-a-mac"})
    assert invalid.status_code == 200
    assert "12 hex digits" in invalid.text
    assert 'id="add-machine" open' in invalid.text
    assert "not-a-mac" in invalid.text


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
    assert 'id="machine-save"' in page.text
    assert 'name="timezone"' in page.text
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
    assert 'value="America/New_York" selected' in page.text
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


def test_deploy_copies_blank_machine_seed_from_image(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import register_machine
    from src.seed_store import read_machine_seed, write_image_seed

    with session_scope() as db:
        image = create_image(
            db,
            name="copy-on-deploy",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:71", hostname="n-copy", actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    write_image_seed(
        image_id,
        OsFamily.linux,
        "#cloud-config\n# image-default-token\nhostname: {{hostname}}\n",
    )
    page = client.get(f"/machines/{mid}")
    assert "Copy Default" in page.text
    assert 'formaction="/machines/' in page.text
    deployed = client.post(
        f"/machines/{mid}/deploy",
        data={"image_id": str(image_id), "username": "root", "password": "copy-secret"},
        follow_redirects=False,
    )
    assert deployed.status_code in {302, 303}
    copied = read_machine_seed(mid, OsFamily.linux)
    assert "image-default-token" in copied
    detail = client.get(f"/machines/{mid}")
    assert "image-default-token" in detail.text
    assert "Copy Default" in detail.text


def test_deploy_keeps_existing_machine_seed(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import register_machine
    from src.seed_store import read_machine_seed, write_image_seed, write_machine_seed

    with session_scope() as db:
        image = create_image(
            db,
            name="keep-machine-seed",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:72", actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    write_image_seed(image_id, OsFamily.linux, "#cloud-config\n# image-should-not-win\nhostname: {{hostname}}\n")
    write_machine_seed(mid, OsFamily.linux, "#cloud-config\n# keep-machine-token\nhostname: {{hostname}}\n")
    client.post(
        f"/machines/{mid}/deploy",
        data={"image_id": str(image_id), "username": "root", "password": "keep-secret"},
        follow_redirects=False,
    )
    copied = read_machine_seed(mid, OsFamily.linux)
    assert "keep-machine-token" in copied
    assert "image-should-not-win" not in copied


def test_copy_default_overwrites_machine_seed(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import register_machine
    from src.seed_store import read_machine_seed, write_image_seed, write_machine_seed

    with session_scope() as db:
        image = create_image(
            db,
            name="copy-default",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:73", actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    write_machine_seed(mid, OsFamily.linux, "#cloud-config\n# stale-machine\nhostname: {{hostname}}\n")
    write_image_seed(image_id, OsFamily.linux, "#cloud-config\n# latest-default\nhostname: {{hostname}}\n")
    copied = client.post(
        f"/machines/{mid}/seed/copy-default",
        data={"image_id": str(image_id)},
        follow_redirects=False,
    )
    assert copied.status_code in {302, 303}
    body = read_machine_seed(mid, OsFamily.linux)
    assert "latest-default" in body
    assert "stale-machine" not in body
    detail = client.get(f"/machines/{mid}")
    assert "latest-default" in detail.text
    row = next(m for m in client.get("/api/machines").json() if m["id"] == mid)
    assert row["assigned_image_id"] == image_id
    assert row["state"] == "pending"


def test_deploy_saves_hostname_and_timezone(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import load_overlay, register_machine

    with session_scope() as db:
        image = create_image(
            db,
            name="deploy-saves",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:75", actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    deployed = client.post(
        f"/machines/{mid}/deploy",
        data={
            "image_id": str(image_id),
            "hostname": "from-deploy",
            "timezone": "America/Chicago",
            "packages": "qemu-guest-agent",
            "username": "root",
            "password": "deploy-secret",
        },
        follow_redirects=False,
    )
    assert deployed.status_code in {302, 303}
    row = client.get(f"/api/machines/{mid}").json()
    assert row["hostname"] == "from-deploy"
    assert row["state"] == "deploying"
    page = client.get(f"/machines/{mid}")
    assert "from-deploy" in page.text
    assert 'value="America/Chicago" selected' in page.text
    with session_scope() as db:
        from src.models import Machine

        saved = db.get(Machine, mid)
        overlay = load_overlay(saved.guest_overlay)
        assert overlay["timezone"] == "America/Chicago"
        assert overlay["packages"] == ["qemu-guest-agent"]


def test_new_machine_inherits_settings_timezone(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import load_overlay, touch_machine
    from src.models import Machine

    saved = client.post(
        "/settings/machines",
        data={"imaging_timeout_minutes": "15", "default_timezone": "America/Los_Angeles"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    settings_page = client.get("/settings")
    assert 'name="default_timezone"' in settings_page.text
    assert 'value="America/Los_Angeles" selected' in settings_page.text
    created = client.post(
        "/machines",
        data={"mac": "02:00:00:00:00:76", "hostname": "tz-node"},
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    mid = int(created.headers["location"].rsplit("/", 1)[-1])
    page = client.get(f"/machines/{mid}")
    assert "<select" in page.text
    assert 'name="timezone"' in page.text
    assert 'value="America/Los_Angeles" selected' in page.text
    rejected = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "tz-node", "timezone": "Not/AZone"},
    )
    assert rejected.status_code == 200
    assert "IANA timezone" in rejected.text
    with session_scope() as db:
        discovered = touch_machine(db, mac="02:00:00:00:00:77", uuid=None, client_ip="10.0.0.77")
        db.commit()
        overlay = load_overlay(db.get(Machine, int(discovered.id)).guest_overlay)
        assert overlay["timezone"] == "America/Los_Angeles"


def test_copy_default_rejected_while_deploying(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import register_machine

    with session_scope() as db:
        image = create_image(
            db,
            name="copy-locked",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:74", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = int(machine.id)
        image_id = int(image.id)
    response = client.post(f"/machines/{mid}/seed/copy-default", data={"image_id": str(image_id)})
    assert response.status_code == 200
    assert "deploy is in progress" in response.text
