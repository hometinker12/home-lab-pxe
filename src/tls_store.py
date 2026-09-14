"""Self-signed and operator-provided TLS material for the HTTPS console."""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .settings import get_settings

LOGGER = logging.getLogger("home_lab_pxe")

CERT_NAME = "tls.crt"
KEY_NAME = "tls.key"
SOURCE_NAME = "tls.source"
RELOAD_NAME = "ssl.reload"
SOURCE_SELF_SIGNED = "self-signed"
SOURCE_OPERATOR = "operator"
MAX_PEM_BYTES = 256 * 1024
_CERT_DAYS = 825


class TlsError(ValueError):
    pass


@dataclass(frozen=True)
class TlsInfo:
    source: str
    subject: str
    issuer: str
    not_before: str
    not_after: str
    fingerprint_sha256: str
    san: tuple[str, ...]


def ssl_dir() -> Path:
    path = get_settings().ssl_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def cert_path() -> Path:
    return ssl_dir() / CERT_NAME


def key_path() -> Path:
    return ssl_dir() / KEY_NAME


def source_path() -> Path:
    return ssl_dir() / SOURCE_NAME


def reload_path() -> Path:
    path = get_settings().data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path / RELOAD_NAME


def console_https_url() -> str:
    settings = get_settings()
    parsed = urlparse(settings.public_url)
    host = parsed.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{settings.https_port}"


def request_https_reload() -> None:
    reload_path().write_text("reload\n", encoding="utf-8")


def load_tls_info() -> TlsInfo | None:
    cert_file = cert_path()
    key_file = key_path()
    if not cert_file.is_file() or not key_file.is_file():
        return None
    cert_pem = cert_file.read_bytes()
    key_pem = key_file.read_bytes()
    cert, _ = _parse_cert_and_key(cert_pem, key_pem)
    return _info_from_cert(cert, _read_source())


def ensure_tls_material() -> TlsInfo:
    ssl_dir()
    cert_file = cert_path()
    key_file = key_path()
    cert_exists = cert_file.is_file()
    key_exists = key_file.is_file()
    if cert_exists and key_exists:
        info = load_tls_info()
        if info is None:
            raise TlsError("TLS certificate files exist but could not be read")
        if not source_path().is_file():
            _write_source(SOURCE_OPERATOR)
            return load_tls_info() or info
        return info
    if cert_exists or key_exists:
        raise TlsError("TLS volume has an incomplete certificate/key pair")
    return generate_self_signed()


def generate_self_signed() -> TlsInfo:
    cert_pem, key_pem = issue_self_signed_pem()
    _write_pair(cert_pem, key_pem, SOURCE_SELF_SIGNED)
    LOGGER.info("generated self-signed TLS certificate at %s", cert_path())
    info = load_tls_info()
    if info is None:
        raise TlsError("failed to load the generated TLS certificate")
    return info


def install_pem(cert_pem: bytes, key_pem: bytes) -> TlsInfo:
    cert, key = _parse_cert_and_key(cert_pem, key_pem)
    normalized_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    bundle = cert_pem.strip() + b"\n"
    _write_pair(bundle, normalized_key, SOURCE_OPERATOR)
    LOGGER.info("installed operator TLS certificate subject=%s", cert.subject.rfc4514_string())
    request_https_reload()
    info = load_tls_info()
    if info is None:
        raise TlsError("failed to load the installed TLS certificate")
    return info


def read_pem_upload(data: bytes | None, *, label: str) -> bytes:
    if not data:
        raise TlsError(f"{label} file is required")
    if len(data) > MAX_PEM_BYTES:
        raise TlsError(f"{label} file is too large")
    text = data.strip()
    if not text:
        raise TlsError(f"{label} file is empty")
    return text + b"\n"


def issue_self_signed_pem() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "home-lab-pxe")])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=_CERT_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(_san_names()), critical=False)
    )
    cert = builder.sign(key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _san_names() -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName("home-lab-pxe"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    parsed = urlparse(get_settings().public_url)
    host = (parsed.hostname or "").strip()
    if not host or host in {"localhost", "127.0.0.1", "home-lab-pxe"}:
        return names
    try:
        names.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        names.append(x509.DNSName(host[:255]))
    return names


def _parse_cert_and_key(cert_pem: bytes, key_pem: bytes):
    try:
        certs = x509.load_pem_x509_certificates(cert_pem)
    except ValueError as exc:
        raise TlsError("certificate file must be a PEM X.509 certificate") from exc
    if not certs:
        raise TlsError("certificate file did not contain a PEM certificate")
    cert = certs[0]
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except TypeError as exc:
        raise TlsError("private key must be an unencrypted PEM file") from exc
    except ValueError as exc:
        raise TlsError("key file must be a PEM private key") from exc
    try:
        public = key.public_key()
    except AttributeError as exc:
        raise TlsError("private key type is not supported") from exc
    if _spki(cert.public_key()) != _spki(public):
        raise TlsError("certificate does not match the private key")
    return cert, key


def _spki(public_key) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _info_from_cert(cert: x509.Certificate, source: str) -> TlsInfo:
    san: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san = [name.value if isinstance(name.value, str) else str(name.value) for name in ext.value]
    except x509.ExtensionNotFound:
        pass
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    grouped = ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2))
    return TlsInfo(
        source=source,
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        not_before=_fmt_time(cert.not_valid_before_utc),
        not_after=_fmt_time(cert.not_valid_after_utc),
        fingerprint_sha256=grouped,
        san=tuple(san),
    )


def _fmt_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _read_source() -> str:
    try:
        raw = source_path().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return SOURCE_OPERATOR
    if raw in {SOURCE_SELF_SIGNED, SOURCE_OPERATOR}:
        return raw
    return SOURCE_OPERATOR


def _write_source(source: str) -> None:
    source_path().write_text(source + "\n", encoding="utf-8")


def _write_pair(cert_pem: bytes, key_pem: bytes, source: str) -> None:
    ssl_dir()
    _atomic_write(cert_path(), cert_pem, 0o644)
    _atomic_write(key_path(), key_pem, 0o600)
    _write_source(source)


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass
