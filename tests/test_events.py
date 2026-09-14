from tests.conftest import login

from src.inventory.service import create_image, deploy_machine, touch_machine
from src.models import OsFamily


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
        image = create_image(db, name="u-form", os_family=OsFamily.linux, actor="admin")
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


def test_unsupported_event_is_rejected(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:33", uuid=None, client_ip="10.0.0.5")
        image = create_image(db, name="u-bad-event", os_family=OsFamily.linux, actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    response = client.post(f"/api/machines/{mid}/events", json={"event": "wipe-all"})
    assert response.status_code == 400
