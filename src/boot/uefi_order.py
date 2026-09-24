"""UEFI BootOrder policy applied from inside the installed guest.

PXE cannot change firmware boot order, so after an install a small helper runs in the guest and
rewrites BootOrder (Linux: ``efibootmgr``; Windows: ``bcdedit /enum firmware``) for the machine's
``pxe`` or ``disk`` policy.

The classification and ordering functions below are pure and stdlib-only (``re``) so their source can be
shipped verbatim inside the served Linux helper via :func:`inspect.getsource`. The PowerShell helper uses
regexes generated from the same keyword constants so the two cannot drift.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import re
import shutil
import subprocess
import textwrap
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

POLICIES = ("pxe", "disk")

NETWORK_WORDS = ("PXE", "HTTP", "IPv4", "IPv6", "Network", "LAN", "Ethernet", "NIC")
DISK_WORDS = ("Windows Boot Manager", "ubuntu", "debian", "rhel", "grub", "shim")
DEVICE_PATH_TOKENS = (
    "HD",
    "PciRoot",
    "MAC",
    "Uri",
    "VenHw",
    "FvVol",
    "BBS",
    "Fv",
    "VenMsg",
    "Acpi",
    "USB",
    "Sata",
)

EFI_DIR_ENV = "PXE_UEFI_ORDER_EFI_DIR"
DEFAULT_EFI_DIR = "/sys/firmware/efi"


def word_pattern(words):
    """Case-sensitivity-free alternation with word boundaries; valid in Python ``re`` and .NET."""
    return r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b"


def classify(description, device_path=""):
    """Return ``network``, ``disk`` or ``other``. Network wins: some PXE device paths contain ``HD(``."""
    desc = description or ""
    path = device_path or ""
    if re.search(word_pattern(NETWORK_WORDS), desc, re.IGNORECASE) or "MAC(" in path or "Uri(" in path:
        return "network"
    if re.search(word_pattern(DISK_WORDS), desc, re.IGNORECASE) or "HD(" in path:
        return "disk"
    return "other"


def is_ipv4_network(description):
    """IPv4 PXE entry: mentions IPv4 or PXE, and is neither IPv6 nor HTTP boot."""
    desc = description or ""

    def has(word):
        return re.search(word_pattern((word,)), desc, re.IGNORECASE) is not None

    if has("IPv6") or has("HTTP"):
        return False
    return has("IPv4") or has("PXE")


def reorder(entries, policy, current=None):
    """New BootOrder ids, or ``None`` when nothing should change.

    ``entries`` are ``(id, description)`` or ``(id, description, device_path)`` tuples in current BootOrder
    order. Firmware relative order is kept within each bucket.

    ``pxe``: current (if network), IPv4 network, disk, other network (IPv6/HTTP), other. The iPXE menu's
    "continue" exits back to firmware, which tries the next BootOrder entry, so disk must follow IPv4 PXE.
    ``disk``: disk, other, network.
    """
    if policy not in ("pxe", "disk"):
        return None
    items = []
    for entry in entries:
        boot_id = str(entry[0]).upper()
        desc = entry[1] if len(entry) > 1 else ""
        path = entry[2] if len(entry) > 2 else ""
        kind = classify(desc, path)
        items.append((boot_id, kind, kind == "network" and is_ipv4_network(desc)))
    if not any(kind == "network" for _, kind, _ in items):
        return None
    cur = str(current).upper() if current else None
    if policy == "pxe":
        pick = next((i for i, item in enumerate(items) if item[0] == cur and item[1] == "network"), None)
        first = [items[pick]] if pick is not None else []
        rest = [item for i, item in enumerate(items) if i != pick]
        new = (
            first
            + [item for item in rest if item[1] == "network" and item[2]]
            + [item for item in rest if item[1] == "disk"]
            + [item for item in rest if item[1] == "network" and not item[2]]
            + [item for item in rest if item[1] == "other"]
        )
    else:
        new = (
            [item for item in items if item[1] == "disk"]
            + [item for item in items if item[1] == "other"]
            + [item for item in items if item[1] == "network"]
        )
    order = [item[0] for item in new]
    if order == [item[0] for item in items]:
        return None
    return order


def _split_boot_entry(rest):
    """Split ``desc<TAB>path`` (old efibootmgr) or ``desc  HD(...)`` (efibootmgr v18+)."""
    if "\t" in rest:
        desc, path = rest.split("\t", 1)
        return desc.strip(), path.strip()
    tokens = "|".join(re.escape(token) for token in DEVICE_PATH_TOKENS)
    match = re.search(r"\s{2,}(?=(?:" + tokens + r")\()", rest)
    if match:
        return rest[: match.start()].strip(), rest[match.end() :].strip()
    return rest.strip(), ""


def parse_efibootmgr(text):
    """Parse ``efibootmgr -v`` output into ``(boot_order, current, entries)``.

    ``entries`` are ``(id, description, device_path)`` in listing order; ids are upper-case hex.
    Continuation lines (``dp:``/``data:`` in v18+) and unknown lines are ignored.
    """
    boot_order = []
    current = None
    entries = []
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        match = re.match(r"^BootCurrent:\s*([0-9A-Fa-f]{4})\s*$", line)
        if match:
            current = match.group(1).upper()
            continue
        match = re.match(r"^BootOrder:\s*(.*)$", line)
        if match:
            boot_order = [part.strip().upper() for part in match.group(1).split(",") if part.strip()]
            continue
        match = re.match(r"^Boot([0-9A-Fa-f]{4})\*?\s(.*)$", line)
        if match:
            desc, path = _split_boot_entry(match.group(2))
            entries.append((match.group(1).upper(), desc, path))
    return boot_order, current, entries


def plan_efibootmgr(text, policy):
    """New BootOrder ids for ``efibootmgr -v`` output, or ``None``. Unlisted BootOrder ids count as other."""
    boot_order, current, entries = parse_efibootmgr(text)
    if not boot_order:
        return None
    known = {entry[0]: entry for entry in entries}
    ordered = [known.get(boot_id, (boot_id, "", "")) for boot_id in boot_order]
    return reorder(ordered, policy, current)


# --- Linux guest runtime (shipped verbatim in the helper program) ---------------------------------------


def _efibootmgr_prefix():
    exe = shutil.which("efibootmgr")
    if exe:
        return [exe]
    curtin = shutil.which("curtin")
    if curtin:
        return [curtin, "in-target", "--target=/target", "--", "efibootmgr"]
    return None


def _confirm_pxe(public_url, machine_id):
    url = public_url + "/api/machines/" + str(machine_id) + "/events"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status == 200
        except urllib.error.HTTPError as exc:
            return exc.code == 409
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return False


def main(public_url, policy, machine_id):
    """Apply ``policy`` to UEFI BootOrder. Never raises; the caller always exits 0."""
    if policy not in ("pxe", "disk") or not str(machine_id).isdigit():
        return
    if not os.path.isdir(os.environ.get(EFI_DIR_ENV) or DEFAULT_EFI_DIR):
        return
    prefix = _efibootmgr_prefix()
    if prefix is None:
        return
    listing = subprocess.run(prefix + ["-v"], capture_output=True, text=True, timeout=60, check=False)
    if listing.returncode != 0:
        return
    if policy == "pxe" and not _confirm_pxe(public_url, machine_id):
        return
    order = plan_efibootmgr(listing.stdout, policy)
    if order is None:
        return
    subprocess.run(prefix + ["-o", ",".join(order)], capture_output=True, timeout=60, check=False)


def _cli(public_url, argv):
    try:
        if len(argv) >= 2:
            main(public_url, argv[0], argv[1])
    except BaseException:
        pass
    return 0


_LINUX_HELPER_FUNCTIONS = (
    word_pattern,
    classify,
    is_ipv4_network,
    reorder,
    _split_boot_entry,
    parse_efibootmgr,
    plan_efibootmgr,
    _efibootmgr_prefix,
    _confirm_pxe,
    main,
    _cli,
)


def linux_helper_program(public_url: str) -> str:
    """Standalone Python 3 program: ``python3 uefi-boot-order.py <pxe|disk> <machine_id>``. Always exits 0."""
    header = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "import os",
            "import re",
            "import shutil",
            "import subprocess",
            "import sys",
            "import time",
            "import urllib.error",
            "import urllib.request",
            "",
            f"PUBLIC_URL = {json.dumps((public_url or '').rstrip('/'))}",
            f"NETWORK_WORDS = {NETWORK_WORDS!r}",
            f"DISK_WORDS = {DISK_WORDS!r}",
            f"DEVICE_PATH_TOKENS = {DEVICE_PATH_TOKENS!r}",
            f"EFI_DIR_ENV = {EFI_DIR_ENV!r}",
            f"DEFAULT_EFI_DIR = {DEFAULT_EFI_DIR!r}",
        ]
    )
    body = "\n\n".join(textwrap.dedent(inspect.getsource(fn)) for fn in _LINUX_HELPER_FUNCTIONS)
    footer = 'if __name__ == "__main__":\n    sys.exit(_cli(PUBLIC_URL, sys.argv[1:]))\n'
    return header + "\n\n\n" + body + "\n\n" + footer


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_WINDOWS_TEMPLATE = r"""# home-lab-pxe: apply UEFI boot order policy (pxe|disk) via the firmware boot manager.
param([string]$Policy = '', [string]$MachineId = '')

$PxeBase = __BASE__
$NetworkPattern = __NETWORK__
$DiskPattern = __DISK__
$Ipv4Pattern = __IPV4__
$Ipv6Pattern = __IPV6__
$PxePattern = __PXE__
$HttpPattern = __HTTP__

function Invoke-Bcd {
    param([string[]]$BcdArgs)
    & bcdedit.exe @BcdArgs
}

function Invoke-PxeGate {
    param([string]$Uri)
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $result = Invoke-PxeGateOnce -Uri $Uri
        if ($null -ne $result) { return $result }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Invoke-PxeGateOnce {
    param([string]$Uri)
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $Uri -TimeoutSec 15
        return ([int]$r.StatusCode -eq 200)
    } catch [System.Net.WebException] {
        if ($null -eq $_.Exception.Response) { return $null }
        $code = [int]$_.Exception.Response.StatusCode
        return ($code -eq 200 -or $code -eq 409)
    } catch {
        try {
            $code = [int]$_.Exception.Response.StatusCode
            return ($code -eq 409)
        } catch {
            return $false
        }
    }
}

function Get-EntryKind {
    param([string]$Id, [string]$Description)
    if ($Id -eq '{bootmgr}') { return 'disk' }
    if ($Description -match $NetworkPattern) { return 'network' }
    if ($Description -match $DiskPattern) { return 'disk' }
    return 'other'
}

function Test-Ipv4Network {
    param([string]$Description)
    if ($Description -match $Ipv6Pattern -or $Description -match $HttpPattern) { return $false }
    return ($Description -match $Ipv4Pattern -or $Description -match $PxePattern)
}

function Read-FirmwareEntries {
    param([string[]]$Lines)
    $order = @()
    $descriptions = @{}
    $currentId = $null
    $collecting = $false
    foreach ($line in $Lines) {
        $text = [string]$line
        if ($collecting -and $text -match '^\s+(\{[^}]+\})\s*$') {
            $order += $Matches[1]
            continue
        }
        $collecting = $false
        if ($text -match '^identifier\s+(\{[^}]+\})') {
            $currentId = $Matches[1]
        } elseif ($text -match '^description\s+(.+?)\s*$') {
            if ($currentId) { $descriptions[$currentId] = $Matches[1] }
        } elseif ($text -match '^displayorder\s+(\{[^}]+\})') {
            if ($currentId -eq '{fwbootmgr}') {
                $order = @($Matches[1])
                $collecting = $true
            }
        }
    }
    return @{ Order = $order; Descriptions = $descriptions }
}

function Get-NewOrder {
    param([string[]]$Order, [hashtable]$Descriptions, [string]$Policy)
    $items = @()
    foreach ($id in $Order) {
        $desc = [string]$Descriptions[$id]
        $kind = Get-EntryKind -Id $id -Description $desc
        $ipv4 = ($kind -eq 'network') -and (Test-Ipv4Network -Description $desc)
        $items += ,@($id, $kind, $ipv4)
    }
    if (-not ($items | Where-Object { $_[1] -eq 'network' })) { return $null }
    $new = @()
    if ($Policy -eq 'pxe') {
        foreach ($i in $items) { if ($i[1] -eq 'network' -and $i[2]) { $new += $i[0] } }
        foreach ($i in $items) { if ($i[1] -eq 'disk') { $new += $i[0] } }
        foreach ($i in $items) { if ($i[1] -eq 'network' -and -not $i[2]) { $new += $i[0] } }
        foreach ($i in $items) { if ($i[1] -eq 'other') { $new += $i[0] } }
    } else {
        foreach ($i in $items) { if ($i[1] -eq 'disk') { $new += $i[0] } }
        foreach ($i in $items) { if ($i[1] -eq 'other') { $new += $i[0] } }
        foreach ($i in $items) { if ($i[1] -eq 'network') { $new += $i[0] } }
    }
    if (($new -join ',') -eq ($Order -join ',')) { return $null }
    return ,$new
}

function Invoke-UefiBootOrder {
    param([string]$Policy, [string]$MachineId)
    if ($Policy -ne 'pxe' -and $Policy -ne 'disk') { return }
    if ($MachineId -notmatch '^\d+$') { return }
    $parsed = Read-FirmwareEntries -Lines @(Invoke-Bcd @('/enum', 'firmware'))
    if ($parsed.Order.Count -eq 0) { return }
    if ($Policy -eq 'pxe') {
        if (-not (Invoke-PxeGate -Uri "$PxeBase/api/machines/$MachineId/events")) { return }
    }
    $new = Get-NewOrder -Order $parsed.Order -Descriptions $parsed.Descriptions -Policy $Policy
    if ($null -eq $new -or $new.Count -eq 0) { return }
    Invoke-Bcd (@('/set', '{fwbootmgr}', 'displayorder') + $new) | Out-Null
}

if (-not $env:PXE_UEFI_ORDER_NO_MAIN) {
    try { Invoke-UefiBootOrder -Policy $Policy -MachineId $MachineId } catch { }
    exit 0
}
"""


def windows_helper_script(public_url: str) -> str:
    """PowerShell 5.1 helper: ``powershell -File uefi-boot-order.ps1 <pxe|disk> <machine_id>``. Always exits 0."""
    replacements = {
        "__BASE__": _ps_quote((public_url or "").rstrip("/")),
        "__NETWORK__": _ps_quote("(?i)" + word_pattern(NETWORK_WORDS)),
        "__DISK__": _ps_quote("(?i)" + word_pattern(DISK_WORDS)),
        "__IPV4__": _ps_quote("(?i)" + word_pattern(("IPv4",))),
        "__IPV6__": _ps_quote("(?i)" + word_pattern(("IPv6",))),
        "__PXE__": _ps_quote("(?i)" + word_pattern(("PXE",))),
        "__HTTP__": _ps_quote("(?i)" + word_pattern(("HTTP",))),
    }
    script = _WINDOWS_TEMPLATE
    for token, value in replacements.items():
        script = script.replace(token, value)
    return script


UNATTEND_NS = "urn:schemas-microsoft-com:unattend"
WCM_NS = "http://schemas.microsoft.com/WMIConfig/2002/State"
DEPLOYMENT_COMPONENT = "Microsoft-Windows-Deployment"
# Setup documents 259, but ~255 is the widely reported practical ceiling; stay under it.
RUN_SYNCHRONOUS_PATH_MAX = 255
_COMPONENT_DEFAULTS = (
    ("processorArchitecture", "amd64"),
    ("publicKeyToken", "31bf3856ad364e35"),
    ("language", "neutral"),
    ("versionScope", "nonSxS"),
)
# Unquoted inside cmd.exe /c "...": no spaces, quotes, %, &, |, ^, <, >.
# register_namespace mutates process-wide state; serialize concurrent renders.
_NS_LOCK = threading.Lock()
_CMD_SAFE_URL = re.compile(r"^https?://[A-Za-z0-9.\-:\[\]/_~]+$")


def valid_policy_args(policy: str, machine_id: str) -> bool:
    """``policy`` is pxe/disk and ``machine_id`` is ASCII digits."""
    return policy in POLICIES and bool(machine_id) and machine_id.isascii() and machine_id.isdigit()


def windows_run_command(public_url: str, policy: str, machine_id: str) -> str:
    """specialize RunSynchronous Path: download the PS helper, run it, and always exit 0."""
    url = f"{public_url.rstrip('/')}/boot-files/uefi-boot-order.ps1"
    script = r"%WINDIR%\Temp\pxe-bo.ps1"
    return (
        f'cmd.exe /c "curl.exe -s -o {script} {url} & '
        f'powershell -NoProfile -ExecutionPolicy Bypass -File {script} {policy} {machine_id} & exit /b 0"'
    )


def _append(parent: ET.Element, child: ET.Element, level: int) -> None:
    """Append ``child`` to ``parent`` (at depth ``level``) keeping two-space indentation."""
    inner = "\n" + "  " * (level + 1)
    if len(parent):
        last = parent[-1]
        child.tail = last.tail
        last.tail = parent.text if parent.text and not parent.text.strip() else inner
    else:
        parent.text = inner
        child.tail = "\n" + "  " * level
    parent.append(child)
    ET.indent(child, space="  ", level=level + 1)


def _insert_settings(root: ET.Element, settings: ET.Element, ns: str) -> None:
    """New specialize pass goes before oobeSystem when present, else at the end."""
    for index, child in enumerate(root):
        if child.tag == f"{ns}settings" and child.get("pass") == "oobeSystem":
            settings.tail = root.text if root.text and not root.text.strip() else "\n  "
            root.insert(index, settings)
            ET.indent(settings, space="  ", level=1)
            return
    _append(root, settings, 0)


def inject_uefi_order_command(xml_text: str, public_url: str, machine_id: str, policy: str) -> str:
    """Add a specialize RunSynchronousCommand that applies the BootOrder policy.

    Returns ``xml_text`` unchanged on invalid input, a parse error, an existing helper command, or a Path
    over the RunSynchronous Path length limit.
    """
    policy = str(policy or "")
    machine_id = str(machine_id or "")
    public_url = str(public_url or "").rstrip("/")
    if not valid_policy_args(policy, machine_id) or not _CMD_SAFE_URL.match(public_url):
        return xml_text
    path_text = windows_run_command(public_url, policy, machine_id)
    if len(path_text) > RUN_SYNCHRONOUS_PATH_MAX:
        return xml_text
    with _NS_LOCK:
        try:
            for _event, (prefix, uri) in ET.iterparse(io.StringIO(xml_text), events=("start-ns",)):
                ET.register_namespace(prefix or "", uri)
            ET.register_namespace("wcm", WCM_NS)
            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
            root = ET.fromstring(xml_text, parser=parser)
            ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
            if any("uefi-boot-order.ps1" in (el.text or "") for el in root.iter(f"{ns}Path")):
                return xml_text

            settings = next(
                (el for el in root.findall(f"{ns}settings") if el.get("pass") == "specialize"),
                None,
            )
            if settings is None:
                settings = ET.Element(f"{ns}settings", {"pass": "specialize"})
                _insert_settings(root, settings, ns)

            components = settings.findall(f"{ns}component")
            component = next((el for el in components if el.get("name") == DEPLOYMENT_COMPONENT), None)
            if component is None:
                sibling = components[0] if components else next(root.iter(f"{ns}component"), None)
                attrs = {"name": DEPLOYMENT_COMPONENT}
                for key, default in _COMPONENT_DEFAULTS:
                    attrs[key] = (sibling.get(key) if sibling is not None else None) or default
                component = ET.Element(f"{ns}component", attrs)
                _append(settings, component, 1)

            run_sync = component.find(f"{ns}RunSynchronous")
            if run_sync is None:
                run_sync = ET.Element(f"{ns}RunSynchronous")
                _append(component, run_sync, 2)

            orders = []
            for cmd in run_sync.findall(f"{ns}RunSynchronousCommand"):
                text = (cmd.findtext(f"{ns}Order") or "").strip()
                if text.isdigit():
                    orders.append(int(text))
            command = ET.Element(f"{ns}RunSynchronousCommand", {f"{{{WCM_NS}}}action": "add"})
            ET.SubElement(command, f"{ns}Order").text = str(max(orders, default=0) + 1)
            ET.SubElement(command, f"{ns}Description").text = "PXE UEFI boot order"
            ET.SubElement(command, f"{ns}Path").text = path_text
            _append(run_sync, command, 3)

            body = ET.tostring(root, encoding="unicode")
        except (ET.ParseError, ValueError):
            return xml_text
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"
