"""Write dnsmasq.conf from DHCP settings (no secrets)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import Settings


@dataclass(frozen=True)
class DnsmasqSpec:
    public_url: str
    dhcp_mode: str
    bind_interface: str
    dhcp_range: str
    dhcp_router: str
    dhcp_dns: str
    extra_options: tuple[str, ...] = ()
    dhcp_enabled: bool = True
    tftp_enabled: bool = True


def spec_from_settings(settings: Settings) -> DnsmasqSpec:
    return DnsmasqSpec(
        public_url=settings.public_url,
        dhcp_mode=settings.dhcp_mode,
        bind_interface=settings.bind_interface,
        dhcp_range=settings.dhcp_range,
        dhcp_router=settings.dhcp_router,
        dhcp_dns=settings.dhcp_dns,
        dhcp_enabled=settings.enable_dhcp,
        tftp_enabled=settings.enable_tftp,
    )


def render_dnsmasq_conf(spec: Settings | DnsmasqSpec, *, tftp_root: Path, conf_path: Path) -> str:
    if isinstance(spec, Settings):
        spec = spec_from_settings(spec)
    iface = spec.bind_interface
    lines = [
        "port=0",
        f"interface={iface}",
        "bind-interfaces",
        "log-dhcp",
    ]
    if spec.tftp_enabled:
        lines.extend(
            [
                "enable-tftp",
                "tftp-single-port",
                f"tftp-root={tftp_root}",
            ]
        )
    if spec.dhcp_enabled:
        lines.extend(
            [
                "dhcp-no-override",
                "dhcp-option=vendor:PXEClient,6,2b",
                "dhcp-match=set:ipxe,175",
                "dhcp-match=set:efi-x86_64,option:client-arch,7",
                "dhcp-match=set:efi-x86_64,option:client-arch,9",
                "dhcp-match=set:efi-arm64,option:client-arch,11",
            ]
        )
        if spec.dhcp_mode == "authoritative":
            lines.append("dhcp-authoritative")
            lines.append(f"dhcp-range={spec.dhcp_range}")
            if spec.dhcp_router:
                lines.append(f"dhcp-option=3,{spec.dhcp_router}")
            if spec.dhcp_dns:
                lines.append(f"dhcp-option=6,{spec.dhcp_dns}")
        else:
            lines.append("dhcp-range=10.0.0.0,proxy")
        lines.extend(
            [
                "dhcp-boot=tag:!ipxe,tag:!efi-x86_64,tag:!efi-arm64,undionly.kpxe",
                "dhcp-boot=tag:!ipxe,tag:efi-x86_64,ipxe.efi",
                "dhcp-boot=tag:!ipxe,tag:efi-arm64,snponly.efi",
                f"dhcp-boot=tag:ipxe,{spec.public_url}/boot.ipxe",
            ]
        )
    lines.extend(spec.extra_options)
    text = "\n".join(lines) + "\n"
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(text, encoding="utf-8")
    return text
