"""Allowlisted placeholder substitution for seed templates."""

from __future__ import annotations

import hashlib
import html
import inspect
import json
import re
import textwrap
import xml.etree.ElementTree as ET
from typing import Any

import yaml
from passlib.hash import sha512_crypt
from sqlmodel import Session

from .boot.uefi_order import inject_uefi_order_command, valid_policy_args
from .models import AccountKind, Image, InstallAttempt, Machine, OsFamily
from .seed_store import CRYPT_HASH_RE, SeedError, factory_seed_text, read_image_seed, read_machine_seed, read_seed
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
        "install_log_url",
        "timezone",
        "ssh_keys",
        "packages",
        "wim_index",
        "install_media_path",
        "source_id",
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


_SECRET_KEYS = (
    "password",
    "passwd",
    "hashed_password",
    "hashed_passwd",
    "hashed-passwd",
    "plain_text_passwd",
    "plain-text-passwd",
    "secret",
)
# A block-mapping key line, optionally a list item (``- key:``, ``- - key:``) and optionally quoted.
_SECRET_LINE_RE = re.compile(
    r"(?:-\s+)*([\"']?)(" + "|".join(re.escape(key) for key in _SECRET_KEYS) + r")\1\s*:",
    flags=re.IGNORECASE,
)
_SECRET_KEY_SET = frozenset(_SECRET_KEYS)
# Keys that may also hold a crypt hash (same rule as the cloud-init editor). Everything else is
# placeholder-only.
_HASH_KEYS = frozenset({"hashed_passwd", "hashed-passwd"})
_SENTINEL_PASSWORD = "PXE-SENTINEL-PASSWORD"


def _has_password_placeholder(text: str) -> bool:
    return "{{password" in text


def _is_crypt_hash(value: str) -> bool:
    return bool(CRYPT_HASH_RE.fullmatch(value.strip()))


def _line_scalar(rest: str) -> str:
    """Plain or quoted scalar after ``key:`` on one line, without a trailing comment."""
    text = re.split(r"\s+#", rest.strip(), maxsplit=1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _reject_flow_secrets(value: Any) -> None:
    """Catch flow-style mappings (``{name: a, plain_text_passwd: x}``) that the line check cannot see.

    Runs on the template parsed with placeholders swapped for sentinels, so a placeholder value
    shows up as its sentinel.
    """
    if isinstance(value, list):
        for item in value:
            _reject_flow_secrets(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if isinstance(key, str) and key.lower() in _SECRET_KEY_SET:
            if item is None or item == "":
                continue
            if isinstance(item, str) and key.lower() in _HASH_KEYS and _is_crypt_hash(item):
                continue
            if not (isinstance(item, str) and _SENTINEL_PASSWORD in item):
                raise SeedError("Credential fields must use placeholders")
            continue
        _reject_flow_secrets(item)


def _yaml_reject_literal_secrets(text: str) -> None:
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SECRET_LINE_RE.match(stripped)
        if not match or _has_password_placeholder(stripped):
            continue
        if match.group(2).lower() in _HASH_KEYS and _is_crypt_hash(_line_scalar(stripped[match.end() :])):
            continue
        raise SeedError("Credential fields must use placeholders")
    sentinels = {**dummy_values(), "password": _SENTINEL_PASSWORD, "password_hash": _SENTINEL_PASSWORD}
    try:
        parsed = yaml.safe_load(substitute_yaml(text, sentinels))
    except (yaml.YAMLError, SeedRenderError):
        return  # validate_seed_template reports invalid YAML / placeholders itself
    _reject_flow_secrets(parsed)


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
    """Best-effort empty POST; a failed callback must not abort Subiquity."""
    token = url if str(url).startswith("{{") else json.dumps(url)
    return f"wget -q --tries=3 --timeout=10 --post-file=/dev/null -O /dev/null {token} || true"


def _imaging_wget(url: str) -> str:
    return _shell_wget(url)


def _install_log_command(url: str) -> str:
    token = url if str(url).startswith("{{") else json.dumps(url)
    return (
        'sh -c \'log=/tmp/pxe-install-fail.log; : > "$log"; '
        'tail -c 49152 /var/log/installer/subiquity-server-debug.log >> "$log" 2>/dev/null || true; '
        'tail -c 16384 /var/log/installer/curtin-install.log >> "$log" 2>/dev/null || true; '
        f'wget -q --tries=3 --timeout=30 --post-file="$log" -O /dev/null {token} || true\''
    )


def _has_install_log(cmds: list) -> bool:
    blob = " ".join(_command_text(cmd) for cmd in cmds)
    return "install-log" in blob or "{{install_log_url}}" in blob


def _ensure_install_log_error_command(auto: dict, install_log_url: str) -> bool:
    url = install_log_url or "{{install_log_url}}"
    cmds = auto.get("error-commands")
    if not isinstance(cmds, list):
        auto["error-commands"] = [_install_log_command(url)]
        return True
    if _has_install_log(cmds):
        return False
    auto["error-commands"] = [*cmds, _install_log_command(url)]
    return True


# sysrq `s` and `u` only schedule work. `b` resets immediately, so the EFI
# system partition's FAT writes never hit the disk. `sync` waits until they do.
FORCE_REBOOT_CMD = "sh -c 'sync; sync; sleep 2; echo 1 > /proc/sys/kernel/sysrq; echo b > /proc/sysrq-trigger'"

# netplan apply runs `udevadm settle` with no timeout. A Wi-Fi NIC that is still
# probing (wlp*) keeps that queue busy, settle exits 1, and Subiquity aborts.
# Early-commands run before Network/apply_autoinstall_config. Unbind only
# interfaces that expose phy80211 so the PXE NIC (eno*/eth*) stays up.
QUIET_WIFI_CMD = (
    "sh -c 'for p in /sys/class/net/*; do "
    'n=$(basename "$p"); '
    '[ -e "$p/phy80211" ] || continue; '
    'dev=$(basename "$(readlink -f "$p/device" 2>/dev/null)" 2>/dev/null || true); '
    'drv=$(basename "$(readlink -f "$p/device/driver" 2>/dev/null)" 2>/dev/null || true); '
    'if [ -n "$dev" ] && [ -n "$drv" ] && [ -w "/sys/bus/pci/drivers/$drv/unbind" ]; then '
    'printf %s "$dev" > "/sys/bus/pci/drivers/$drv/unbind" || true; '
    'elif [ -n "$dev" ] && [ -n "$drv" ] && [ -w "/sys/bus/usb/drivers/$drv/unbind" ]; then '
    'printf %s "$dev" > "/sys/bus/usb/drivers/$drv/unbind" || true; '
    'else ip link set "$n" down || true; fi; '
    "done; true'"
)


def _has_force_reboot(cmds: list) -> bool:
    blob = " ".join(_command_text(cmd) for cmd in cmds)
    return "sysrq-trigger" in blob or "reboot -f" in blob


def _async_sysrq_sync(cmd: object) -> bool:
    """True when the command uses sysrq sync/remount, which do not wait."""
    text = _command_text(cmd)
    return "sysrq-trigger" in text and ("echo s >" in text or "echo u >" in text)


def _ensure_force_reboot_late_command(auto: dict) -> bool:
    """Casper NFS installs hang on a blank cursor if systemd waits to unmount nfsroot."""
    cmds = auto.get("late-commands")
    if not isinstance(cmds, list):
        auto["late-commands"] = [FORCE_REBOOT_CMD]
        return True
    changed = False
    rewritten: list = []
    for cmd in cmds:
        if _async_sysrq_sync(cmd):
            rewritten.append(FORCE_REBOOT_CMD)
            changed = True
        else:
            rewritten.append(cmd)
    if _has_force_reboot(rewritten):
        if changed:
            auto["late-commands"] = rewritten
        return changed
    auto["late-commands"] = [*rewritten, FORCE_REBOOT_CMD]
    return True


_SSH_KEY_LIST_KEYS = frozenset({"ssh_authorized_keys", "authorized-keys"})


def _drop_empty_ssh_key_lists(obj: Any) -> bool:
    """Cloud-init/Subiquity reject ssh_authorized_keys: [] (minItems: 1)."""
    changed = False
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            val = obj[key]
            if key in _SSH_KEY_LIST_KEYS and val == []:
                del obj[key]
                changed = True
            elif isinstance(val, (dict, list)) and _drop_empty_ssh_key_lists(val):
                changed = True
    elif isinstance(obj, list):
        for item in obj:
            if _drop_empty_ssh_key_lists(item):
                changed = True
    return changed


def _pop_parent_key_for_empty_block(lines: list[str], item_indent: str) -> None:
    """Drop `ssh_authorized_keys:` when its {{ssh_keys}} block is empty."""
    if not lines:
        return
    prev = lines[-1]
    newline = "\n" if prev.endswith("\n") else ""
    raw = prev[: -len(newline)] if newline else prev
    stripped = raw.strip()
    if stripped.startswith("#") or not re.fullmatch(r"[A-Za-z0-9_-]+:\s*", stripped):
        return
    prev_indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
    if len(prev_indent) < len(item_indent):
        lines.pop()


def _normalize_wget_empty_post(cmd: object) -> tuple[object, bool]:
    """Rewrite `--post-data=` (empty) to `--post-file=/dev/null` for GNU/BusyBox wget."""
    if isinstance(cmd, str):
        new = re.sub(r"--post-data=(?=\s|$)", "--post-file=/dev/null", cmd)
        return new, new != cmd
    if isinstance(cmd, list):
        out: list[object] = []
        changed = False
        for part in cmd:
            if part in {"--post-data=", "--post-data"}:
                out.append("--post-file=/dev/null")
                changed = True
            else:
                out.append(part)
        return out, changed
    return cmd, False


def _normalize_callback_wgets(auto: dict) -> bool:
    changed = False
    for key in ("early-commands", "late-commands"):
        cmds = auto.get(key)
        if not isinstance(cmds, list):
            continue
        rewritten: list[object] = []
        key_changed = False
        for cmd in cmds:
            new, did = _normalize_wget_empty_post(cmd)
            rewritten.append(new)
            key_changed = key_changed or did
        if key_changed:
            auto[key] = rewritten
            changed = True
    return changed


def _has_quiet_wifi(cmds: list) -> bool:
    blob = " ".join(_command_text(cmd) for cmd in cmds)
    return "phy80211" in blob


def _ensure_quiet_wifi_early_command(auto: dict) -> bool:
    """Drop live Wi-Fi NICs before Subiquity runs netplan apply."""
    cmds = auto.get("early-commands")
    if not isinstance(cmds, list):
        auto["early-commands"] = [QUIET_WIFI_CMD]
        return True
    if _has_quiet_wifi(cmds):
        return False
    auto["early-commands"] = [QUIET_WIFI_CMD, *cmds]
    return True


def silence_subiquity_client_source(text: str) -> str | None:
    """Drop Subiquity's yes/no prompt. The server already continues once apt starts."""
    import ast

    marker = "Add 'autoinstall' to your kernel command line"
    start = text.find("    async def noninteractive_confirmation(self):")
    if start < 0 or marker not in text:
        return None
    rest = text.find("\n    async def ", start + 1)
    if rest < 0:
        return None
    replacement = "    async def noninteractive_confirmation(self):\n        await self.confirm_install()\n"
    patched = text[:start] + replacement + text[rest + 1 :]
    try:
        ast.parse(patched)
    except SyntaxError:
        return None
    return patched


_CONFIRM_POLLER = r"""
import socket
import time
import urllib.parse

def post():
    query = urllib.parse.urlencode({"tty": "/dev/tty1"})
    req = (
        f"POST /meta/confirm?{query} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect("/run/subiquity/socket")
        sock.sendall(req)
        data = sock.recv(256)
    finally:
        sock.close()
    parts = data.split(None, 2)
    return len(parts) > 1 and parts[1].startswith(b"2")

for _ in range(5400):
    try:
        state = open("/run/subiquity/server-state").read().strip()
    except OSError:
        state = ""
    if state == "NEEDS_CONFIRMATION":
        try:
            if post():
                break
        except OSError:
            pass
    elif state in {"RUNNING", "LATE_COMMANDS", "DONE", "ERROR"}:
        break
    time.sleep(0.5)
"""


def autoinstall_confirm_program() -> str:
    """Python helper fetched by the early-command. Kept off the installer console."""
    fn = textwrap.dedent(inspect.getsource(silence_subiquity_client_source))
    poller = json.dumps(_CONFIRM_POLLER)
    runner = f"""
import pathlib
import subprocess

def patch_client():
    paths = list(pathlib.Path("/snap/subiquity").glob(
        "*/lib/python3.*/site-packages/subiquity/client/client.py"
    ))
    if not paths:
        return
    patched = silence_subiquity_client_source(paths[0].read_text(encoding="utf-8"))
    if not patched:
        return
    dest = pathlib.Path("/run/pxe-subiquity-client.py")
    dest.write_text(patched, encoding="utf-8")
    for path in paths:
        subprocess.run(["mount", "--bind", str(dest), str(path)], check=False)
    subprocess.run(
        ["systemctl", "restart", "snap.subiquity.subiquity-service"],
        check=False,
        timeout=25,
    )

def main():
    try:
        patch_client()
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["python3", "-c", {poller}],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

main()
"""
    return fn + "\n" + textwrap.dedent(runner)


def _autoinstall_confirm_command(public_url: str) -> str:
    base = (public_url or "{{public_url}}").rstrip("/")
    url = f"{base}/boot-files/autoinstall-confirm.py"
    token = url if url.startswith("{{") else json.dumps(url)
    return (
        "wget -q --tries=3 --timeout=20 -O /run/pxe-autoinstall-confirm.py "
        f"{token} && python3 /run/pxe-autoinstall-confirm.py || true"
    )


def _has_autoinstall_confirm(cmds: list) -> bool:
    blob = " ".join(_command_text(cmd) for cmd in cmds)
    return "autoinstall-confirm.py" in blob


def _is_inline_confirm_command(cmd: object) -> bool:
    text = _command_text(cmd)
    return "silence_subiquity_client_source" in text or ("python3 - << 'PY'" in text and "/meta/confirm" in text)


def _ensure_autoinstall_confirm_command(auto: dict, public_url: str) -> bool:
    """Subiquity's text client still asks for yes after apt configuration has started."""
    cmd = _autoinstall_confirm_command(public_url)
    cmds = auto.get("early-commands")
    if not isinstance(cmds, list):
        auto["early-commands"] = [cmd]
        return True
    kept = [item for item in cmds if not _is_inline_confirm_command(item)]
    if _has_autoinstall_confirm(kept):
        if kept != cmds:
            auto["early-commands"] = kept
            return True
        return False
    auto["early-commands"] = [cmd, *kept]
    return True


def _uefi_order_command(public_url: str, policy: str, machine_id: str) -> str:
    """Late-command that fetches and runs the BootOrder helper; always exits 0."""
    base = (public_url or "{{public_url}}").rstrip("/")
    url = f"{base}/boot-files/uefi-boot-order.py"
    token = url if url.startswith("{{") else json.dumps(url)
    return (
        "wget -q --tries=3 --timeout=20 -O /run/pxe-uefi-boot-order.py "
        f"{token} && python3 /run/pxe-uefi-boot-order.py {policy} {machine_id} || true"
    )


def _ensure_uefi_order_late_command(auto: dict, public_url: str, machine_id: str, policy: str) -> bool:
    """Run the BootOrder helper just before the forced reboot, after phone-home."""
    if not valid_policy_args(policy, machine_id):
        return False
    cmds = auto.get("late-commands")
    if not isinstance(cmds, list):
        cmds = []
    if any("uefi-boot-order.py" in _command_text(cmd) for cmd in cmds):
        return False
    cmd = _uefi_order_command(public_url, policy, machine_id)
    index = next((i for i, item in enumerate(cmds) if _has_force_reboot([item])), len(cmds))
    auto["late-commands"] = [*cmds[:index], cmd, *cmds[index:]]
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


def _ensure_source(auto: dict, source_id: str = "") -> bool:
    """Pin Subiquity source.id from the image catalog when the seed omits it."""
    existing = auto.get("source") if isinstance(auto.get("source"), dict) else {}
    current = str(existing.get("id") or "").strip()
    wanted = current or str(source_id or "").strip()
    merged = {**{"search_drivers": False}, **existing}
    if wanted:
        merged["id"] = wanted
    elif "id" in merged and not str(merged.get("id") or "").strip():
        merged.pop("id", None)
    if auto.get("source") == merged:
        return False
    auto["source"] = merged
    return True


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


def complete_linux_user_data(
    rendered: str,
    *,
    imaging_url: str = "",
    install_log_url: str = "",
    source_id: str = "",
    public_url: str = "",
    next_boot_device: str = "",
    machine_id: str = "",
) -> str:
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
    if _ensure_quiet_wifi_early_command(auto):
        changed = True
    if _ensure_imaging_early_command(auto, imaging_url):
        changed = True
    if _ensure_autoinstall_confirm_command(auto, public_url):
        changed = True
    if _ensure_install_log_error_command(auto, install_log_url):
        changed = True
    if _normalize_callback_wgets(auto):
        changed = True
    if _ensure_optional_catchall_nics(auto):
        changed = True
    if _drop_autoinstall_timezone(auto):
        changed = True
    if _ensure_source(auto, source_id):
        changed = True
    if _ensure_identity(auto):
        changed = True
    if _sanitize_identity_username(auto):
        changed = True
    if _ensure_force_reboot_late_command(auto):
        changed = True
    if _ensure_uefi_order_late_command(auto, public_url, machine_id, next_boot_device):
        changed = True
    if _drop_empty_ssh_key_lists(parsed):
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
        "install_log_url": "http://127.0.0.1:8080/api/machines/0/install-log",
        "timezone": "UTC",
        "ssh_keys": [],
        "packages": [],
        "wim_index": "1",
        "install_media_path": r"sources\install.wim",
        "source_id": "ubuntu-server",
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
                _pop_parent_key_for_empty_block(lines, indent)
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


_EMPTY_IMAGE_NAME = re.compile(
    r"\s*<MetaData\b[^>]*>\s*<Key>\s*/IMAGE/NAME\s*</Key>\s*<Value>\s*</Value>\s*</MetaData>",
    flags=re.IGNORECASE,
)


def drop_empty_image_name_metadata(xml_text: str) -> str:
    """Windows Setup treats INDEX+NAME as AND; drop NAME when {{source_id}} is empty."""
    return _EMPTY_IMAGE_NAME.sub("", xml_text or "")


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
    source_id = ""
    if attempt is not None and (attempt.source_id or "").strip():
        source_id = attempt.source_id.strip()
    elif image is not None:
        source_id = (image.source_id or "").strip()
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
        "install_log_url": f"{public}/api/machines/{machine.id}/install-log",
        "timezone": str(overlay.get("timezone") or "").strip() or default_timezone(db),
        "ssh_keys": [str(k).strip() for k in ssh_keys if str(k).strip()] if isinstance(ssh_keys, list) else [],
        "packages": [str(p).strip() for p in packages if str(p).strip()] if isinstance(packages, list) else [],
        "wim_index": wim_index,
        "install_media_path": install_media,
        "source_id": source_id,
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
            rendered = drop_empty_image_name_metadata(substitute_xml(text, values))
            ET.fromstring(rendered)
            rendered = inject_uefi_order_command(
                rendered,
                str(values.get("public_url") or ""),
                str(values.get("machine_id") or ""),
                str(machine.next_boot_device or ""),
            )
        else:
            rendered = substitute_yaml(text, values)
            if not machine_override:
                raw = str(overlay.get("raw_overlay") or "").strip()
                if raw:
                    rendered = rendered.rstrip() + "\n" + raw + "\n"
            rendered = complete_linux_user_data(
                rendered,
                imaging_url=str(values.get("imaging_url") or ""),
                install_log_url=str(values.get("install_log_url") or ""),
                source_id=str(values.get("source_id") or ""),
                public_url=str(values.get("public_url") or ""),
                next_boot_device=str(machine.next_boot_device or ""),
                machine_id=str(values.get("machine_id") or ""),
            )
            yaml.safe_load(rendered)
    except (SeedRenderError, ET.ParseError, yaml.YAMLError) as exc:
        raise SeedRenderError("seed_render_failed") from exc
    return rendered if rendered.endswith("\n") else rendered + "\n"
