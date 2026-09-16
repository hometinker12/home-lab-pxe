from tests.conftest import login

from src.extract_worker import run_one_job
from src.models import ExtractStatus


class _LinuxRunner:
    def list_entries(self, iso):
        return [("casper/vmlinuz", 4, False), ("casper/initrd", 4, False)]

    def extract_member_bytes(self, iso, member):
        return b"kern" if "vmlinuz" in member.replace("\\", "/") else b"ird"

    def extract_tree(self, iso, dest):
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
    assert "Edit" in listing.text
    detail = client.get("/images/1")
    assert detail.status_code == 200
    assert "ubuntu/vmlinuz" in detail.text
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
    dest = tmp_path / "images" / "uploads" / "1" / "image.iso"
    assert dest.read_bytes() == b"iso-bytes"
    api = client.get("/api/images").json()
    assert api[0]["iso_path"] == "uploads/1/image.iso"
    assert api[0]["extract_status"] == "queued"
    listing = client.get("/images")
    assert 'data-os="linux"' in listing.text
    assert 'data-os="windows"' in listing.text
    assert "iso_file" in listing.text
    from src.db import session_scope

    with session_scope() as db:
        run_one_job(1, 1, runner=_LinuxRunner())
        db.commit()
    api = client.get("/api/images").json()
    assert api[0]["extract_status"] == "ready"
    assert api[0]["kernel_path"].endswith("/kernel")
    assert dest.read_bytes() == b"iso-bytes"


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

        def extract_tree(self, iso, dest):
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
    assert "boot_wim_file" in page.text
    assert 'data-os="windows"' in page.text
    assert "iso_file" in page.text


def test_image_upload_rejects_unsafe_filename(client):
    login(client)
    response = client.post(
        "/images",
        data={"name": "evil", "os_family": "linux", "arch": "x86_64"},
        files={"kernel_file": ("not a safe name.bin", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "simple relative name" in response.text


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
