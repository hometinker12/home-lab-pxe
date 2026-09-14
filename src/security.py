"""Password hashing and Fernet helpers for local-account ciphertext."""

from __future__ import annotations

import os
import sys

from cryptography.fernet import Fernet
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

_INSECURE_ENCRYPTION_PLACEHOLDERS = frozenset(
    {
        "",
        "change-me-before-production",
        "replace-with-fernet-key",
    }
)

_fernet: Fernet | None = None


def allow_insecure_defaults() -> bool:
    forced = os.getenv("PXE_ALLOW_INSECURE_DEFAULTS", "").strip().lower()
    if forced in {"0", "false", "no"}:
        return False
    if forced in {"1", "true", "yes"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return True
    return False


def _require_encryption_key() -> str:
    candidate = (os.getenv("ENCRYPTION_KEY") or "").strip()
    if candidate in _INSECURE_ENCRYPTION_PLACEHOLDERS:
        if allow_insecure_defaults():
            return Fernet.generate_key().decode()
        raise RuntimeError(
            "ENCRYPTION_KEY must be set to a valid Fernet key before starting the application. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        Fernet(candidate.encode())
    except (ValueError, TypeError) as exc:
        if allow_insecure_defaults():
            return Fernet.generate_key().decode()
        raise RuntimeError(
            "ENCRYPTION_KEY is not a valid Fernet key. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc
    return candidate


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_require_encryption_key().encode())
    return _fernet


def reset_fernet_for_tests() -> None:
    global _fernet
    _fernet = None


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


DUMMY_PASSWORD_HASH = hash_password("pxe-dummy-password-for-timing")


def encrypt_value(value: str) -> str:
    return get_fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    return get_fernet().decrypt(value.encode()).decode()
