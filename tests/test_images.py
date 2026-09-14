from tests.conftest import login


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


def test_iso_upload_writes_under_image_root(client, tmp_path):
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
    listing = client.get("/images")
    assert 'data-os="linux"' in listing.text
    assert 'data-os="windows"' in listing.text
    assert "iso_file" in listing.text


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
