"""Detect and label IPv4 addresses for the Settings console.

Inside Docker Desktop the container only has a bridge address (172.x) and
host.docker.internal is the VM gateway, not the host's LAN NIC. Set
PXE_HOST_LAN_IPV4 in .env to this computer's LAN IPv4 (not 127.0.0.1, not a
Docker 172.x address).
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv4Address, ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

from .settings import get_settings

_DOCKER_DESKTOP_GW = IPv4Address("192.168.65.254")
_DOCKER_BRIDGE = ip_network("172.16.0.0/12")


def in_container() -> bool:
    return Path("/.dockerenv").exists()


def _as_ipv4(value: str) -> IPv4Address | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = ip_address(text)
    except (AddressValueError, ValueError):
        return None
    if parsed.version != 4:
        return None
    return IPv4Address(str(parsed))


def is_docker_desktop_gateway(ip: IPv4Address) -> bool:
    return ip == _DOCKER_DESKTOP_GW


def is_usable_host_lan(ip: IPv4Address, *, container: bool) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    if is_docker_desktop_gateway(ip):
        return False
    if container and ip in _DOCKER_BRIDGE:
        return False
    return True


def parse_host_header(host: str) -> str:
    text = (host or "").split(",")[0].strip()
    if not text:
        return ""
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end != -1 else text
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def _udp_source_ipv4() -> IPv4Address | None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = _as_ipv4(sock.getsockname()[0])
        sock.close()
        return ip
    except OSError:
        return None


def interface_ipv4s() -> list[str]:
    found: set[str] = set()
    udp = _udp_source_ipv4()
    if udp is not None:
        found.add(str(udp))
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = _as_ipv4(item[4][0])
            if ip is not None:
                found.add(str(ip))
    except OSError:
        pass
    try:
        for item in socket.getaddrinfo("host.docker.internal", None, socket.AF_INET):
            ip = _as_ipv4(item[4][0])
            if ip is not None:
                found.add(str(ip))
    except OSError:
        pass
    return sorted(found)


def _from_env() -> tuple[IPv4Address | None, str]:
    ip = _as_ipv4(os.getenv("PXE_HOST_LAN_IPV4", ""))
    if ip is not None:
        return ip, "PXE_HOST_LAN_IPV4"
    return None, ""


@dataclass(frozen=True)
class NetSnapshot:
    host_lan_ipv4: str
    host_lan_source: str
    request_host: str
    public_url: str
    public_host: str
    advertised_pxe_url: str
    container_ipv4s: tuple[str, ...]
    docker_gateway: str
    in_container: bool


def advertised_boot_url(host_lan_ipv4: str) -> str:
    settings = get_settings()
    parsed = urlparse(settings.public_url)
    port = parsed.port or settings.http_port
    host = host_lan_ipv4 or parsed.hostname or "127.0.0.1"
    return f"http://{host}:{port}/boot.ipxe"


def net_snapshot(request=None) -> NetSnapshot:
    inside = in_container()
    env_ip, env_source = _from_env()
    request_host = ""
    if request is not None:
        forwarded = request.headers.get("x-forwarded-host") or ""
        header = forwarded or request.headers.get("host") or ""
        request_host = parse_host_header(header)
    host_lan = ""
    source = ""
    if env_ip is not None and is_usable_host_lan(env_ip, container=False):
        host_lan = str(env_ip)
        source = env_source
    if not host_lan:
        req_ip = _as_ipv4(request_host)
        if req_ip is not None and is_usable_host_lan(req_ip, container=False):
            host_lan = str(req_ip)
            source = "this HTTP request"
    public = get_settings().public_url.rstrip("/")
    public_host = urlparse(public).hostname or ""
    container_ips = tuple(
        ip
        for ip in interface_ipv4s()
        if (parsed := _as_ipv4(ip)) is not None and not parsed.is_loopback and not is_docker_desktop_gateway(parsed)
    )
    gw = ""
    try:
        for item in socket.getaddrinfo("host.docker.internal", None, socket.AF_INET):
            ip = _as_ipv4(item[4][0])
            if ip is not None:
                gw = str(ip)
                break
    except OSError:
        pass
    return NetSnapshot(
        host_lan_ipv4=host_lan,
        host_lan_source=source,
        request_host=request_host,
        public_url=public,
        public_host=public_host,
        advertised_pxe_url=advertised_boot_url(host_lan),
        container_ipv4s=container_ips,
        docker_gateway=gw,
        in_container=inside,
    )
