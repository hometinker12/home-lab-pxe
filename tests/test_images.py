import shutil
from pathlib import Path

from tests.conftest import login
from tests.test_install_sources import minimal_wim_bytes

from src.extract_worker import run_one_job
from src.models import ExtractStatus


class _LinuxRunner:
    def list_entries(self, iso):
        return [("casper/vmlinuz", 4, False), ("casper/initrd", 4, False)]

    def extract_member_bytes(self, iso, member):
        return b"kern" if "vmlinuz" in member.replace("\\", "/") else b"ird"

    def extract_tree(self, iso, dest, prefixes=()):
        return None


def test_create_and_edit_image_metadata(client):
    login(client)
    created = client.post(
        "/images",
        data={
            "name": "ubuntu-edit",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
            "cmdline": "quiet",
        },
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    assert created.headers["location"] == "/images/1"
    listing = client.get("/images")
    assert "ubuntu-edit" in listing.text
    assert 'href="/images/1"' in listing.text
    assert "Edit" in listing.text
    assert "Delete" in listing.text
    assert 'data-open-dialog="add-image"' in listing.text
    assert 'id="add-image"' in listing.text
    assert '<section class="card">' not in listing.text
    detail = client.get("/images/1")
    assert detail.status_code == 200
    assert "ubuntu/vmlinuz" in detail.text
    assert "Choose file" in detail.text
    assert "data-iso-path-display" in detail.text
    assert 'name="iso_path"' in detail.text
    assert 'data-os="linux,tool" open' not in detail.text
    assert "Install source" in detail.text
    assert "Pick after extract" in detail.text
    updated = client.post(
        "/images/1",
        data={
            "name": "ubuntu-edit",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd-new",
            "cmdline": "autoinstall",
        },
        follow_redirects=False,
    )
    assert updated.status_code in {302, 303}
    again = client.get("/images/1")
    assert "initrd-new" in again.text
    assert "autoinstall" in again.text
    payload = client.get("/api/images").json()
    assert payload[0]["initrd_path"] == "ubuntu/initrd-new"
    assert payload[0]["extract_status"] == "idle"


def test_edit_image_iso_path_disabled_and_advanced_collapsed(client):
    login(client)
    created = client.post(
        "/images",
        data={
            "name": "iso-row",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
            "iso_path": "uploads/9/server.iso",
        },
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    page = client.get("/images/1")
    assert page.status_code == 200
    assert "data-iso-path-display" in page.text
    assert 'class="path-file-row"' in page.text
    assert 'value="uploads/9/server.iso" disabled' in page.text
    assert "Choose file" in page.text
    assert 'name="iso_file"' in page.text
    assert page.text.find("data-iso-path-display") < page.text.find("Choose file")
    assert 'data-os="linux,tool" open' not in page.text
    saved = client.post(
        "/images/1",
        data={
            "name": "iso-row",
            "os_family": "linux",
            "arch": "x86_64",
            "iso_path": "uploads/9/server.iso",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
        },
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    assert client.get("/api/images").json()[0]["iso_path"] == "uploads/9/server.iso"


def test_image_upload_writes_under_image_root(client, tmp_path):
    login(client)
    response = client.post(
        "/images",
        data={"name": "uploaded-linux", "os_family": "linux", "arch": "x86_64"},
        files={"kernel_file": ("vmlinuz", b"kernel-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    matches = list((tmp_path / "images" / "uploads" / "1").glob("kernel*"))
    assert matches
    assert matches[0].read_bytes() == b"kernel-bytes"
    api = client.get("/api/images").json()
    assert api[0]["kernel_path"].startswith("uploads/1/")


def test_iso_upload_queues_extract(client, tmp_path):
    login(client)
    response = client.post(
        "/images",
        data={"name": "ubuntu-iso", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert response.headers["location"] == "/images"
    dest = tmp_path / "images" / "uploads" / "1" / "image.iso"
    assert dest.read_bytes() == b"iso-bytes"
    api = client.get("/api/images").json()
    assert api[0]["iso_path"] == "uploads/1/image.iso"
    assert api[0]["extract_status"] == "queued"
    listing = client.get("/images")
    assert 'data-os="linux,tool"' in listing.text
    assert 'data-os="windows"' in listing.text
    assert "iso_file" in listing.text
    assert "data-image-edit" in listing.text
    assert 'href="/images/1"' in listing.text
    assert "Wait until extraction finishes" in listing.text
    from src.db import session_scope

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxRunner())
        db.commit()
    api = client.get("/api/images").json()
    assert api[0]["extract_status"] == "ready"
    assert api[0]["kernel_path"].endswith("/kernel")
    assert dest.read_bytes() == b"iso-bytes"
    from src.models import Image

    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.extract_generation == "linux/1/1"
        assert image.iso_path.endswith("image.iso")
    dest = tmp_path / "images" / "uploads" / "1" / "image.iso"
    assert dest.is_file()


def test_missing_iso_stays_idle(client):
    login(client)
    from src.db import session_scope
    from src.inventory.service import create_image
    from src.models import OsFamily

    with session_scope() as db:
        create_image(db, name="ghost-iso", os_family=OsFamily.linux, iso_path="ubuntu/live.iso", actor="admin")
        db.commit()
    api = client.get("/api/images").json()
    ghost = next(img for img in api if img["name"] == "ghost-iso")
    assert ghost["extract_status"] == "idle"


def test_retry_extract_requeues(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "retry-iso", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.models import Image

    with session_scope() as db:
        image = db.get(Image, 1)
        image.extract_status = ExtractStatus.failed.value
        image.extract_error = "boom"
        db.add(image)
        db.commit()
        rev = image.extract_revision
    retry = client.post("/images/1/extract", follow_redirects=False)
    assert retry.status_code in {302, 303}
    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.queued.value
        assert image.extract_revision == rev + 1


def test_custom_kernel_not_overwritten(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={
            "name": "custom-kernel",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
        },
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.models import Image

    with session_scope() as db:
        image = db.get(Image, 1)
        run_one_job(1, int(image.extract_revision), runner=_LinuxRunner())
        db.commit()
        image = db.get(Image, 1)
        assert image.kernel_path == "ubuntu/vmlinuz"
        assert image.initrd_path == "ubuntu/initrd"
        assert image.extract_status == ExtractStatus.ready.value


def test_stale_revision_discarded(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "race-iso", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.extract_worker import schedule_extract
    from src.models import Image

    with session_scope() as db:
        image = db.get(Image, 1)
        first = int(image.extract_revision)
        schedule_extract(db, image)
        second = int(image.extract_revision)
        db.commit()
    run_one_job(1, first, runner=_LinuxRunner())
    from src.db import session_scope as scope2

    with scope2() as db:
        image = db.get(Image, 1)
        assert int(image.extract_revision) == second
        assert image.extract_status == ExtractStatus.queued.value
    run_one_job(1, second, runner=_LinuxRunner())
    with scope2() as db:
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.ready.value


def test_failed_extract_keeps_iso(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "bad-iso", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )

    class Boom:
        def list_entries(self, iso):
            raise Exception("nope")

        def extract_member_bytes(self, iso, member):
            return b""

        def extract_tree(self, iso, dest, prefixes=()):
            return None

    from src.db import session_scope
    from src.models import Image

    with session_scope() as db:
        image = db.get(Image, 1)
        run_one_job(1, int(image.extract_revision), runner=Boom())
        db.commit()
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.failed.value
        assert image.iso_path.endswith("image.iso")


def test_image_form_hides_os_specific_fields(client):
    login(client)
    page = client.get("/images")
    assert 'data-open-dialog="add-image"' in page.text
    assert 'id="add-image"' in page.text
    assert '<section class="card">' not in page.text
    assert "boot_wim_file" in page.text
    assert 'data-os="windows"' in page.text
    assert "iso_file" in page.text
    assert "Advanced Settings" in page.text
    assert "kernel_path" in page.text
    assert page.text.find('name="iso_file"') < page.text.find("Advanced Settings")


def test_iso_upload_script_closes_add_dialog(client):
    login(client)
    js = client.get("/static/app.js")
    assert js.status_code == 200
    text = js.content.decode()
    assert "uploadIsoWithProgress" in text
    assert 'form.closest("dialog")' in text
    assert "dialog.upload-overlay" in text
    assert "dlg.close" in text
    assert "showModal" in text


def test_edit_blocked_while_extracting(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "busy-iso", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    listing = client.get("/images")
    assert 'href="/images/1"' in listing.text
    assert "Wait until extraction finishes" in listing.text
    detail = client.get("/images/1")
    assert detail.status_code == 200
    assert "Edit is disabled until it finishes" in detail.text
    blocked = client.post(
        "/images/1",
        data={"name": "busy-iso", "os_family": "linux", "arch": "x86_64", "kernel_path": "ubuntu/vmlinuz"},
    )
    assert blocked.status_code == 200
    assert "extraction finishes" in blocked.text.lower()
    from src.db import session_scope

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxRunner())
        db.commit()
    listing = client.get("/images")
    assert 'href="/images/1"' in listing.text
    saved = client.post(
        "/images/1",
        data={"name": "busy-iso", "os_family": "linux", "arch": "x86_64", "kernel_path": "ubuntu/vmlinuz"},
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}


def test_image_upload_rejects_unsafe_filename(client):
    login(client)
    response = client.post(
        "/images",
        data={"name": "evil", "os_family": "linux", "arch": "x86_64"},
        files={"kernel_file": ("not a safe name.bin", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "simple relative name" in response.text
    assert 'id="add-image" open' in response.text


def test_duplicate_image_name_is_rejected(client):
    login(client)
    first = client.post(
        "/images",
        data={"name": "same", "os_family": "linux", "arch": "x86_64", "kernel_path": "a/vmlinuz"},
        follow_redirects=False,
    )
    assert first.status_code in {302, 303}
    second = client.post(
        "/images",
        data={"name": "same", "os_family": "linux", "arch": "x86_64", "kernel_path": "b/vmlinuz"},
    )
    assert second.status_code == 200
    assert "already exists" in second.text
    assert 'id="add-image" open' in second.text


def test_iso_range_request(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "range-iso", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"abcdefghij", "application/octet-stream")},
        follow_redirects=False,
    )
    response = client.get("/boot-files/1/iso", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"cdef"


def test_delete_image_removes_row_and_files(client, tmp_path):
    login(client)
    created = client.post(
        "/images",
        data={"name": "to-delete", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    assert created.headers["location"] == "/images"
    uploaded = tmp_path / "images" / "uploads" / "1"
    assert uploaded.is_dir()
    deleted = client.post("/images/1/delete", follow_redirects=False)
    assert deleted.status_code in {302, 303}
    assert deleted.headers["location"] == "/images"
    assert client.get("/api/images").json() == []
    assert not uploaded.exists()
    listing = client.get("/images")
    assert "to-delete" not in listing.text


def test_delete_image_blocked_during_install(client):
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
    assert 'id="add-image" open' not in blocked.text
    names = [img["name"] for img in client.get("/api/images").json()]
    assert "in-use" in names


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


def test_linux_iso_publishes_nfs_casper(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "ubuntu-nfs", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxNfsRunner())
        db.commit()
    api = client.get("/api/images").json()
    assert api[0]["extract_status"] == "ready"
    from src.models import Image

    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.extract_generation == "nfs/1/1"
        assert image.iso_path == ""
        assert image.source_id == "ubuntu-server"
        assert "ubuntu-server-minimal" in (image.source_options or "")
    squash = tmp_path / "images" / "nfs" / "1" / "1" / "casper" / "filesystem.squashfs"
    assert squash.read_bytes() == b"squashok"
    page = client.get("/images/1")
    assert 'name="source_id"' in page.text
    assert "ubuntu-server-minimal" in page.text
    assert "Ubuntu Server (minimized)" in page.text
    saved = client.post(
        "/images/1",
        data={
            "name": "ubuntu-nfs",
            "os_family": "linux",
            "arch": "x86_64",
            "source_id": "ubuntu-server-minimal",
            "kernel_path": api[0]["kernel_path"],
            "initrd_path": api[0]["initrd_path"],
        },
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    assert client.get("/api/images").json()[0]["source_id"] == "ubuntu-server-minimal"
    assert (tmp_path / "images" / "nfs" / "1" / "1" / "dists" / "resolute" / "Release").read_bytes() == b"rel"
    kernel = tmp_path / "images" / "uploads" / "1" / "extracts" / "1" / "kernel"
    assert kernel.read_bytes() == b"kern"
    iso = tmp_path / "images" / "uploads" / "1" / "image.iso"
    assert not iso.exists()


def test_linux_nfs_backfill_requeues_missing_apt_repo(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "ubuntu-nfs-apt", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.extract_worker import requeue_linux_nfs_backfill
    from src.models import Image

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxNfsRunner())
        db.commit()
    shutil.rmtree(tmp_path / "images" / "nfs" / "1" / "1" / "dists")
    iso = tmp_path / "images" / "uploads" / "1" / "image.iso"
    iso.parent.mkdir(parents=True, exist_ok=True)
    iso.write_bytes(b"iso")
    with session_scope() as db:
        image = db.get(Image, 1)
        image.iso_path = "uploads/1/image.iso"
        db.add(image)
        db.commit()
    requeue_linux_nfs_backfill()
    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.queued.value
        assert int(image.extract_revision) == 2


def test_linux_nfs_backfill_requeues_legacy_extract(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "legacy-linux", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.extract_worker import requeue_linux_nfs_backfill
    from src.models import Image

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxRunner())
        image = db.get(Image, 1)
        image.extract_generation = "uploads/1/extracts/1"
        db.add(image)
        db.commit()
    requeue_linux_nfs_backfill()
    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.queued.value
        assert int(image.extract_revision) == 2


def test_linux_http_generation_skips_nfs_backfill(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "kernel-only", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.extract_worker import requeue_linux_nfs_backfill
    from src.models import Image

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxRunner())
        db.commit()
    requeue_linux_nfs_backfill()
    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.ready.value
        assert image.extract_generation == "linux/1/1"
        assert int(image.extract_revision) == 1
        assert image.iso_path.endswith("image.iso")
    assert (tmp_path / "images" / "uploads" / "1" / "image.iso").is_file()


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


def test_windows_iso_extract_deletes_uploaded_iso(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "ws-iso", "os_family": "windows", "arch": "x86_64"},
        files={"iso_file": ("server.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.models import Image

    iso = tmp_path / "images" / "uploads" / "1" / "image.iso"
    assert iso.is_file()
    with session_scope() as db:
        run_one_job(1, 1, runner=_WindowsRunner())
        db.commit()
        image = db.get(Image, 1)
        assert image.extract_status == ExtractStatus.ready.value
        assert image.iso_path == ""
        assert image.boot_wim_path.endswith("boot.wim")
        assert image.source_id == "Windows Server 2022 SERVERSTANDARDCORE"
        assert "SERVERDATACENTER" in (image.source_options or "")
        assert int(image.wim_index or 0) == 1
    assert not iso.exists()
    assert (tmp_path / "images" / "smb" / "1" / "1" / "setup.exe").is_file()


def test_discard_sweep_removes_iso_after_nfs_media(client, tmp_path):
    login(client)
    client.post(
        "/images",
        data={"name": "already-nfs", "os_family": "linux", "arch": "x86_64"},
        files={"iso_file": ("ubuntu.iso", b"iso-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    from src.db import session_scope
    from src.extract_worker import discard_isos_for_extracted_media
    from src.models import Image

    iso = tmp_path / "images" / "uploads" / "1" / "image.iso"
    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxNfsRunner())
        image = db.get(Image, 1)
        image.iso_path = "uploads/1/image.iso"
        db.add(image)
        db.commit()
    iso.write_bytes(b"iso-bytes")
    assert iso.is_file()
    discard_isos_for_extracted_media()
    assert not iso.exists()
    with session_scope() as db:
        image = db.get(Image, 1)
        assert image.iso_path == ""
