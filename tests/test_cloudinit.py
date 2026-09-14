from tests.conftest import login

from src.inventory.service import create_image, deploy_machine, touch_machine, upsert_local_account
from src.models import AccountKind, OsFamily


def test_cloud_init_injects_root_and_bumps_instance_id(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:11", uuid=None, client_ip="10.0.0.8")
        machine.hostname = "web1"
        image = create_image(db, name="u24", os_family=OsFamily.linux, actor="admin")
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
    user_data = client.get(f"/cloud-init/{mid}/user-data").text
    meta = client.get(f"/cloud-init/{mid}/meta-data").text
    assert "hostname: web1" in user_data
    assert "root:root-secret" in user_data
    assert f"url: http://pxe.test:8080/api/machines/{mid}/events" in user_data
    assert first_id in meta
    assert client.get(f"/cloud-init/{mid}/vendor-data").status_code == 200
    detail = client.get(f"/api/machines/{mid}").json()
    assert "root-secret" not in str(detail)


def test_phone_home_marks_deployed(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import create_image, deploy_machine, touch_machine
    from src.models import OsFamily

    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:12", uuid=None, client_ip="10.0.0.9")
        image = create_image(db, name="u24b", os_family=OsFamily.linux, actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = machine.id
    response = client.post(f"/api/machines/{mid}/events", json={"event": "deployed"})
    assert response.status_code == 200
    assert response.json()["state"] == "deployed"
    ipxe = client.get("/ipxe/02-00-00-00-00-12").text
    assert "exit" in ipxe
