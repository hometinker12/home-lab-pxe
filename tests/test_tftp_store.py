import pytest
from tests.conftest import login

from src.tftp_store import TftpStoreError, format_bytes, is_stub, resolve_tftp, write_boot_chain_script


def test_client_writes_tftp_boot_chain_script(client, tmp_path):
    path = tmp_path / "tftp" / "boot.ipxe"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!ipxe")
    assert "chain --replace http://pxe.test:8080/ipxe/${mac:hexhyp}" in text


def test_write_boot_chain_script_uses_public_url(client, tmp_path):
    path = write_boot_chain_script()
    assert path.name == "boot.ipxe"
    assert "http://pxe.test:8080/ipxe/" in path.read_text(encoding="utf-8")


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(10) == "10 B"
    assert format_bytes(1024) == "1.0 KB"
    assert "KB" in format_bytes(72373)


def test_settings_lists_tftp_files_and_stubs(client, tmp_path):
    login(client)
    root = tmp_path / "tftp"
    root.mkdir(exist_ok=True)
    (root / "undionly.kpxe").write_bytes(b"ipxe-stub\n")
    (root / "ipxe.efi").write_bytes(b"MZ" + b"\x00" * 200)
    settings = client.get("/settings")
    assert "tftp-browser" not in settings.text
    page = client.get("/files")
    assert page.status_code == 200
    assert 'class="fm"' in page.text
    assert 'href="/files" class="current"' in page.text
    assert "tftp-browser" not in page.text
    assert "undionly.kpxe" in page.text
    assert "stub" in page.text
    assert "Volumes" in page.text
    assert "Date Modified" in page.text


def test_tftp_upload_download_delete(client, tmp_path):
    login(client)
    uploaded = client.post(
        "/files/upload",
        data={"dir": ""},
        files={"file": ("snponly.efi", b"real-efi-bytes", "application/octet-stream")},
        follow_redirects=False,
    )
    assert uploaded.status_code in {302, 303}
    dest = tmp_path / "tftp" / "snponly.efi"
    assert dest.read_bytes() == b"real-efi-bytes"
    listing = client.get("/files")
    assert "snponly.efi" in listing.text
    downloaded = client.get("/files/download", params={"path": "snponly.efi"})
    assert downloaded.status_code == 200
    assert downloaded.content == b"real-efi-bytes"
    deleted = client.post(
        "/files/delete",
        data={"path": "snponly.efi", "dir": ""},
        follow_redirects=False,
    )
    assert deleted.status_code in {302, 303}
    assert not dest.exists()


def test_tftp_mkdir_and_nested_upload(client, tmp_path):
    login(client)
    created = client.post("/files/mkdir", data={"dir": "", "name": "efi"}, follow_redirects=False)
    assert created.status_code in {302, 303}
    assert (tmp_path / "tftp" / "efi").is_dir()
    nested = client.post(
        "/files/upload",
        data={"dir": "efi"},
        files={"file": ("ipxe.efi", b"nested-efi", "application/octet-stream")},
        follow_redirects=False,
    )
    assert nested.status_code in {302, 303}
    assert (tmp_path / "tftp" / "efi" / "ipxe.efi").read_bytes() == b"nested-efi"
    page = client.get("/files?dir=efi")
    assert "ipxe.efi" in page.text
    assert "/files/download?path=efi" in page.text


def test_tftp_rejects_path_escape(client, tmp_path):
    login(client)
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"nope")
    listing = client.get("/files?dir=../")
    assert listing.status_code == 200
    assert "not allowed" in listing.text.lower()
    download = client.get("/files/download", params={"path": "../secret.bin"})
    assert download.status_code in {400, 404, 422}
    client.post("/files/delete", data={"path": "../secret.bin", "dir": ""})
    assert secret.read_bytes() == b"nope"
    client.post(
        "/files/upload",
        data={"dir": ".."},
        files={"file": ("evil.bin", b"x", "application/octet-stream")},
    )
    assert not (tmp_path / "evil.bin").exists()
    assert not (tmp_path / "tftp" / "evil.bin").exists()


def test_tftp_upload_rejects_unsafe_filename(client):
    login(client)
    response = client.post(
        "/files/upload",
        data={"dir": ""},
        files={"file": ("not a safe name.bin", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "simple relative name" in response.text


def test_tftp_download_requires_auth(client, tmp_path):
    root = tmp_path / "tftp"
    root.mkdir(exist_ok=True)
    (root / "undionly.kpxe").write_bytes(b"abc")
    response = client.get("/files/download", params={"path": "undionly.kpxe"}, follow_redirects=False)
    assert response.status_code in {401, 303}


def test_files_page_requires_auth(client):
    response = client.get("/files", follow_redirects=False)
    assert response.status_code in {401, 303}


def test_tftp_cannot_delete_root(client):
    login(client)
    response = client.post("/files/delete", data={"path": "", "dir": ""})
    assert response.status_code == 200
    assert "Cannot delete the TFTP root" in response.text


def test_tftp_cannot_delete_nonempty_folder(client, tmp_path):
    login(client)
    client.post("/files/mkdir", data={"dir": "", "name": "keep"}, follow_redirects=False)
    client.post(
        "/files/upload",
        data={"dir": "keep"},
        files={"file": ("a.efi", b"x", "application/octet-stream")},
        follow_redirects=False,
    )
    response = client.post("/files/delete", data={"path": "keep", "dir": ""})
    assert response.status_code == 200
    assert "not empty" in response.text.lower()
    assert (tmp_path / "tftp" / "keep" / "a.efi").is_file()


def test_resolve_tftp_blocks_escape(client):
    with pytest.raises(TftpStoreError):
        resolve_tftp("../etc/passwd")


def test_is_stub_detects_placeholder(client, tmp_path):
    path = tmp_path / "tftp" / "ipxe.efi"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"ipxe-stub\n")
    assert is_stub(path) is True
    path.write_bytes(b"MZ" + b"\x00" * 80)
    assert is_stub(path) is False
