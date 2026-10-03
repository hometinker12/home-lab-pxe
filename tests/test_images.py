import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlmodel import select
from tests.conftest import login
from tests.test_install_sources import minimal_wim_bytes

from src.extract_worker import run_one_job
from src.models import ExtractStatus, MachineState


class _LinuxRunner:
    def list_entries(self, iso):
        return [("casper/vmlinuz", 4, False), ("casper/initrd", 4, False)]

    def extract_member_bytes(self, iso, member):
        return b"kern" if "vmlinuz" in member.replace("\\", "/") else b"ird"

    def extract_tree(self, iso, dest, prefixes=()):
        return None


class _LinuxNfsRunner:
    def list_entries(self, iso):
        return [
            ("casper/vmlinuz", 4, False),
            ("casper/initrd", 4, False),
            ("casper/filesystem.squashfs", 8, False),
            (".disk/casper-uuid-generic", 4, False),
        ]

    def extract_member_bytes(self, iso, member):
        return b"kern" if "vmlinuz" in member.replace("\\", "/") else b"ird"

    def extract_tree(self, iso, dest, prefixes=()):
        dest = Path(dest)
        casper = dest / "casper"
        disk = dest / ".disk"
        casper.mkdir(parents=True, exist_ok=True)
        disk.mkdir(parents=True, exist_ok=True)
        (casper / "vmlinuz").write_bytes(b"kern")
        (casper / "initrd").write_bytes(b"ird")
        (casper / "filesystem.squashfs").write_bytes(b"squashok")
        (casper / "install-sources.yaml").write_text(
            "sources:\n"
            "- id: ubuntu-server-minimal\n"
            "  name:\n"
            "    en: Ubuntu Server (minimized)\n"
            "- default: true\n"
            "  id: ubuntu-server\n"
            "  name:\n"
            "    en: Ubuntu Server\n",
            encoding="utf-8",
        )
        (disk / "casper-uuid-generic").write_bytes(b"uuid")
        release = dest / "dists" / "resolute" / "Release"
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_bytes(b"rel")
        deb = dest / "pool" / "main" / "a" / "hello.deb"
        deb.parent.mkdir(parents=True, exist_ok=True)
        deb.write_bytes(b"deb")


class _WindowsRunner:
    def list_entries(self, iso):
        return [
            ("setup.exe", 4, False),
            ("sources/boot.wim", 4, False),
            ("sources/install.wim", 4, False),
        ]

    def extract_member_bytes(self, iso, member):
        return b"wim"

    def extract_tree(self, iso, dest, prefixes=()):
        dest = Path(dest)
        (dest / "setup.exe").write_bytes(b"setup")
        sources = dest / "sources"
        sources.mkdir(parents=True, exist_ok=True)
        (sources / "boot.wim").write_bytes(b"boot")
        (sources / "install.wim").write_bytes(
            minimal_wim_bytes(
                [
                    (1, "Windows Server 2022 SERVERSTANDARDCORE", "Standard Core"),
                    (2, "Windows Server 2022 SERVERDATACENTER", "Datacenter"),
                ]
            )
        )


def _upload_iso(client, name="ubuntu-iso", os_family="linux", body=b"iso-bytes", **extra):
    response = client.post(
        "/images/isos",
        data={"name": name, "os_family": os_family, "arch": "x86_64", **extra},
        files={"iso_file": ("media.iso", body, "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    return next(row for row in client.get("/api/isos").json() if row["name"] == name)


def _add_template(client, name, iso_id, **extra):
    response = client.post("/images", data={"name": name, "iso_id": str(iso_id), **extra}, follow_redirects=False)
    assert response.status_code in {302, 303}
    return next(row for row in client.get("/api/images").json() if row["name"] == name)


def _iso(iso_id):
    from src.db import session_scope
    from src.models import Iso

    with session_scope() as db:
        row = db.get(Iso, iso_id)
        db.expunge(row)
        return row


def test_images_page_has_iso_and_template_sections(client):
    login(client)
    page = client.get("/images")
    assert page.status_code == 200
    assert 'role="tablist"' in page.text
    assert page.text.find('id="templates-tab"') < page.text.find('id="isos-tab"')
    assert page.text.find('id="templates-panel"') < page.text.find('id="isos-panel"')
    templates_tab = page.text[page.text.find('id="templates-tab"') : page.text.find('id="isos-tab"')]
    assert 'aria-selected="true"' in templates_tab
    assert 'class="count"' in templates_tab
    isos_tab = page.text[page.text.find('id="isos-tab"') : page.text.find("</a>", page.text.find('id="isos-tab"'))]
    assert 'aria-selected="false"' in isos_tab
    assert 'class="count"' in isos_tab
    assert 'data-tab-panel="isos" hidden' in page.text
    assert 'data-tab-panel="templates" hidden' not in page.text
    assert 'data-open-dialog="add-iso"' in page.text
    assert 'data-open-dialog="add-template"' in page.text
    assert 'id="add-iso"' in page.text
    assert 'id="add-template"' in page.text
    add_iso = page.text[page.text.find('id="add-iso"') : page.text.find('id="add-template"')]
    assert 'name="iso_file"' in add_iso
    assert 'name="cmdline"' in add_iso
    assert 'name="kernel_path"' in add_iso
    assert 'name="initrd_path"' in add_iso
    assert 'name="boot_wim_file"' in add_iso
    assert "Advanced Settings" in add_iso
    assert 'name="source_id"' not in add_iso
    add_template = page.text[page.text.find('id="add-template"') :]
    assert 'name="iso_id"' in add_template
    assert "No ISO (tool: kernel/initrd)" in add_template
    assert 'data-template-os="tool"' in add_template
    assert 'id="image-os-filter"' in page.text


def test_static_assets_carry_content_hash(client):
    login(client)
    page = client.get("/images")
    assert re.search(r'/static/app\.css\?v=[0-9a-f]{12}"', page.text)
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{12}"', page.text)


def test_images_page_isos_tab_query_selects_isos_panel(client):
    login(client)
    page = client.get("/images?tab=isos")
    assert page.status_code == 200
    isos_tab = page.text[page.text.find('id="isos-tab"') : page.text.find("</a>", page.text.find('id="isos-tab"'))]
    assert 'aria-selected="true"' in isos_tab
    assert 'data-tab-panel="templates" hidden' in page.text
    assert 'data-tab-panel="isos" hidden' not in page.text
    assert 'data-tab-action="templates" hidden' in page.text


def test_iso_with_kernel_paths_and_shared_template(client):
    login(client)
    created = client.post(
        "/images/isos",
        data={
            "name": "ubuntu-kernel",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
            "cmdline": "quiet",
        },
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    iso = client.get("/api/isos").json()[0]
    assert created.headers["location"] == f"/images/isos/{iso['id']}"
    assert iso["cmdline"] == "quiet"
    assert iso["extract_status"] == "idle"
    first = _add_template(client, "ubuntu-a", iso["id"])
    second = _add_template(client, "ubuntu-b", iso["id"])
    for row in (first, second):
        assert row["iso_id"] == iso["id"]
        assert row["iso_name"] == "ubuntu-kernel"
        assert row["kernel_path"] == "ubuntu/vmlinuz"
        assert row["cmdline"] == "quiet"
        assert row["os_family"] == "linux"
    detail = client.get(f"/images/{first['id']}")
    assert detail.status_code == 200
    assert 'name="iso_file"' not in detail.text
    assert 'name="kernel_path"' not in detail.text
    assert 'name="cmdline"' not in detail.text
    assert "data-template-media" in detail.text
    assert "ubuntu/vmlinuz" in detail.text
    assert "Install source" in detail.text
    assert 'name="user_data"' in detail.text
    assert "data-strip-autoinstall" in detail.text
    assert "Reset to default" in detail.text
    assert f'formaction="/images/{first["id"]}/seed/reset"' in detail.text
    iso_page = client.get(f"/images/isos/{iso['id']}")
    assert 'name="cmdline"' in iso_page.text
    assert 'name="kernel_path"' in iso_page.text
    assert "ubuntu-a" in iso_page.text and "ubuntu-b" in iso_page.text
    saved = client.post(
        f"/images/isos/{iso['id']}",
        data={
            "name": "ubuntu-kernel",
            "arch": "x86_64",
            "iso_path": "",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd-new",
            "cmdline": "autoinstall",
        },
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    for row in client.get("/api/images").json():
        assert row["initrd_path"] == "ubuntu/initrd-new"
        assert row["cmdline"] == "autoinstall"
    renamed = client.post(
        f"/images/{first['id']}",
        data={"name": "ubuntu-a2", "iso_id": str(iso["id"]), "folder_id": str(first["folder_id"]), "cmdline": "hack"},
        follow_redirects=False,
    )
    assert renamed.status_code in {302, 303}
    row = next(img for img in client.get("/api/images").json() if img["id"] == first["id"])
    assert row["name"] == "ubuntu-a2"
    assert row["cmdline"] == "autoinstall"


def test_iso_upload_queues_extract_and_keeps_file(client, tmp_path):
    login(client)
    iso = _upload_iso(client)
    dest = tmp_path / "images" / "isos" / str(iso["id"]) / "source.iso"
    assert dest.read_bytes() == b"iso-bytes"
    assert iso["iso_path"] == f"isos/{iso['id']}/source.iso"
    assert iso["size_bytes"] == len(b"iso-bytes")
    assert iso["extract_status"] == "queued"
    template = _add_template(client, "ubuntu-from-iso", iso["id"])
    assert template["extract_status"] == "queued"
    listing = client.get("/images")
    assert f'href="/images/isos/{iso["id"]}"' in listing.text
    assert "Wait until extraction finishes" in listing.text
    run_one_job(iso["id"], 1, runner=_LinuxRunner())
    iso_row = _iso(iso["id"])
    assert iso_row.extract_status == ExtractStatus.ready.value
    assert iso_row.extract_generation == f"linux/{iso['id']}/1"
    assert iso_row.kernel_path == f"isos/{iso['id']}/extracts/1/kernel"
    assert iso_row.iso_path.endswith("source.iso")
    assert dest.is_file()
    api = client.get("/api/images").json()[0]
    assert api["extract_status"] == "ready"
    assert api["kernel_path"] == iso_row.kernel_path


def test_second_template_does_not_requeue_extract(client, tmp_path):
    login(client)
    iso = _upload_iso(client)
    run_one_job(iso["id"], 1, runner=_LinuxNfsRunner())
    first = _add_template(client, "server", iso["id"])
    second = _add_template(client, "minimal", iso["id"])
    assert _iso(iso["id"]).extract_revision == 1
    assert _iso(iso["id"]).extract_status == ExtractStatus.ready.value
    assert first["source_id"] == "ubuntu-server"
    saved = client.post(
        f"/images/{second['id']}",
        data={"name": "minimal", "source_id": "ubuntu-server-minimal", "folder_id": str(second["folder_id"])},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    rows = {row["name"]: row for row in client.get("/api/images").json()}
    assert rows["server"]["source_id"] == "ubuntu-server"
    assert rows["minimal"]["source_id"] == "ubuntu-server-minimal"
    assert _iso(iso["id"]).extract_revision == 1
    page = client.get(f"/images/{second['id']}")
    assert "Ubuntu Server (minimized)" in page.text


def test_retry_extract_requeues_iso(client):
    login(client)
    iso = _upload_iso(client, name="retry-iso")
    from src.db import session_scope
    from src.models import Iso

    with session_scope() as db:
        row = db.get(Iso, iso["id"])
        row.extract_status = ExtractStatus.failed.value
        row.extract_error = "boom"
        db.add(row)
        db.commit()
    retry = client.post(f"/images/isos/{iso['id']}/extract", follow_redirects=False)
    assert retry.status_code in {302, 303}
    assert _iso(iso["id"]).extract_status == ExtractStatus.queued.value
    assert _iso(iso["id"]).extract_revision == 2


def test_custom_kernel_not_overwritten(client):
    login(client)
    iso = _upload_iso(client, name="custom-kernel", kernel_path="ubuntu/vmlinuz", initrd_path="ubuntu/initrd")
    run_one_job(iso["id"], 1, runner=_LinuxRunner())
    row = _iso(iso["id"])
    assert row.kernel_path == "ubuntu/vmlinuz"
    assert row.initrd_path == "ubuntu/initrd"
    assert row.extract_status == ExtractStatus.ready.value


def test_stale_revision_discarded(client):
    login(client)
    iso = _upload_iso(client, name="race-iso")
    from src.db import session_scope
    from src.extract_worker import schedule_extract
    from src.models import Iso

    with session_scope() as db:
        row = db.get(Iso, iso["id"])
        schedule_extract(db, row)
        db.commit()
    run_one_job(iso["id"], 1, runner=_LinuxRunner())
    assert _iso(iso["id"]).extract_revision == 2
    assert _iso(iso["id"]).extract_status == ExtractStatus.queued.value
    run_one_job(iso["id"], 2, runner=_LinuxRunner())
    assert _iso(iso["id"]).extract_status == ExtractStatus.ready.value


def test_non_casper_linux_iso_sanboots(client):
    login(client)
    iso = _upload_iso(client, name="truenas-iso")
    from src.db import session_scope
    from src.inventory.service import deploy_machine, find_by_mac, get_image, get_open_attempt, register_machine
    from src.models import Iso

    with session_scope() as db:
        row = db.get(Iso, iso["id"])
        row.kernel_path = f"isos/{iso['id']}/extracts/1/kernel"
        row.initrd_path = f"isos/{iso['id']}/extracts/1/initrd"
        row.extract_generation = f"nfs/{iso['id']}/1"
        row.source_options = '[{"id": "ubuntu-server", "label": "Ubuntu Server", "default": true}]'
        db.add(row)
        db.commit()
    stale = _add_template(client, "truenas-stale", iso["id"])
    assert stale["source_id"] == "ubuntu-server"

    class OtherIso:
        def list_entries(self, iso):
            return [("boot/vmlinuz", 4, False), ("README", 1, False)]

        def extract_member_bytes(self, iso, member):
            return b"x"

        def extract_tree(self, iso, dest, prefixes=()):
            return None

    run_one_job(iso["id"], 1, runner=OtherIso())
    row = _iso(iso["id"])
    assert row.extract_status == ExtractStatus.ready.value
    assert row.kernel_path == ""
    assert row.initrd_path == ""
    assert row.extract_generation == ""
    assert row.extract_error == ""
    assert row.iso_path.endswith("source.iso")
    assert row.source_options == ""
    page = client.get(f"/images/isos/{iso['id']}")
    assert "sanboot" in page.text
    relinked = next(img for img in client.get("/api/images").json() if img["id"] == stale["id"])
    assert relinked["source_id"] == ""
    assert relinked["source_options"] == []
    template = _add_template(client, "truenas", iso["id"])
    assert template["kernel_path"] == ""
    assert template["iso_path"].endswith("source.iso")
    detail = client.get(f"/images/{template['id']}")
    assert "<option>sanboot</option>" in detail.text
    assert "Ubuntu Server" not in detail.text
    assert "Pick after extract" not in detail.text
    saved = client.post(
        f"/images/{template['id']}",
        data={"name": "truenas", "iso_id": str(iso["id"]), "source_id": "", "folder_id": str(template["folder_id"])},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    with session_scope() as db:
        image = get_image(db, template["id"])
        machine = register_machine(db, mac="02:00:00:00:00:71", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
    response = client.get("/ipxe/02-00-00-00-00-71")
    assert "sanboot" in response.text
    assert f"/boot-files/{template['id']}/iso" in response.text
    assert "kernel " not in response.text
    with session_scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:71")
        assert machine.state == MachineState.deployed.value
        assert get_open_attempt(db, machine) is None
    assert client.get(f"/boot-files/{template['id']}/iso").status_code == 200
    assert "sanboot --no-describe http" not in client.get("/ipxe/02-00-00-00-00-71").text


def test_windows_iso_sanboot_marks_deployed(client, tmp_path):
    from src.db import session_scope
    from src.inventory.service import create_image, deploy_machine, find_by_mac, get_open_attempt, register_machine
    from src.models import OsFamily

    iso_file = tmp_path / "images" / "win" / "server.iso"
    iso_file.parent.mkdir(parents=True, exist_ok=True)
    iso_file.write_bytes(b"win-iso")
    with session_scope() as db:
        image = create_image(
            db, name="win-sanboot", os_family=OsFamily.windows, iso_path="win/server.iso", actor="admin"
        )
        machine = register_machine(db, mac="02:00:00:00:00:72", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        image_id = image.id
    script = client.get("/ipxe/02-00-00-00-00-72").text
    assert "sanboot --no-describe http://" in script
    assert f"/boot-files/{image_id}/iso" in script
    with session_scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:72")
        assert machine.state == MachineState.deployed.value
        assert get_open_attempt(db, machine) is None


def test_extracted_linux_install_stays_deploying(client):
    login(client)
    iso = _upload_iso(client, name="ubuntu-stays")
    run_one_job(iso["id"], 1, runner=_LinuxNfsRunner())
    template = _add_template(client, "ubuntu-stays-tpl", iso["id"])
    from src.db import session_scope
    from src.inventory.service import deploy_machine, find_by_mac, get_image, register_machine

    with session_scope() as db:
        machine = register_machine(db, mac="02:00:00:00:00:73", actor="admin")
        deploy_machine(db, machine, image=get_image(db, template["id"]), actor="admin")
        db.commit()
    assert "kernel --name=vmlinuz" in client.get("/ipxe/02-00-00-00-00-73").text
    with session_scope() as db:
        assert find_by_mac(db, "02:00:00:00:00:73").state == MachineState.deploying.value


def test_non_casper_iso_keeps_custom_kernel(client):
    login(client)
    iso = _upload_iso(client, name="custom-other", kernel_path="custom/vmlinuz", initrd_path="custom/initrd")

    class OtherIso:
        def list_entries(self, iso):
            return [("README", 1, False)]

        def extract_member_bytes(self, iso, member):
            return b"x"

        def extract_tree(self, iso, dest, prefixes=()):
            return None

    run_one_job(iso["id"], 1, runner=OtherIso())
    row = _iso(iso["id"])
    assert row.extract_status == ExtractStatus.ready.value
    assert row.kernel_path == "custom/vmlinuz"
    assert row.initrd_path == "custom/initrd"
    assert row.extract_generation == ""


def test_failed_extract_keeps_iso_and_marks_templates(client):
    login(client)
    iso = _upload_iso(client, name="bad-iso")
    template = _add_template(client, "bad-template", iso["id"])

    class Boom:
        def list_entries(self, iso):
            raise Exception("nope")

        def extract_member_bytes(self, iso, member):
            return b""

        def extract_tree(self, iso, dest, prefixes=()):
            return None

    run_one_job(iso["id"], 1, runner=Boom())
    assert _iso(iso["id"]).extract_status == ExtractStatus.failed.value
    assert _iso(iso["id"]).iso_path.endswith("source.iso")
    row = next(img for img in client.get("/api/images").json() if img["id"] == template["id"])
    assert row["extract_status"] == "failed"


def test_iso_edit_blocked_while_extracting_but_template_saves(client):
    login(client)
    iso = _upload_iso(client, name="busy-iso")
    template = _add_template(client, "busy-template", iso["id"])
    detail = client.get(f"/images/isos/{iso['id']}")
    assert "Edit is disabled until it finishes" in detail.text
    assert 'data-extract-api="/api/isos"' in detail.text
    blocked = client.post(f"/images/isos/{iso['id']}", data={"name": "busy-iso", "cmdline": "x"})
    assert blocked.status_code == 200
    assert "extraction finishes" in blocked.text.lower()
    page = client.get(f"/images/{template['id']}")
    assert "still extracting" in page.text
    assert "<fieldset" not in page.text
    saved = client.post(
        f"/images/{template['id']}",
        data={"name": "busy-renamed", "folder_id": str(template["folder_id"])},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    run_one_job(iso["id"], 1, runner=_LinuxRunner())
    saved = client.post(
        f"/images/isos/{iso['id']}",
        data={"name": "busy-iso", "arch": "x86_64", "iso_path": _iso(iso["id"]).iso_path, "cmdline": "quiet"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    assert _iso(iso["id"]).extract_revision == 1
    names = [row["name"] for row in client.get("/api/images").json()]
    assert "busy-renamed" in names


def test_iso_upload_rejects_unsafe_filename(client):
    login(client)
    response = client.post(
        "/images/isos",
        data={"name": "evil", "os_family": "linux", "arch": "x86_64"},
        files={"kernel_file": ("not a safe name.bin", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "simple relative name" in response.text
    assert 'id="add-iso" open' in response.text


def test_duplicate_names_are_rejected(client):
    login(client)
    iso = _upload_iso(client, name="same")
    again = client.post("/images/isos", data={"name": "same", "os_family": "linux"})
    assert again.status_code == 200
    assert "already exists" in again.text
    assert 'id="add-iso" open' in again.text
    _add_template(client, "tpl", iso["id"])
    dup = client.post("/images", data={"name": "tpl", "iso_id": str(iso["id"])})
    assert dup.status_code == 200
    assert "already exists" in dup.text
    assert 'id="add-template" open' in dup.text


def test_tool_template_keeps_own_boot_media(client, tmp_path):
    login(client)
    response = client.post(
        "/images",
        data={"name": "memtest", "iso_id": "tool", "arch": "x86_64", "cmdline": "console=ttyS0"},
        files={"kernel_file": ("vmlinuz", b"kernel-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    row = client.get("/api/images").json()[0]
    assert row["os_family"] == "tool"
    assert row["iso_id"] is None
    assert row["kernel_path"].startswith(f"uploads/{row['id']}/")
    assert row["cmdline"] == "console=ttyS0"
    matches = list((tmp_path / "images" / "uploads" / str(row["id"])).glob("kernel*"))
    assert matches and matches[0].read_bytes() == b"kernel-bytes"
    page = client.get(f"/images/{row['id']}")
    assert 'name="kernel_path"' in page.text
    assert 'name="user_data"' not in page.text
    client.post(f"/images/{row['id']}/delete", follow_redirects=False)
    assert not matches[0].exists()


def test_iso_range_request(client):
    login(client)
    iso = _upload_iso(client, name="range-iso", body=b"abcdefghij")
    template = _add_template(client, "range-tpl", iso["id"])
    response = client.get(f"/boot-files/{template['id']}/iso", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"cdef"
    head = client.head(f"/boot-files/{template['id']}/iso")
    assert head.status_code == 200
    assert head.headers["content-length"] == "10"
    assert head.headers.get("accept-ranges") == "bytes"
    assert head.content == b""
    assert client.head(f"/boot-files/{template['id']}/nope").status_code == 404


def test_delete_template_keeps_iso_and_media(client, tmp_path):
    login(client)
    iso = _upload_iso(client, name="shared")
    run_one_job(iso["id"], 1, runner=_LinuxNfsRunner())
    first = _add_template(client, "keep", iso["id"])
    second = _add_template(client, "drop", iso["id"])
    seed = tmp_path / "images" / "uploads" / str(second["id"]) / "user-data"
    assert seed.is_file()
    nfs = tmp_path / "images" / "nfs" / str(iso["id"]) / "1"
    iso_file = tmp_path / "images" / "isos" / str(iso["id"]) / "source.iso"
    deleted = client.post(f"/images/{second['id']}/delete", follow_redirects=False)
    assert deleted.status_code in {302, 303}
    assert not seed.exists()
    assert nfs.is_dir()
    assert iso_file.is_file()
    assert [row["name"] for row in client.get("/api/images").json()] == ["keep"]
    blocked = client.post(f"/images/isos/{iso['id']}/delete")
    assert blocked.status_code == 200
    assert "Delete the templates that use this ISO first" in blocked.text
    assert iso_file.is_file()
    listing = client.get("/images")
    assert 'title="Used by 1 template"' in listing.text
    client.post(f"/images/{first['id']}/delete", follow_redirects=False)
    removed = client.post(f"/images/isos/{iso['id']}/delete", follow_redirects=False)
    assert removed.status_code in {302, 303}
    assert client.get("/api/isos").json() == []
    assert not iso_file.exists()
    assert not nfs.exists()


def test_delete_template_blocked_during_install(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import create_image, deploy_machine, register_machine
    from src.models import OsFamily

    with session_scope() as db:
        image = create_image(
            db,
            name="in-use",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:99", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        db.commit()
        image_id = int(image.id)
    blocked = client.post(f"/images/{image_id}/delete")
    assert blocked.status_code == 200
    assert "installing" in blocked.text.lower() or "deploying" in blocked.text.lower()
    names = [img["name"] for img in client.get("/api/images").json()]
    assert "in-use" in names


def test_template_cannot_switch_to_other_os_iso(client):
    login(client)
    linux = _upload_iso(client, name="lin")
    windows = _upload_iso(client, name="win", os_family="windows")
    template = _add_template(client, "lin-tpl", linux["id"])
    response = client.post(
        f"/images/{template['id']}",
        data={"name": "lin-tpl", "iso_id": str(windows["id"]), "folder_id": str(template["folder_id"])},
    )
    assert response.status_code == 200
    assert "same OS" in response.text


def test_linux_iso_publishes_nfs_casper(client, tmp_path):
    login(client)
    iso = _upload_iso(client, name="ubuntu-nfs")
    template = _add_template(client, "ubuntu-nfs-tpl", iso["id"])
    run_one_job(iso["id"], 1, runner=_LinuxNfsRunner())
    row = _iso(iso["id"])
    assert row.extract_generation == f"nfs/{iso['id']}/1"
    assert row.iso_path.endswith("source.iso")
    assert "ubuntu-server-minimal" in row.source_options
    squash = tmp_path / "images" / "nfs" / str(iso["id"]) / "1" / "casper" / "filesystem.squashfs"
    assert squash.read_bytes() == b"squashok"
    api = next(img for img in client.get("/api/images").json() if img["id"] == template["id"])
    assert api["source_id"] == "ubuntu-server"
    page = client.get(f"/images/{template['id']}")
    assert 'name="source_id"' in page.text
    assert "Ubuntu Server (minimized)" in page.text
    kernel = tmp_path / "images" / "isos" / str(iso["id"]) / "extracts" / "1" / "kernel"
    assert kernel.read_bytes() == b"kern"
    assert (tmp_path / "images" / "isos" / str(iso["id"]) / "source.iso").is_file()


class _TrueNasRunner:
    """Debian live-boot layout: kernel and initrd at the ISO root, installer image beside them."""

    files = {
        "vmlinuz": b"tn-kern",
        "initrd.img": b"tn-ird",
        "live/filesystem.squashfs": b"tn-squash",
        "TrueNAS-SCALE.update": b"tn-update",
        "boot/grub/grub.cfg": b"cfg",
    }

    def list_entries(self, iso):
        return [(name, len(body), False) for name, body in self.files.items()]

    def extract_member_bytes(self, iso, member):
        return self.files[member]

    def extract_tree(self, iso, dest, prefixes=()):
        assert prefixes == ()
        for name, body in self.files.items():
            path = Path(dest).joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)


def test_live_boot_iso_netboots_over_nfs(client, tmp_path):
    login(client)
    iso = _upload_iso(client, name="truenas-live")
    from src.db import session_scope
    from src.ganesha_exports import render_generation_exports
    from src.inventory.service import deploy_machine, find_by_mac, get_image, get_open_attempt, register_machine
    from src.models import Iso

    with session_scope() as db:
        row = db.get(Iso, iso["id"])
        row.source_options = '[{"id": "ubuntu-server", "label": "Ubuntu Server", "default": true}]'
        db.add(row)
        db.commit()
    run_one_job(iso["id"], 1, runner=_TrueNasRunner())
    row = _iso(iso["id"])
    assert row.extract_status == ExtractStatus.ready.value, row.extract_error
    assert row.extract_generation == f"nfs/{iso['id']}/1"
    assert row.source_options == ""
    tree = tmp_path / "images" / "nfs" / str(iso["id"]) / "1"
    assert (tree / "TrueNAS-SCALE.update").read_bytes() == b"tn-update"
    assert str(tree.resolve().as_posix()) in render_generation_exports(tmp_path / "images")
    from src.extract_worker import linux_needs_nfs_backfill

    assert linux_needs_nfs_backfill(row) is False

    template = _add_template(client, "truenas-live-tpl", iso["id"])
    assert template["source_id"] == ""
    assert client.get(f"/boot-files/{template['id']}/kernel").content == b"tn-kern"
    detail = client.get(f"/images/{template['id']}").text
    assert "<option>live-boot</option>" in detail
    assert "Ubuntu Server" not in detail
    assert "live-boot" in client.get(f"/images/isos/{iso['id']}").text

    with session_scope() as db:
        machine = register_machine(db, mac="02:00:00:00:00:74", actor="admin")
        deploy_machine(db, machine, image=get_image(db, template["id"]), actor="admin")
        db.commit()
    script = client.get("/ipxe/02-00-00-00-00-74").text
    kernel_line = next(line for line in script.splitlines() if line.startswith("kernel "))
    assert "boot=live" in kernel_line
    assert "netboot=nfs" in kernel_line
    assert f"nfsroot=pxe.test:/var/lib/pxe/images/nfs/{iso['id']}/1" in kernel_line
    assert "nfsopts=vers=3,tcp,port=2049" in kernel_line
    assert "systemd.mount-extra=/run/live/medium:/cdrom:none:bind" in kernel_line
    assert "autoinstall" not in script
    assert "cloud-init" not in script
    assert "sanboot" not in script
    assert _kernel_bytes(client, script) == b"tn-kern"
    with session_scope() as db:
        machine = find_by_mac(db, "02:00:00:00:00:74")
        assert machine.state == MachineState.deployed.value
        assert get_open_attempt(db, machine) is None
    assert _kernel_bytes(client, script) == b"tn-kern"


def test_live_boot_serves_current_revision_after_reextract(client, tmp_path):
    login(client)
    iso = _upload_iso(client, name="truenas-reextract")
    from src.db import session_scope
    from src.extract_worker import schedule_extract
    from src.inventory.service import deploy_machine, get_image, register_machine
    from src.models import Iso

    run_one_job(iso["id"], 1, runner=_TrueNasRunner())
    template = _add_template(client, "truenas-reextract-tpl", iso["id"])
    with session_scope() as db:
        machine = register_machine(db, mac="02:00:00:00:00:75", actor="admin")
        deploy_machine(db, machine, image=get_image(db, template["id"]), actor="admin")
        db.commit()
    with session_scope() as db:
        assert schedule_extract(db, db.get(Iso, iso["id"]))
        db.commit()
    run_one_job(iso["id"], 2, runner=_TrueNasRunner())
    nfs = tmp_path / "images" / "nfs" / str(iso["id"])
    assert (nfs / "1").is_dir()

    script = client.get("/ipxe/02-00-00-00-00-75").text
    kernel_line = next(line for line in script.splitlines() if line.startswith("kernel "))
    assert f"nfsroot=pxe.test:/var/lib/pxe/images/nfs/{iso['id']}/2" in kernel_line
    assert (nfs / "2" / "live" / "filesystem.squashfs").is_file()
    assert not (nfs / "1").exists()


def test_template_delete_ignores_paths_that_climb_out_of_its_folder(client, tmp_path):
    from src.image_store import remove_template_files

    images = tmp_path / "images"
    other = images / "nfs" / "9" / "1"
    other.mkdir(parents=True)
    (other / "keep").write_bytes(b"x")
    own = images / "uploads" / "5"
    own.mkdir(parents=True)
    (own / "kernel").write_bytes(b"k")
    remove_template_files(5, ("uploads/5/../../nfs", "uploads/5/./kernel", "uploads/5/kernel"))
    assert (other / "keep").is_file()
    assert not (own / "kernel").exists()


def test_sanboot_iso_is_not_requeued_on_worker_start(client):
    login(client)
    iso = _upload_iso(client, name="sanboot-restart")

    class OtherIso:
        def list_entries(self, iso):
            return [("README", 1, False)]

        def extract_member_bytes(self, iso, member):
            return b"x"

        def extract_tree(self, iso, dest, prefixes=()):
            return None

    run_one_job(iso["id"], 1, runner=OtherIso())
    from src.extract_worker import linux_needs_nfs_backfill

    assert linux_needs_nfs_backfill(_iso(iso["id"])) is False


class _PerIsoLinuxRunner(_LinuxNfsRunner):
    """Kernel bytes come from the ISO file, so a template on the wrong ISO serves the wrong kernel."""

    def extract_member_bytes(self, iso, member):
        tag = Path(iso).read_bytes()
        return b"kern-" + tag if "vmlinuz" in member.replace("\\", "/") else b"ird-" + tag

    def extract_tree(self, iso, dest, prefixes=()):
        super().extract_tree(iso, dest, prefixes)
        tag = Path(iso).read_bytes()
        (Path(dest) / "casper" / "vmlinuz").write_bytes(b"kern-" + tag)
        (Path(dest) / "casper" / "filesystem.squashfs").write_bytes(b"squash-" + tag)


def _kernel_bytes(client, script):
    url = re.search(r"^kernel --name=vmlinuz (\S+)", script, re.M).group(1)
    return client.get(urlsplit(url).path).content


def test_second_linux_iso_boots_its_own_kernel(client):
    login(client)
    first = _upload_iso(client, name="ubuntu-old", body=b"old-release")
    second = _upload_iso(client, name="ubuntu-new", body=b"new-release")
    run_one_job(first["id"], 1, runner=_PerIsoLinuxRunner())
    run_one_job(second["id"], 1, runner=_PerIsoLinuxRunner())
    old_tpl = _add_template(client, "old-tpl", first["id"])
    new_tpl = _add_template(client, "new-tpl", second["id"])
    assert old_tpl["kernel_path"] != new_tpl["kernel_path"]
    assert new_tpl["kernel_path"].startswith(f"isos/{second['id']}/")

    from src.db import session_scope
    from src.inventory.service import deploy_machine, find_by_mac, get_image, mark_deployed, mark_ready

    client.get("/ipxe/02-00-00-00-00-81")
    client.get("/ipxe/02-00-00-00-00-82")
    with session_scope() as db:
        deploy_machine(db, find_by_mac(db, "02:00:00:00:00:81"), image=get_image(db, new_tpl["id"]), actor="admin")
        picker = find_by_mac(db, "02:00:00:00:00:82")
        mark_ready(db, picker, hostname="picker", actor="admin")
        deploy_machine(db, picker, image=get_image(db, old_tpl["id"]), actor="admin")
        mark_deployed(db, picker)
        db.commit()

    deployed = client.get("/ipxe/02-00-00-00-00-81").text
    assert _kernel_bytes(client, deployed) == b"kern-new-release"
    if "nfsroot=" in deployed:
        assert f"/{second['id']}/1 " in deployed

    picked = client.get(f"/ipxe/02-00-00-00-00-82/boot/{new_tpl['id']}").text
    assert _kernel_bytes(client, picked) == b"kern-new-release"
    assert _kernel_bytes(client, client.get("/ipxe/02-00-00-00-00-82").text) == b"kern-new-release"


def test_linux_nfs_backfill_requeues_missing_apt_repo(client, tmp_path):
    login(client)
    iso = _upload_iso(client, name="ubuntu-nfs-apt")
    run_one_job(iso["id"], 1, runner=_LinuxNfsRunner())
    shutil.rmtree(tmp_path / "images" / "nfs" / str(iso["id"]) / "1" / "dists")
    from src.extract_worker import requeue_linux_nfs_backfill

    requeue_linux_nfs_backfill()
    assert _iso(iso["id"]).extract_status == ExtractStatus.queued.value
    assert _iso(iso["id"]).extract_revision == 2


def test_linux_http_generation_skips_nfs_backfill(client):
    login(client)
    iso = _upload_iso(client, name="kernel-only")
    run_one_job(iso["id"], 1, runner=_LinuxRunner())
    from src.extract_worker import requeue_linux_nfs_backfill

    requeue_linux_nfs_backfill()
    row = _iso(iso["id"])
    assert row.extract_status == ExtractStatus.ready.value
    assert row.extract_revision == 1


def test_windows_iso_extract_keeps_iso(client, tmp_path):
    login(client)
    iso = _upload_iso(client, name="ws-iso", os_family="windows")
    core = _add_template(client, "ws-core", iso["id"])
    dc = _add_template(client, "ws-dc", iso["id"])
    run_one_job(iso["id"], 1, runner=_WindowsRunner())
    row = _iso(iso["id"])
    assert row.extract_status == ExtractStatus.ready.value
    assert row.boot_wim_path.endswith("boot.wim")
    assert (tmp_path / "images" / "isos" / str(iso["id"]) / "source.iso").is_file()
    assert (tmp_path / "images" / "smb" / str(iso["id"]) / "1" / "setup.exe").is_file()
    saved = client.post(
        f"/images/{dc['id']}",
        data={"name": "ws-dc", "source_id": "Windows Server 2022 SERVERDATACENTER", "folder_id": str(dc["folder_id"])},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    rows = {img["id"]: img for img in client.get("/api/images").json()}
    assert rows[core["id"]]["source_id"] == "Windows Server 2022 SERVERSTANDARDCORE"
    assert rows[core["id"]]["wim_index"] == 1
    assert rows[dc["id"]]["source_id"] == "Windows Server 2022 SERVERDATACENTER"
    assert rows[dc["id"]]["wim_index"] == 2
    assert rows[dc["id"]]["boot_wim_path"] == row.boot_wim_path


def test_legacy_image_migrates_onto_iso_with_same_id(client, tmp_path):
    login(client)
    from src.db import _migrate_schema, get_engine, session_scope
    from src.models import Image, Iso

    iso_file = tmp_path / "images" / "uploads" / "7" / "image.iso"
    iso_file.parent.mkdir(parents=True, exist_ok=True)
    iso_file.write_bytes(b"legacy")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO image (id, name, os_family, arch, kernel_path, initrd_path, boot_wim_path, "
                "install_wim_path, iso_path, cmdline, extract_status, extract_error, extract_revision, "
                "extract_generation, wim_index, source_id, source_options, sort_order) VALUES "
                "(7, 'legacy', 'linux', 'x86_64', 'uploads/7/extracts/3/kernel', 'uploads/7/extracts/3/initrd', "
                "'', '', 'uploads/7/image.iso', 'quiet', 'ready', '', 3, 'nfs/7/3', 1, 'ubuntu-server', '', 0)"
            )
        )
    _migrate_schema()
    _migrate_schema()
    with session_scope() as db:
        iso = db.get(Iso, 7)
        image = db.get(Image, 7)
        assert iso is not None
        assert iso.name == "legacy"
        assert iso.extract_generation == "nfs/7/3"
        assert iso.kernel_path == "uploads/7/extracts/3/kernel"
        assert iso.cmdline == "quiet"
        assert iso.size_bytes == len(b"legacy")
        assert image.iso_id == 7
        assert len(db.exec(select(Iso)).all()) == 1
    iso_new = _upload_iso(client, name="after-migration")
    assert iso_new["id"] > 7


def test_iso_upload_script_closes_add_dialog(client):
    login(client)
    js = client.get("/static/app.js")
    assert js.status_code == 200
    text_js = js.content.decode()
    assert "uploadIsoWithProgress" in text_js
    assert 'form.closest("dialog")' in text_js
    assert "dialog.upload-overlay" in text_js
    assert "dlg.close" in text_js
    assert "showModal" in text_js
    assert "/api/isos" in text_js
