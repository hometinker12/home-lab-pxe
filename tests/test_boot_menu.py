from tests.conftest import login

from src.boot.policy import ScriptKind, decide_script
from src.inventory.boot_menu import (
    create_folder,
    delete_folder,
    get_folder,
    list_folders,
    parent_folder_options,
    rename_folder,
)
from src.inventory.service import create_image, deploy_machine, disable_machine, find_by_mac, mark_ready
from src.models import OsFamily


def test_seed_default_folders(client):
    login(client)
    page = client.get("/boot-menu")
    assert page.status_code == 200
    assert "Windows" in page.text
    assert "Linux" in page.text
    assert "Tools" in page.text
    assert "Unknown / disabled timeout" in page.text
    from src.db import session_scope

    with session_scope() as db:
        names = {row.name for row in list_folders(db)}
    assert names == {"Windows", "Linux", "Tools"}
    assert 'id="edit-folder"' in page.text
    assert 'data-open-dialog="edit-folder"' in page.text
    assert 'id="move-image"' in page.text
    assert ">Move up<" not in page.text
    assert ">Move down<" not in page.text
    assert "Move Windows up" in page.text
    assert "Move Windows down" in page.text
    assert "autofocus" not in page.text
    assert "pxe:boot-menu-scroll" in page.text
    assert 'id="folder-edit"' in page.text
    assert ">Root<" in page.text
    assert ">Top level<" not in page.text
    edit = page.text.split('id="folder-edit"', 1)[1].split("</form>", 1)[0]
    assert edit.index(">Root<") < edit.index(">Linux<")
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert b"pxe:boot-menu-scroll" in js.content
    assert b"showModalPreservingScroll" in js.content


def test_last_folder_cannot_be_deleted(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        folders = list_folders(db)
        linux = next(row for row in folders if row.name == "Linux")
        windows = next(row for row in folders if row.name == "Windows")
        tools = next(row for row in folders if row.name == "Tools")
        delete_folder(db, linux, actor="admin")
        delete_folder(db, windows, actor="admin")
        try:
            delete_folder(db, tools, actor="admin")
            raise AssertionError("last folder deleted")
        except ValueError as exc:
            assert "last remaining folder" in str(exc)
        db.commit()


def test_nested_folder_create_and_rename(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        linux = next(row for row in list_folders(db) if row.name == "Linux")
        child = create_folder(db, name="Ubuntu", parent_id=linux.id, actor="admin")
        rename_folder(db, child, name="Ubuntu LTS", actor="admin")
        db.commit()
        child_id = child.id
    page = client.get(f"/boot-menu?folder={child_id}")
    assert "Ubuntu LTS" in page.text
    created = client.post(
        "/boot-menu/folders", data={"name": "Rescue", "parent_id": str(child_id)}, follow_redirects=False
    )
    assert created.status_code in {302, 303}


def test_edit_folder_can_change_parent(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        linux = next(row for row in list_folders(db) if row.name == "Linux")
        windows = next(row for row in list_folders(db) if row.name == "Windows")
        child = create_folder(db, name="Ubuntu", parent_id=linux.id, actor="admin")
        db.commit()
        linux_id = linux.id
        windows_id = windows.id
        child_id = child.id
        options = parent_folder_options(db, linux)
        assert all(fid != linux_id for fid, _ in options)
        assert all(not path.endswith("/Ubuntu") for _, path in options)
    page = client.get(f"/boot-menu?folder={child_id}")
    edit = page.text.split('id="folder-edit"', 1)[1].split("</form>", 1)[0]
    assert 'name="parent_id"' in edit
    assert edit.index(">Root<") < edit.index(">Windows<")
    assert f'value="{linux_id}"' in edit
    assert f'value="{child_id}"' not in edit
    to_root = client.post(
        f"/boot-menu/folders/{child_id}",
        data={"name": "Ubuntu", "parent_id": ""},
        follow_redirects=False,
    )
    assert to_root.status_code in {302, 303}
    with session_scope() as db:
        row = get_folder(db, child_id)
        assert row is not None
        assert row.parent_id is None
        assert row.name == "Ubuntu"
    under_windows = client.post(
        f"/boot-menu/folders/{child_id}",
        data={"name": "Ubuntu LTS", "parent_id": str(windows_id)},
        follow_redirects=False,
    )
    assert under_windows.status_code in {302, 303}
    with session_scope() as db:
        row = get_folder(db, child_id)
        assert row is not None
        assert row.parent_id == windows_id
        assert row.name == "Ubuntu LTS"


def test_cannot_reparent_folder_into_self_or_child(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        linux = next(row for row in list_folders(db) if row.name == "Linux")
        child = create_folder(db, name="Ubuntu", parent_id=linux.id, actor="admin")
        db.commit()
        linux_id = linux.id
        child_id = child.id
    into_self = client.post(
        f"/boot-menu/folders/{linux_id}",
        data={"name": "Linux", "parent_id": str(linux_id)},
    )
    assert into_self.status_code == 200
    assert "cannot be moved into itself or a nested folder" in into_self.text
    assert 'id="edit-folder" open' in into_self.text
    into_child = client.post(
        f"/boot-menu/folders/{linux_id}",
        data={"name": "Linux", "parent_id": str(child_id)},
    )
    assert into_child.status_code == 200
    assert "cannot be moved into itself or a nested folder" in into_child.text
    clash = client.post(
        f"/boot-menu/folders/{child_id}",
        data={"name": "Windows", "parent_id": ""},
    )
    assert clash.status_code == 200
    assert "already exists" in clash.text
    with session_scope() as db:
        linux = get_folder(db, linux_id)
        child = get_folder(db, child_id)
        assert linux is not None and linux.parent_id is None
        assert child is not None and child.parent_id == linux_id


def test_image_requires_folder_assignment(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(db, name="foldered-linux", os_family=OsFamily.linux, actor="admin")
        db.commit()
        assert image.folder_id is not None
        folder_id = image.folder_id
    listing = client.get("/images")
    assert "Linux" in listing.text
    api = client.get("/api/images").json()
    row = next(img for img in api if img["name"] == "foldered-linux")
    assert row["folder_id"] == folder_id


def test_unknown_mac_continues_to_disk(client):
    response = client.get("/ipxe/02-00-00-00-00-21")
    body = response.text
    assert body.startswith("#!ipxe")
    assert "Continuing to next boot device" in body
    assert "exit" in body
    assert "\nmenu " not in body
    assert "choose" not in body
    assert "sleep 5" in body
    assert "password" not in body.lower()


def test_disabled_mac_matches_unknown(client):
    client.get("/ipxe/02-00-00-00-00-22")
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:22")
        assert machine is not None
        mark_ready(db, machine, hostname="lab-disabled", actor="admin")
        disable_machine(db, machine, actor="admin", disabled=True)
        db.commit()
        assert decide_script(db, machine) == ScriptKind.unknown_local
    body = client.get("/ipxe/02-00-00-00-00-22").text
    assert "Continuing to next boot device" in body
    assert "choose" not in body
    image = None
    from src.db import session_scope as scope

    with scope() as db:
        image = create_image(
            db,
            name="disabled-target",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        db.commit()
        image_id = image.id
    boot = client.get(f"/ipxe/02-00-00-00-00-22/boot/{image_id}")
    assert "kernel" not in boot.text
    assert "Continuing to next boot device" in boot.text


def test_named_machine_sees_folder_menu(client):
    client.get("/ipxe/02-00-00-00-00-23")
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:23")
        assert machine is not None
        mark_ready(db, machine, hostname="lab-ready", actor="admin")
        db.commit()
    body = client.get("/ipxe/02-00-00-00-00-23").text
    assert "menu " in body
    assert "choose" in body
    assert "Continue to next boot device" in body
    assert "item local " in body.split("item f")[0]
    assert "Windows" in body
    assert "Linux" in body
    assert "Tools" in body


def test_client_os_pick_deploys_ready_machine(client):
    client.get("/ipxe/02-00-00-00-00-24")
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="menu-ubuntu",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = find_by_mac(db, "02:00:00:00:00:24")
        assert machine is not None
        mark_ready(db, machine, hostname="lab-pick", actor="admin")
        db.commit()
        image_id = image.id
        machine_id = machine.id
    unknown = client.get("/ipxe/02-00-00-00-00-99/boot/" + str(image_id))
    assert "kernel" not in unknown.text
    body = client.get(f"/ipxe/02-00-00-00-00-24/boot/{image_id}").text
    assert "kernel" in body
    assert "autoinstall" in body
    assert "password" not in body.lower()
    from src.db import session_scope as scope

    with scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:24")
        assert machine is not None
        assert machine.state == "deploying"
        assert machine.assigned_image_id == image_id
        assert machine.id == machine_id


def test_unknown_cannot_boot_image(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="guarded",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        db.commit()
        image_id = image.id
    body = client.get(f"/ipxe/02-00-00-00-00-25/boot/{image_id}").text
    assert "kernel" not in body
    assert "Continuing to next boot device" in body
    from src.db import session_scope as scope

    with scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:25")
        assert machine is not None
        assert machine.state == "pending"


def test_tool_boot_does_not_change_state(client):
    client.get("/ipxe/02-00-00-00-00-26")
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="memtest",
            os_family=OsFamily.tool,
            kernel_path="tools/memtest",
            initrd_path="tools/initrd",
            actor="admin",
        )
        machine = find_by_mac(db, "02:00:00:00:00:26")
        assert machine is not None
        mark_ready(db, machine, hostname="lab-tools", actor="admin")
        db.commit()
        image_id = image.id
    body = client.get(f"/ipxe/02-00-00-00-00-26/boot/{image_id}").text
    assert "kernel" in body
    assert "autoinstall" not in body
    assert "cloud-init" not in body
    from src.db import session_scope as scope

    with scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:26")
        assert machine is not None
        assert machine.state == "ready"
        assert machine.assigned_image_id is None


def test_folder_with_images_cannot_be_deleted(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        linux = next(row for row in list_folders(db) if row.name == "Linux")
        image = create_image(db, name="folder-guard", os_family=OsFamily.linux, actor="admin")
        db.commit()
        linux_id = linux.id
        assert image.folder_id == linux_id
    page = client.get(f"/boot-menu?folder={linux_id}")
    assert "Move images out of this folder first" in page.text
    assert "Move folder" in page.text
    blocked = client.post(f"/boot-menu/folders/{linux_id}/delete")
    assert blocked.status_code == 200
    assert "Move images out of this folder first" in blocked.text
    assert 'id="edit-folder" open' in blocked.text
    with session_scope() as db:
        assert any(row.name == "Linux" for row in list_folders(db))


def test_move_image_to_another_folder(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import get_image

    with session_scope() as db:
        linux = next(row for row in list_folders(db) if row.name == "Linux")
        windows = next(row for row in list_folders(db) if row.name == "Windows")
        image = create_image(db, name="relocate-me", os_family=OsFamily.linux, actor="admin")
        child = create_folder(db, name="Ubuntu", parent_id=linux.id, actor="admin")
        db.commit()
        linux_id = linux.id
        windows_id = windows.id
        image_id = image.id
        assert image.folder_id == linux_id
        child_id = child.id
    menu = client.get("/boot-menu")
    assert "Linux/Ubuntu" in menu.text
    moved = client.post(
        f"/boot-menu/images/{image_id}/folder",
        data={"folder_id": str(windows_id)},
        follow_redirects=False,
    )
    assert moved.status_code in {302, 303}
    assert moved.headers["location"] == f"/boot-menu?folder={windows_id}"
    with session_scope() as db:
        image = get_image(db, image_id)
        assert image is not None
        assert image.folder_id == windows_id
    nested_page = client.get(f"/boot-menu?folder={child_id}")
    assert "Ubuntu" in nested_page.text


def test_console_cannot_deploy_tool_image(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="not-a-deploy",
            os_family=OsFamily.tool,
            kernel_path="tools/memtest",
            initrd_path="tools/initrd",
            actor="admin",
        )
        from src.inventory.service import register_machine

        machine = register_machine(db, mac="02:00:00:00:00:27", hostname="lab-tool-deploy", actor="admin")
        try:
            deploy_machine(db, machine, image=image, actor="admin")
            raise AssertionError("tool deploy allowed")
        except ValueError as exc:
            assert "PXE menu" in str(exc)
        db.commit()
