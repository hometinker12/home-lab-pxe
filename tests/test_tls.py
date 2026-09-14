from pathlib import Path

from cryptography.hazmat.primitives import serialization
from tests.conftest import login

from src.tls_store import (
    SOURCE_OPERATOR,
    SOURCE_SELF_SIGNED,
    TlsError,
    cert_path,
    ensure_tls_material,
    install_pem,
    issue_self_signed_pem,
    key_path,
    load_tls_info,
    reload_path,
)


def test_ensure_generates_once(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PXE_SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("PXE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PXE_PUBLIC_URL", "http://pxe.test:8080")
    from src.settings import clear_settings_cache

    clear_settings_cache()
    first = ensure_tls_material()
    assert first.source == SOURCE_SELF_SIGNED
    assert cert_path().is_file()
    assert key_path().is_file()
    text = cert_path().read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in text
    assert "BEGIN PRIVATE" in key_path().read_text(encoding="utf-8")
    fingerprint = first.fingerprint_sha256
    again = ensure_tls_material()
    assert again.fingerprint_sha256 == fingerprint
    assert again.source == SOURCE_SELF_SIGNED
    assert "pxe.test" in again.san


def test_install_pem_requires_matching_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PXE_SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("PXE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PXE_PUBLIC_URL", "http://127.0.0.1:8080")
    from src.settings import clear_settings_cache

    clear_settings_cache()
    ensure_tls_material()
    cert_a, key_a = issue_self_signed_pem()
    _cert_b, key_b = issue_self_signed_pem()
    try:
        install_pem(cert_a, key_b)
        raise AssertionError("mismatched key should fail")
    except TlsError as exc:
        assert "does not match" in str(exc)
    install_pem(cert_a, key_a)
    info = load_tls_info()
    assert info is not None
    assert info.source == SOURCE_OPERATOR
    assert reload_path().is_file()


def test_install_rejects_encrypted_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PXE_SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("PXE_DATA_DIR", str(tmp_path))
    from src.settings import clear_settings_cache

    clear_settings_cache()
    cert_pem, key_pem = issue_self_signed_pem()
    key = serialization.load_pem_private_key(key_pem, password=None)
    encrypted = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"unit-test-secret"),
    )
    try:
        install_pem(cert_pem, encrypted)
        raise AssertionError("encrypted key should fail")
    except TlsError as exc:
        assert "unencrypted" in str(exc).lower()


def test_settings_https_section_and_pem_replace(client):
    login(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "HTTPS" in page.text
    assert "self-signed" in page.text
    assert "BEGIN PRIVATE" not in page.text
    assert "BEGIN RSA PRIVATE" not in page.text
    original = load_tls_info()
    assert original is not None
    cert_pem, key_pem = issue_self_signed_pem()
    replaced = client.post(
        "/settings/ssl",
        files={
            "cert_pem": ("fullchain.pem", cert_pem, "application/x-pem-file"),
            "key_pem": ("privkey.pem", key_pem, "application/x-pem-file"),
        },
        follow_redirects=False,
    )
    assert replaced.status_code in {302, 303}
    info = load_tls_info()
    assert info is not None
    assert info.source == SOURCE_OPERATOR
    assert info.fingerprint_sha256 != original.fingerprint_sha256
    again = client.get("/settings")
    assert "operator-provided PEM" in again.text
    assert cert_pem.decode() not in again.text
    assert key_pem.decode() not in again.text


def test_settings_rejects_mismatched_pem(client):
    login(client)
    client.get("/settings")
    cert_a, _key_a = issue_self_signed_pem()
    _cert_b, key_b = issue_self_signed_pem()
    response = client.post(
        "/settings/ssl",
        files={
            "cert_pem": ("tls.crt", cert_a, "application/x-pem-file"),
            "key_pem": ("tls.key", key_b, "application/x-pem-file"),
        },
    )
    assert response.status_code == 200
    assert "does not match" in response.text.lower()


def test_settings_regenerates_self_signed(client):
    login(client)
    client.get("/settings")
    before = load_tls_info()
    assert before is not None
    response = client.post("/settings/ssl/regenerate", follow_redirects=False)
    assert response.status_code in {302, 303}
    after = load_tls_info()
    assert after is not None
    assert after.source == SOURCE_SELF_SIGNED
    assert after.fingerprint_sha256 != before.fingerprint_sha256
