"""Allowlisted placeholder substitution for seed templates."""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import yaml
from passlib.hash import sha512_crypt
from sqlmodel import Session

from .models import AccountKind, Image, InstallAttempt, Machine, OsFamily
from .seed_store import SeedError, factory_seed_text, read_image_seed, read_machine_seed, read_seed
from .settings import get_settings

TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")
ALLOWED_TOKENS = frozenset(
    {
        "hostname",
        "username",
        "password",
        "password_hash",
        "instance_id",
        "machine_id",
        "public_url",
        "phone_home_url",
        "imaging_url",
        "timezone",
        "ssh_keys",
        "packages",
        "wim_index",
        "install_media_path",
    }
)
BLOCK_TOKENS = frozenset({"ssh_keys", "packages"})
_SALT_ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class SeedRenderError(ValueError):
    pass


def validate_tokens(text: str) -> None:
    for match in TOKEN_RE.finditer(text or ""):
        name = match.group(1)
        if name not in ALLOWED_TOKENS:
            raise SeedError(f"Unknown placeholder {{{{{name}}}}}")


def _stable_salt(instance_id: str) -> str:
    digest = hashlib.sha256(f"pxe-sha512-salt:{instance_id}".encode()).digest()
    return "".join(_SALT_ALPHABET[byte % len(_SALT_ALPHABET)] for byte in digest[:16])


def password_hash_for(password: str, instance_id: str) -> str:
    return sha512_crypt.using(salt=_stable_salt(instance_id), rounds=5000).hash(password)


def _yaml_reject_literal_secrets(text: str) -> None:
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"(password|passwd|hashed_password|secret)\s*:", stripped, flags=re.IGNORECASE)
        if match and "{{password" not in stripped and "{{password_hash}}" not in stripped:
            raise SeedError("Credential fields must use placeholders")


def _xml_reject_literal_secrets(text: str) -> None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SeedError("unattend.xml is not valid XML") from exc
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in {"password", "administratorpassword", "value"} and (node.text or "").strip():
            if tag == "value":
                parent = _parent_tag(root, node)
                if parent not in {"password", "administratorpassword"}:
                    continue
            if "{{" not in (node.text or ""):
                raise SeedError("Credential fields must use placeholders")
    if (
        root.find(".//{urn:schemas-microsoft-com:unattend}ImageInstall") is None
        and root.find(".//ImageInstall") is None
    ):
        raise SeedError("unattend.xml must include ImageInstall")
    if (
        root.find(".//{urn:schemas-microsoft-com:unattend}AdministratorPassword") is None
        and root.find(".//AdministratorPassword") is None
    ):
        raise SeedError("unattend.xml must include AdministratorPassword")


def _parent_tag(root: ET.Element, target: ET.Element) -> str:
    for parent in root.iter():
        if target in list(parent):
            return parent.tag.rsplit("}", 1)[-1].lower()
    return ""


def validate_seed_template(text: str, os_family: OsFamily | str) -> None:
    if not (text or "").strip():
        raise SeedError("Seed file cannot be empty")
    validate_tokens(text)
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    if family == OsFamily.windows.value:
        _xml_reject_literal_secrets(text)
        dummy = substitute_xml(text, dummy_values())
        try:
            ET.fromstring(dummy)
        except ET.ParseError as exc:
            raise SeedError("unattend.xml is not valid XML") from exc
        return
    _yaml_reject_literal_secrets(text)
    rendered = substitute_yaml(text, dummy_values())
    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        raise SeedError("user-data is not valid YAML") from exc
    if parsed is not None and not isinstance(parsed, dict):
        raise SeedError("user-data must be a YAML mapping")


AUTOINSTALL_UNATTENDED = {
    "locale": "en_US.UTF-8",
    "keyboard": {"layout": "us"},
    "refresh-installer": {"update": False},
    "source": {"search_drivers": False},
    "network": {
        "version": 2,
        "ethernets": {
            "zz-all-en": {"match": {"name": "en*"}, "dhcp4": True, "optional": True},
            "zz-all-eth": {"match": {"name": "eth*"}, "dhcp4": True, "optional": True},
        },
    },
    "apt": {
        "preserve_sources_list": False,
        "geoip": False,
        "fallback": "offline-install",
        "mirror-selection": {
            "primary": [
                {"uri": "http://archive.ubuntu.com/ubuntu", "arches": ["amd64", "i386"]},
                {
                    "uri": "http://ports.ubuntu.com/ubuntu-ports",
                    "arches": ["arm64", "armhf", "ppc64el", "riscv64", "s390x"],
                },
            ]
        },
    },
    "storage": {"layout": {"name": "lvm", "sizing-policy": "all", "match": {"size": "largest"}}},
    "shutdown": "reboot",
    "updates": "security",
}

# Subiquity identity cannot create reserved system accounts (root, nobody, …).
_RESERVED_IDENTITY_USERS = frozenset(
    {
        "root",
        "daemon",
        "bin",
        "sys",
        "sync",
        "games",
        "man",
        "lp",
        "mail",
        "news",
        "uucp",
        "proxy",
        "www-data",
        "backup",
        "list",
        "nobody",
        "sshd",
        "_apt",
        "messagebus",
        "systemd-network",
        "systemd-resolve",
        "uuidd",
        "dnsmasq",
        "lxd",
        "tcpdump",
        "landscape",
        "pollinate",
        "tss",
    }
)
_IDENTITY_FALLBACK_USER = "ubuntu"


def _full_line_comments(text: str) -> list[str]:
    comments: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and stripped != "#cloud-config":
            comments.append(stripped)
    return comments


def _with_cloud_config_and_comments(dumped: str, comments: list[str]) -> str:
    body = dumped.lstrip("\n")
    if not body.lstrip().startswith("#cloud-config"):
        body = "#cloud-config\n" + body
    if comments:
        lines = body.splitlines()
        out: list[str] = []
        inserted = False
        for line in lines:
            out.append(line)
            if not inserted and line.strip() == "#cloud-config":
                out.extend(comments)
                inserted = True
        if not inserted:
            out = ["#cloud-config", *comments, *out]
        body = "\n".join(out) + "\n"
    elif not body.endswith("\n"):
        body += "\n"
    return body


def _command_text(cmd: object) -> str:
    if isinstance(cmd, list):
        return " ".join(str(part) for part in cmd)
    return str(cmd)


def _has_imaging_callback(cmds: list) -> bool:
    return any("event=imaging" in _command_text(cmd) or "{{imaging_url}}" in _command_text(cmd) for cmd in cmds)


def _shell_wget(url: str) -> str:
    """Best-effort POST; a failed callback must not abort Subiquity."""
    token = url if str(url).startswith("{{") else json.dumps(url)
    return f"wget -q --tries=3 --timeout=10 --post-data= -O /dev/null {token} || true"


def _imaging_wget(url: str) -> str:
    return _shell_wget(url)


FORCE_REBOOT_CMD = (
    "sh -c 'sleep 1; echo 1 > /proc/sys/kernel/sysrq; "
    "echo s > /proc/sysrq-trigger; echo u > /proc/sysrq-trigger; "
    "echo b > /proc/sysrq-trigger'"
)


def _has_force_reboot(cmds: list) -> bool:
    blob = " ".join(_command_text(cmd) for cmd in cmds)
    return "sysrq-trigger" in blob or "reboot -f" in blob


def _ensure_force_reboot_late_command(auto: dict) -> bool:
    """Casper NFS installs hang on a blank cursor if systemd waits to unmount nfsroot."""
    cmds = auto.get("late-commands")
    if not isinstance(cmds, list):
        auto["late-commands"] = [FORCE_REBOOT_CMD]
        return True
    if _has_force_reboot(cmds):
        return False
    auto["late-commands"] = [*cmds, FORCE_REBOOT_CMD]
    return True


def _ensure_imaging_early_command(auto: dict, imaging_url: str) -> bool:
    url = imaging_url or "{{imaging_url}}"
    cmds = auto.get("early-commands")
    if not isinstance(cmds, list):
        auto["early-commands"] = [_imaging_wget(url)]
        return True
    if _has_imaging_callback(cmds):
        return False
    auto["early-commands"] = [_imaging_wget(url), *cmds]
    return True


def _ensure_optional_catchall_nics(auto: dict) -> bool:
    """Catch-all en*/eth* match blocks must be optional or first boot waits on cloud-init-network."""
    net = auto.get("network")
    if not isinstance(net, dict):
        return False
    ethernets = net.get("ethernets")
    if not isinstance(ethernets, dict):
        return False
    changed = False
    for cfg in ethernets.values():
        if not isinstance(cfg, dict):
            continue
        match = cfg.get("match")
        if not isinstance(match, dict) or not isinstance(match.get("name"), str):
            continue
        if "optional" not in cfg:
            cfg["optional"] = True
            changed = True
    return changed


def _drop_autoinstall_timezone(auto: dict) -> bool:
    """Timezone belongs in cloud-init user-data, not the autoinstall root."""
    if "timezone" not in auto:
        return False
    del auto["timezone"]
    return True


def _safe_identity_username(name: str) -> str:
    cleaned = str(name or "").strip()
    if not cleaned or "{{" in cleaned:
        return cleaned
    if cleaned.lower() in _RESERVED_IDENTITY_USERS:
        return _IDENTITY_FALLBACK_USER
    return cleaned


def _sanitize_identity_username(auto: dict) -> bool:
    """Subiquity rejects identity.username values reserved by the OS (including root)."""
    existing = auto.get("identity") if isinstance(auto.get("identity"), dict) else None
    if existing is None:
        return False
    current = str(existing.get("username") or "").strip()
    safe = _safe_identity_username(current)
    if not safe or safe == current:
        return False
    existing["username"] = safe
    return True


def _identity_from_user_data(auto: dict) -> dict[str, str]:
    ud = auto.get("user-data") if isinstance(auto.get("user-data"), dict) else {}
    hostname = str(ud.get("hostname") or "").strip()
    username = ""
    password = ""
    chpasswd = ud.get("chpasswd")
    if isinstance(chpasswd, dict):
        users = chpasswd.get("users")
        if isinstance(users, list):
            for item in users:
                if isinstance(item, dict) and item.get("name") and item.get("password"):
                    username = str(item.get("name")).strip()
                    password = str(item.get("password")).strip()
                    break
    return {"hostname": hostname, "username": username, "password": password}


def _ensure_identity(auto: dict) -> bool:
    """Subiquity needs identity or the installer prompts for the first user."""
    existing = auto.get("identity") if isinstance(auto.get("identity"), dict) else {}
    if all(str(existing.get(key) or "").strip() for key in ("hostname", "username", "password")):
        return False
    found = _identity_from_user_data(auto)
    merged = {
        "hostname": str(existing.get("hostname") or found.get("hostname") or "").strip(),
        "username": _safe_identity_username(str(existing.get("username") or found.get("username") or "").strip()),
        "password": str(existing.get("password") or found.get("password") or "").strip(),
    }
    if not all(merged.values()):
        return False
    auto["identity"] = {**existing, **merged}
    return True


def complete_linux_user_data(rendered: str, *, imaging_url: str = "") -> str:
    """Fill missing Subiquity autoinstall keys so NFS installs stay non-interactive."""
    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError:
        return rendered
    if not isinstance(parsed, dict):
        return rendered
    auto = parsed.get("autoinstall")
    if not isinstance(auto, dict):
        return rendered
    changed = False
    for key, value in AUTOINSTALL_UNATTENDED.items():
        if key not in auto:
            auto[key] = value
            changed = True
        elif key == "apt" and isinstance(value, dict) and isinstance(auto.get("apt"), dict):
            for apt_key, apt_val in value.items():
                if apt_key not in auto["apt"]:
                    auto["apt"][apt_key] = apt_val
                    changed = True
    if _ensure_imaging_early_command(auto, imaging_url):
        changed = True
    if _ensure_optional_catchall_nics(auto):
        changed = True
    if _drop_autoinstall_timezone(auto):
        changed = True
    if _ensure_identity(auto):
        changed = True
    if _sanitize_identity_username(auto):
        changed = True
    if _ensure_force_reboot_late_command(auto):
        changed = True
    if not changed:
        return rendered if rendered.endswith("\n") else rendered + "\n"
    parsed["autoinstall"] = auto
    dumped = yaml.safe_dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return _with_cloud_config_and_comments(dumped, _full_line_comments(rendered))


def dummy_values() -> dict[str, Any]:
    return {
        "hostname": "dummyhost",
        "username": "dummyuser",
        "password": "DUMMY_PASSWORD_PLACEHOLDER",
        "password_hash": "$6$rounds=5000$dummysalt$dummyhash",
        "instance_id": "dummyinstance",
        "machine_id": "0",
        "public_url": "http://127.0.0.1:8080",
        "phone_home_url": "http://127.0.0.1:8080/api/machines/0/events",
        "imaging_url": "http://127.0.0.1:8080/api/machines/0/events?event=imaging",
        "timezone": "UTC",
        "ssh_keys": [],
        "packages": [],
        "wim_index": "1",
        "install_media_path": r"sources\install.wim",
    }


def substitute_yaml(template: str, values: dict[str, Any]) -> str:
    lines: list[str] = []
    for line in (template or "").splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        raw = line[:-1] if newline else line
        stripped = raw.strip()
        block = re.fullmatch(r"\{\{(ssh_keys|packages)\}\}", stripped)
        if block:
            indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
            items = values.get(block.group(1)) or []
            if not isinstance(items, list) or not items:
                lines.append(f"{indent}[]{newline}")
                continue
            for item in items:
                lines.append(f"{indent}- {json.dumps(str(item))}{newline}")
            continue

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in ALLOWED_TOKENS:
                raise SeedRenderError("Unknown placeholder")
            if name in BLOCK_TOKENS:
                raise SeedRenderError("List placeholders must occupy their own line")
            return json.dumps(values.get(name, ""))

        lines.append(TOKEN_RE.sub(repl, raw) + newline)
    return "".join(lines)


def substitute_xml(template: str, values: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in ALLOWED_TOKENS:
            raise SeedRenderError("Unknown placeholder")
        return html.escape(str(values.get(name, "")), quote=True)

    return TOKEN_RE.sub(repl, template or "")


def select_seed_text(image: Image | None, machine: Machine, os_family: OsFamily | str) -> str:
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    if machine.id:
        machine_text = read_machine_seed(int(machine.id), family)
        if machine_text.strip():
            return machine_text
    if image is not None and image.id:
        image_text = read_image_seed(int(image.id), family)
        if image_text.strip():
            return image_text
    return factory_seed_text(family)


def placeholder_values(
    db: Session,
    machine: Machine,
    image: Image | None,
    attempt: InstallAttempt | None = None,
) -> dict[str, Any]:
    from .inventory.service import load_overlay, resolve_local_account

    overlay = load_overlay(machine.guest_overlay)
    family = OsFamily((attempt.os_family if attempt else None) or (image.os_family if image else OsFamily.linux.value))
    kind = AccountKind.windows_administrator if family == OsFamily.windows else AccountKind.linux_root
    creds = resolve_local_account(db, int(machine.id), kind)
    username = ""
    password = ""
    if creds:
        username, password = creds
    elif family == OsFamily.windows:
        username = "Administrator"
    else:
        username = "root"
    hostname = (machine.hostname or str(overlay.get("hostname") or "")).strip()
    if family == OsFamily.windows:
        hostname = (hostname or f"PXE{machine.id}")[:15]
    else:
        hostname = hostname or f"pxe-{machine.id}"
    settings = get_settings()
    public = settings.public_url
    install_media = ""
    if attempt is not None and attempt.install_wim_path:
        name = attempt.install_wim_path.replace("\\", "/").rsplit("/", 1)[-1]
        install_media = f"sources\\{name}" if name else r"sources\install.wim"
    elif image is not None and image.install_wim_path:
        name = image.install_wim_path.replace("\\", "/").rsplit("/", 1)[-1]
        install_media = f"sources\\{name}" if name else r"sources\install.wim"
    else:
        install_media = r"sources\install.wim"
    wim_index = str((attempt.wim_index if attempt is not None else None) or (image.wim_index if image else 1) or 1)
    packages = overlay.get("packages") or []
    ssh_keys = overlay.get("ssh_keys") or []
    from .dhcp_runtime import default_timezone

    return {
        "hostname": hostname,
        "username": username,
        "password": password,
        "password_hash": password_hash_for(password, machine.instance_id) if password else "",
        "instance_id": machine.instance_id,
        "machine_id": str(machine.id),
        "public_url": public,
        "phone_home_url": f"{public}/api/machines/{machine.id}/events",
        "imaging_url": f"{public}/api/machines/{machine.id}/events?event=imaging",
        "timezone": str(overlay.get("timezone") or "").strip() or default_timezone(db),
        "ssh_keys": [str(k).strip() for k in ssh_keys if str(k).strip()] if isinstance(ssh_keys, list) else [],
        "packages": [str(p).strip() for p in packages if str(p).strip()] if isinstance(packages, list) else [],
        "wim_index": wim_index,
        "install_media_path": install_media,
    }


def render_selected_seed(
    db: Session,
    machine: Machine,
    *,
    attempt: InstallAttempt | None = None,
) -> str:
    from .inventory.service import get_image, load_overlay

    image = get_image(db, machine.assigned_image_id)
    family = OsFamily((attempt.os_family if attempt else None) or (image.os_family if image else OsFamily.linux.value))
    if attempt is not None and attempt.seed_snapshot_path:
        text = read_seed(get_settings().data_dir, attempt.seed_snapshot_path)
    else:
        text = select_seed_text(image, machine, family)
    overlay = load_overlay(machine.guest_overlay)
    machine_override = bool(machine.id and read_machine_seed(int(machine.id), family).strip())
    if not text.strip():
        text = factory_seed_text(family)
    values = placeholder_values(db, machine, image, attempt)
    try:
        if family == OsFamily.windows:
            rendered = substitute_xml(text, values)
            ET.fromstring(rendered)
        else:
            rendered = substitute_yaml(text, values)
            if not machine_override:
                raw = str(overlay.get("raw_overlay") or "").strip()
                if raw:
                    rendered = rendered.rstrip() + "\n" + raw + "\n"
            rendered = complete_linux_user_data(rendered, imaging_url=str(values.get("imaging_url") or ""))
            yaml.safe_load(rendered)
    except (SeedRenderError, ET.ParseError, yaml.YAMLError) as exc:
        raise SeedRenderError("seed_render_failed") from exc
    return rendered if rendered.endswith("\n") else rendered + "\n"
