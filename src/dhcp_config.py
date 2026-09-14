"""Write dnsmasq.conf from settings (no secrets)."""

from __future__ import annotations

from pathlib import Path

from .settings import Settings


def render_dnsmasq_conf(settings: Settings, *, tftp_root: Path, conf_path: Path) -> str:
    iface = settings.bind_interface
    lines = [
        "port=0",
        f"interface={iface}",
        "bind-interfaces",
        "enable-tftp",
        f"tftp-root={tftp_root}",
        "log-dhcp",
        "dhcp-no-override",
        "dhcp-option=vendor:PXEClient,6,2b",
        "dhcp-match=set:ipxe,175",
        "dhcp-match=set:efi-x86_64,option:client-arch,7",
        "dhcp-match=set:efi-x86_64,option:client-arch,9",
        "dhcp-match=set:efi-arm64,option:client-arch,11",
    ]
    if settings.dhcp_mode == "authoritative":
        lines.append("dhcp-authoritative")
        lines.append(f"dhcp-range={settings.dhcp_range}")
        if settings.dhcp_router:
            lines.append(f"dhcp-option=3,{settings.dhcp_router}")
        if settings.dhcp_dns:
            lines.append(f"dhcp-option=6,{settings.dhcp_dns}")
    else:
        lines.append("dhcp-range=10.0.0.0,proxy")
    lines.extend(
        [
            "dhcp-boot=tag:!ipxe,tag:!efi-x86_64,tag:!efi-arm64,undionly.kpxe",
            "dhcp-boot=tag:!ipxe,tag:efi-x86_64,ipxe.efi",
            "dhcp-boot=tag:!ipxe,tag:efi-arm64,snponly.efi",
            f"dhcp-boot=tag:ipxe,{settings.public_url}/boot.ipxe",
        ]
    )
    text = "\n".join(lines) + "\n"
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(text, encoding="utf-8")
    return text
