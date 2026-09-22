from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = (tmp_path / "pxe.db").as_posix()
    tftp = tmp_path / "tftp"
    tftp.mkdir()
    (tftp / "wimboot").write_bytes(b"MZ" + b"\x00" * 2048)
    monkeypatch.setenv("PXE_ALLOW_INSECURE_DEFAULTS", "1")
    monkeypatch.setenv("PXE_RELAX_CSRF", "1")
    monkeypatch.setenv("PXE_DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("PXE_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path / "images"))
    monkeypatch.setenv("PXE_TFTP_ROOT", str(tftp))
    monkeypatch.setenv("PXE_SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("PXE_HTTPS_PORT", "8443")
    monkeypatch.setenv("PXE_PUBLIC_URL", "http://pxe.test:8080")
    monkeypatch.setenv("PXE_ENABLE_DHCP", "0")
    monkeypatch.setenv("SECRET_KEY", "pytest-secret-key-not-for-production")
    monkeypatch.setenv("PXE_SMB_HOST", "pxe.test")
    monkeypatch.setenv("PXE_SMB_USER", "pxemedia")
    monkeypatch.setenv("PXE_SMB_PASSWORD", "test-smb-password-ok")
    from src.auth import reset_serializer_for_tests
    from src.db import reset_engine_for_tests
    from src.security import reset_fernet_for_tests
    from src.settings import clear_settings_cache

    clear_settings_cache()
    reset_engine_for_tests()
    reset_fernet_for_tests()
    reset_serializer_for_tests()
    from src.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def login(client: TestClient) -> None:
    response = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert response.status_code in {302, 303}


def seed_url(client: TestClient, machine_id: int, family: str, leaf: str) -> str:
    instance_id = client.get(f"/api/machines/{machine_id}").json()["instance_id"]
    return f"/{family}/{machine_id}/{instance_id}/{leaf}"
