from cryptography.fernet import Fernet

from src.security import allow_insecure_defaults, decrypt_value, encrypt_value


def test_fernet_round_trip():
    token = encrypt_value("root-password")
    assert token != "root-password"
    assert decrypt_value(token) == "root-password"


def test_password_not_in_login_html(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert "secret" not in page.text


def test_unauthenticated_machines_json(client):
    response = client.get("/api/machines")
    assert response.status_code == 401


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_allow_insecure_defaults_in_pytest():
    assert allow_insecure_defaults() is True


def test_placeholder_key_rejected_outside_tests(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PXE_ALLOW_INSECURE_DEFAULTS", "0")
    monkeypatch.setenv("ENCRYPTION_KEY", "replace-with-fernet-key")
    from src import security

    security.reset_fernet_for_tests()
    try:
        raised = False
        try:
            security._require_encryption_key()
        except RuntimeError:
            raised = True
        assert raised
    finally:
        monkeypatch.setenv("PXE_ALLOW_INSECURE_DEFAULTS", "1")
        security.reset_fernet_for_tests()
        Fernet.generate_key()
