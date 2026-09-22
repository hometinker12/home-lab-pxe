"""Coverage for operator bugs, install logs, sessions, identity, and seed URLs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from itsdangerous import SignatureExpired
from tests.conftest import login, seed_url

from src.auth import SESSION_IDLE_TIMEOUT_SECONDS, create_session_cookie, verify_session_cookie
from src.inventory.service import redact_install_log
from src.seed_render import complete_linux_user_data
from src.web import reported_client_ip


def _linux(db, *, mac: str, name: str):
    from src.inventory.service import create_image, touch_machine
    from src.models import OsFamily

    machine = touch_machine(db, mac=mac, uuid=None, client_ip="10.0.0.8")
    image = create_image(
        db,
        name=name,
        os_family=OsFamily.linux,
        kernel_path="ubuntu/vmlinuz",
        initrd_path="ubuntu/initrd",
        actor="admin",
    )
    return machine, image


def test_reported_client_ip_prefers_direct_peer_and_rejects_junk(monkeypatch):
    assert reported_client_ip("192.168.1.9", "10.1.2.3") == "192.168.1.9"
    assert reported_client_ip("172.17.0.2", "10.1.2.3") == "10.1.2.3"
    assert reported_client_ip("172.17.0.2", None) == ""
    assert reported_client_ip("testclient", "10.1.2.3") == "10.1.2.3"
    assert reported_client_ip("testclient", "not-an-ip") == "testclient"
    assert reported_client_ip("127.0.0.1", "2001:db8::1") == "2001:db8::1"
    monkeypatch.setattr("src.web.in_container", lambda: True)
    assert reported_client_ip("172.22.0.1", "192.168.4.245") == "192.168.4.245"
    assert reported_client_ip("172.22.0.1", None) == ""
    monkeypatch.setattr("src.web.in_container", lambda: False)
    assert reported_client_ip("172.22.0.1", "192.168.4.245") == "172.22.0.1"


def test_ipxe_query_ip_is_stored_when_peer_is_not_a_lan_address(client):
    body = client.get("/ipxe/02-00-00-00-00-91?ip=10.9.8.7&manufacturer=Dell&product=OptiPlex&serial=ABC-1").text
    assert "Continuing to next boot device" in body
    login(client)
    row = client.get("/api/machines").json()[0]
    assert row["last_ip"] == "10.9.8.7"
    page = client.get(f"/machines/{row['id']}")
    assert "Dell" in page.text
    assert "OptiPlex" in page.text
    assert "ABC-1" in page.text
    junk = client.get("/ipxe/02-00-00-00-00-91?ip=not-an-ip")
    assert junk.status_code == 200
    again = client.get(f"/api/machines/{row['id']}").json()
    assert again["last_ip"] != "not-an-ip"


def test_save_persists_password_and_deploy_requires_one(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:92", name="need-pass")
        db.commit()
        mid, image_id = int(machine.id), int(image.id)
    refused = client.post(
        f"/machines/{mid}/deploy",
        data={"image_id": str(image_id), "hostname": "nopass"},
        follow_redirects=False,
    )
    assert refused.status_code == 200
    assert "password" in refused.text.lower()
    assert client.get(f"/api/machines/{mid}").json()["state"] == "pending"
    saved = client.post(
        f"/machines/{mid}/save",
        data={"hostname": "nopass", "username": "root", "password": "saved-secret", "timezone": "UTC"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    assert client.get(f"/api/machines/{mid}").json()["account_password_set"] is True
    deployed = client.post(
        f"/machines/{mid}/deploy",
        data={"image_id": str(image_id), "hostname": "nopass", "timezone": "UTC"},
        follow_redirects=False,
    )
    assert deployed.status_code in {302, 303}
    assert client.get(f"/api/machines/{mid}").json()["state"] == "deploying"


def test_settings_account_username_without_password_is_shown(client):
    login(client)
    response = client.post(
        "/settings/accounts",
        data={"linux_username": "notroot", "linux_password": "", "windows_username": "Administrator"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Password is required" in response.text
    assert 'data-section="accounts"' in response.text
    assert "open" in response.text


def test_deploy_uses_settings_imaging_default(client):
    login(client)
    from src.db import session_scope

    saved = client.post(
        "/settings/accounts",
        data={
            "linux_username": "root",
            "linux_password": "lab-default-secret",
            "windows_username": "Administrator",
            "windows_password": "lab-win-secret",
        },
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:93", name="lab-default")
        db.commit()
        mid, image_id = int(machine.id), int(image.id)
    deployed = client.post(
        f"/machines/{mid}/deploy",
        data={"image_id": str(image_id), "hostname": "from-lab", "timezone": "UTC"},
        follow_redirects=False,
    )
    assert deployed.status_code in {302, 303}


def test_blocked_assigned_image_stays_submittable(client):
    login(client)
    from src.db import session_scope
    from src.models import ExtractStatus

    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:94", name="blocked-img")
        image.extract_status = ExtractStatus.failed.value
        machine.assigned_image_id = image.id
        db.add(image)
        db.add(machine)
        db.commit()
        mid, image_id = int(machine.id), int(image.id)
    page = client.get(f"/machines/{mid}")
    selected = next(line for line in page.text.splitlines() if "selected" in line and f'value="{image_id}"' in line)
    assert "disabled" not in selected
    posted = client.post(
        f"/machines/{mid}/deploy",
        data={"image_id": str(image_id), "hostname": "blocked", "timezone": "UTC", "password": "x"},
        follow_redirects=False,
    )
    assert posted.status_code == 200
    assert posted.status_code != 422
    assert "not ready" in posted.text.lower()


def test_vault_error_is_a_console_message_and_guest_init_404(client):
    login(client)
    from sqlmodel import select

    from src.db import session_scope
    from src.inventory.service import deploy_machine, upsert_local_account
    from src.models import AccountKind, LocalAccount

    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:95", name="bad-key")
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="real-secret",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        row = db.exec(select(LocalAccount).where(LocalAccount.machine_id == machine.id)).first()
        assert row is not None
        row.encrypted_password = "not-a-fernet-token"
        db.add(row)
        db.commit()
        mid = int(machine.id)
    page = client.get(f"/machines/{mid}")
    assert page.status_code == 200
    assert "encryption key does not match stored accounts" in page.text
    assert client.get(seed_url(client, mid, "cloud-init", "user-data")).status_code == 404
    assert client.get(f"/cloud-init/{mid}/user-data").status_code == 404


def test_install_log_is_capped_redacted_and_only_while_installing(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import deploy_machine

    raw = ("plain\n" * 100) + ("y" * 70000) + "\npassword: hunter2\n"
    redacted = redact_install_log(raw)
    assert "hunter2" not in redacted
    assert "[redacted]" in redacted
    assert len(redacted.encode()) <= 65536
    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:96", name="fail-log")
        db.commit()
        pending_id = int(machine.id)
        image_id = int(image.id)
    pending = client.post(f"/api/machines/{pending_id}/install-log", content=b"boom")
    assert pending.status_code == 409
    with session_scope() as db:
        from src.inventory.service import get_image, get_machine

        machine = get_machine(db, pending_id)
        image = get_image(db, image_id)
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
    posted = client.post(
        f"/api/machines/{pending_id}/install-log",
        content=b"ssh_authorized_keys: abc\nsubiquity crashed\n",
    )
    assert posted.status_code == 200
    assert posted.json()["state"] == "failed"
    detail = client.get(f"/machines/{pending_id}")
    assert ">Install failed<" in detail.text
    log = detail.text.split('<pre class="install-log">', 1)[1].split("</pre>", 1)[0]
    assert "subiquity crashed" in log
    assert "ssh_authorized_keys" not in log
    assert "abc" not in log
    menu = client.get("/ipxe/02-00-00-00-00-96").text
    assert "menu " in menu
    assert client.get(f"/cloud-init/{pending_id}/user-data").status_code == 404
    machines = client.get("/machines")
    assert "install failed" in machines.text.lower()


def test_error_commands_are_injected_into_linux_user_data():
    rendered = complete_linux_user_data(
        "autoinstall:\n  version: 1\n",
        install_log_url="http://pxe.test/api/machines/4/install-log",
    )
    assert "error-commands" in rendered
    assert "http://pxe.test/api/machines/4/install-log" in rendered


def test_seed_fetch_refreshes_imaging_clock(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import deploy_machine, get_machine

    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:97", name="heartbeat")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = int(machine.id)
    assert client.post(f"/api/machines/{mid}/events?event=imaging").json()["state"] == "imaging"
    user_data = seed_url(client, mid, "cloud-init", "user-data")
    with session_scope() as db:
        machine = get_machine(db, mid)
        machine.imaging_started_at = datetime.now(UTC) - timedelta(minutes=90)
        db.add(machine)
        db.commit()
    assert client.get(user_data).status_code == 200
    assert client.get(f"/api/machines/{mid}").json()["state"] == "imaging"


def test_session_idle_is_six_hours_and_reissue_resets_it(client, monkeypatch):
    response = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert "Max-Age=21600" in response.headers.get("set-cookie", "")
    page = client.get("/machines")
    assert page.status_code == 200
    assert "Max-Age=21600" in page.headers.get("set-cookie", "")
    start = time.time()
    token = create_session_cookie("admin", 0)
    monkeypatch.setattr(time, "time", lambda: start + SESSION_IDLE_TIMEOUT_SECONDS + 5)
    with pytest.raises(SignatureExpired):
        verify_session_cookie(token)
    issued = start + 3600
    monkeypatch.setattr(time, "time", lambda: issued)
    fresh = create_session_cookie("admin", 0)
    assert verify_session_cookie(fresh) == ("admin", 0)
    monkeypatch.setattr(time, "time", lambda: issued + SESSION_IDLE_TIMEOUT_SECONDS + 5)
    with pytest.raises(SignatureExpired):
        verify_session_cookie(fresh)


def test_uuid_collision_does_not_steal_a_live_machine(client):
    from src.db import session_scope
    from src.inventory.service import find_by_mac, touch_machine

    shared = "11111111-1111-1111-1111-111111111111"
    with session_scope() as db:
        first = touch_machine(db, mac="02:00:00:00:00:a1", uuid=shared, client_ip="10.0.0.1")
        db.commit()
        first_id = int(first.id)
        second = touch_machine(db, mac="02:00:00:00:00:a2", uuid=shared, client_ip="10.0.0.2")
        db.commit()
        assert int(second.id) != first_id
        assert second.uuid is None
        kept = find_by_mac(db, "02:00:00:00:00:a1")
        assert kept is not None
        assert kept.uuid == shared
    login(client)
    activity = client.get("/activity?q=machine.uuid")
    assert "UUID matched a live machine" in activity.text


def test_quiet_nic_uuid_still_remaps_mac():
    from src.db import session_scope
    from src.inventory.service import touch_machine

    shared = "22222222-2222-2222-2222-222222222222"
    with session_scope() as db:
        first = touch_machine(db, mac="02:00:00:00:00:b1", uuid=shared, client_ip="10.0.0.1")
        first.last_seen_at = datetime.now(UTC) - timedelta(hours=2)
        db.add(first)
        db.commit()
        first_id = int(first.id)
        swapped = touch_machine(db, mac="02:00:00:00:00:b2", uuid=shared, client_ip="10.0.0.2")
        db.commit()
        assert int(swapped.id) == first_id
        assert swapped.mac == "02:00:00:00:00:b2"


def test_sqlite_wal_and_unique_local_account(client):
    from sqlalchemy import text
    from sqlmodel import select

    from src.db import get_engine, session_scope
    from src.inventory.service import touch_machine, upsert_local_account
    from src.models import AccountKind, LocalAccount

    with get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    with session_scope() as db:
        machine = touch_machine(db, mac="02:00:00:00:00:c1", uuid=None, client_ip="10.0.0.3")
        db.commit()
        upsert_local_account(
            db, machine_id=int(machine.id), kind=AccountKind.linux_root, username="root", password="one"
        )
        upsert_local_account(
            db, machine_id=int(machine.id), kind=AccountKind.linux_root, username="root", password="two"
        )
        db.commit()
        rows = db.exec(select(LocalAccount).where(LocalAccount.machine_id == machine.id)).all()
        assert len(rows) == 1


def test_console_password_change_signs_out(client):
    login(client)
    changed = client.post(
        "/settings/password",
        data={"current_password": "secret", "new_password": "new-secret", "confirm_password": "new-secret"},
        follow_redirects=False,
    )
    assert changed.status_code in {302, 303}
    assert changed.headers["location"] == "/login"
    page = client.get("/machines", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code in {302, 303}
    assert page.headers["location"] == "/login"
    login_again = client.post("/login", data={"username": "admin", "password": "new-secret"}, follow_redirects=False)
    assert login_again.status_code in {302, 303}


def test_abort_closes_a_deploy(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import deploy_machine, upsert_local_account
    from src.models import AccountKind

    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:d1", name="abort-me")
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="abort-secret",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        mid = int(machine.id)
    aborted = client.post(f"/machines/{mid}/abort", follow_redirects=False)
    assert aborted.status_code in {302, 303}
    assert client.get(f"/api/machines/{mid}").json()["state"] == "timeout_error"
    assert client.get(seed_url(client, mid, "cloud-init", "user-data")).status_code == 404


def test_pending_banner_ignores_ready_hosts(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import mark_ready, touch_machine

    with session_scope() as db:
        pending = touch_machine(db, mac="02:00:00:00:00:e1", uuid=None, client_ip="10.0.0.1")
        ready = touch_machine(db, mac="02:00:00:00:00:e2", uuid=None, client_ip="10.0.0.2")
        mark_ready(db, ready, hostname="ready-box", actor="admin")
        db.commit()
        assert pending.state == "pending"
    page = client.get("/machines")
    assert "1 new or unnamed" in page.text


def test_staged_badge_and_insecure_banner(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import deploy_machine, mark_deployed, stage_machine, upsert_local_account
    from src.models import AccountKind

    with session_scope() as db:
        machine, image = _linux(db, mac="02:00:00:00:00:e3", name="stage-me")
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=AccountKind.linux_root,
            username="root",
            password="stage-secret",
        )
        deploy_machine(db, machine, image=image, actor="admin")
        mark_deployed(db, machine, actor="admin")
        stage_machine(db, machine, actor="admin", image=image)
        db.commit()
        mid = int(machine.id)
    page = client.get(f"/machines/{mid}")
    assert ">Staged<" in page.text
    assert "Insecure defaults are on" in page.text


def test_dockerfile_version_matches_version_file():
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG VERSION={version}" in dockerfile


def test_machines_and_images_pages_have_filters(client):
    login(client)
    machines = client.get("/machines")
    assert 'id="machine-state-filter"' in machines.text
    assert '<option value="pending">Pending</option>' in machines.text
    assert "No machines yet" in machines.text or "data-search" in machines.text
    images = client.get("/images")
    assert 'id="image-os-filter"' in images.text
    assert 'id="image-extract-filter"' in images.text
