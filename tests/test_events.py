from tests.conftest import login

from src.inventory.service import create_image, deploy_machine, touch_machine
from src.models import OsFamily, state_label


def test_phone_home_rejected_when_pending(client):
    client.get("/ipxe/02-00-00-00-00-31")
    login(client)
    mid = client.get("/api/machines").json()[0]["id"]
    response = client.post(f"/api/machines/{mid}/events", json={"event": "deployed"})
    assert response.status_code == 409


def test_phone_home_form_post_marks_deployed(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:32", uuid=None, client_ip="10.0.0.4")
        image = create_image(
            db,
            name="u-form",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    response = client.post(
        f"/api/machines/{mid}/events",
        content=b"instance_id=abc",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "deployed"


def test_imaging_query_event_then_phone_home(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:34", uuid=None, client_ip="10.0.0.6")
        image = create_image(
            db,
            name="u-imaging",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    first = client.post(f"/api/machines/{mid}/events?event=imaging")
    assert first.status_code == 200
    assert first.json()["state"] == "imaging"
    page = client.get(f"/machines/{mid}")
    assert ">Imaging<" in page.text
    again = client.post(f"/api/machines/{mid}/events?event=imaging")
    assert again.status_code == 200
    assert again.json()["state"] == "imaging"
    done = client.post(f"/api/machines/{mid}/events", json={"event": "phone_home"})
    assert done.status_code == 200
    assert done.json()["state"] == "deployed"
    assert client.get(f"/api/machines/{mid}").json()["state"] == "deployed"


def test_imaging_rejected_when_pending(client):
    client.get("/ipxe/02-00-00-00-00-35")
    login(client)
    mid = client.get("/api/machines").json()[0]["id"]
    response = client.post(f"/api/machines/{mid}/events?event=imaging")
    assert response.status_code == 409


def test_unsupported_event_is_rejected(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:33", uuid=None, client_ip="10.0.0.5")
        image = create_image(
            db,
            name="u-bad-event",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    response = client.post(f"/api/machines/{mid}/events", json={"event": "wipe-all"})
    assert response.status_code == 400


def test_state_labels():
    assert state_label("deploying") == "Deploying"
    assert state_label("imaging") == "Imaging"
    assert state_label("deployed") == "Deployed"
    assert state_label("disabled") == "Disabled"
    assert state_label("staged") == "Deploying"
    assert state_label("timeout_error") == "Timeout Error"


def test_imaging_timeout_moves_to_timeout_error(client):
    login(client)
    from datetime import UTC, datetime, timedelta

    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:36", uuid=None, client_ip="10.0.0.8")
        image = create_image(
            db,
            name="u-timeout",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    assert client.post(f"/api/machines/{mid}/events?event=imaging").json()["state"] == "imaging"
    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:36", uuid=None, client_ip="10.0.0.8")
        machine.imaging_started_at = datetime.now(UTC) - timedelta(minutes=16)
        db.add(machine)
        db.commit()
    page = client.get("/machines")
    assert ">Timeout Error<" in page.text
    assert "imaging timeout" in page.text.lower()
    api = client.get(f"/api/machines/{mid}").json()
    assert api["state"] == "timeout_error"
    ipxe = client.get("/ipxe/02-00-00-00-00-36")
    assert "Waiting for operator" in ipxe.text
    assert "kernel" not in ipxe.text
    refused = client.post(f"/api/machines/{mid}/events", json={"event": "phone_home"})
    assert refused.status_code == 409
    assert client.get(f"/cloud-init/{mid}/user-data").status_code == 404
    detail = client.get(f"/machines/{mid}")
    assert "Imaging timed out" in detail.text


def test_imaging_timeout_zero_disables_timer(client):
    login(client)
    from datetime import UTC, datetime, timedelta

    from src.db import session_scope

    saved = client.post(
        "/settings/machines",
        data={"imaging_timeout_minutes": "0"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:37", uuid=None, client_ip="10.0.0.9")
        image = create_image(
            db,
            name="u-timeout-off",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    assert client.post(f"/api/machines/{mid}/events?event=imaging").json()["state"] == "imaging"
    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:37", uuid=None, client_ip="10.0.0.9")
        machine.imaging_started_at = datetime.now(UTC) - timedelta(minutes=60)
        db.add(machine)
        db.commit()
    assert client.get(f"/api/machines/{mid}").json()["state"] == "imaging"
    restore = client.post(
        "/settings/machines",
        data={"imaging_timeout_minutes": "15"},
        follow_redirects=False,
    )
    assert restore.status_code in {302, 303}


def test_imaging_event_does_not_reset_timeout_clock(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:38", uuid=None, client_ip="10.0.0.10")
        image = create_image(
            db,
            name="u-clock",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    client.post(f"/api/machines/{mid}/events?event=imaging")
    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:38", uuid=None, client_ip="10.0.0.10")
        started = machine.imaging_started_at
        assert started is not None
        started_naive = started.replace(tzinfo=None)
    client.post(f"/api/machines/{mid}/events?event=imaging")
    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:38", uuid=None, client_ip="10.0.0.10")
        again = machine.imaging_started_at
        assert again is not None
        assert again.replace(tzinfo=None) == started_naive
