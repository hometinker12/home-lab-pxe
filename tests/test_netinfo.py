from ipaddress import IPv4Address

from tests.conftest import login

from src.netinfo import is_usable_host_lan, parse_host_header


def test_parse_host_header_strips_port():
    assert parse_host_header("192.168.2.223:8080") == "192.168.2.223"
    assert parse_host_header("127.0.0.1") == "127.0.0.1"
    assert parse_host_header("[::1]:8080") == "::1"


def test_usable_host_lan_filters_loopback_docker_and_link_local():
    assert is_usable_host_lan(IPv4Address("192.168.2.223"), container=True) is True
    assert is_usable_host_lan(IPv4Address("127.0.0.1"), container=True) is False
    assert is_usable_host_lan(IPv4Address("169.254.1.1"), container=True) is False
    assert is_usable_host_lan(IPv4Address("172.22.0.2"), container=True) is False
    assert is_usable_host_lan(IPv4Address("192.168.65.254"), container=True) is False
    assert is_usable_host_lan(IPv4Address("172.22.0.2"), container=False) is True


def test_settings_shows_injected_host_lan_ipv4(client, monkeypatch):
    monkeypatch.setenv("PXE_HOST_LAN_IPV4", "192.168.9.9")
    login(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Host LAN IPv4" in page.text
    assert "192.168.9.9" in page.text
    assert "PXE_HOST_LAN_IPV4" in page.text
    assert "http://192.168.9.9:8080/boot.ipxe" in page.text
    assert "This page" not in page.text
    assert "Docker Desktop gateway" not in page.text


def test_settings_prompts_for_host_lan_env_when_unset(client, monkeypatch):
    monkeypatch.delenv("PXE_HOST_LAN_IPV4", raising=False)
    login(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "not set" in page.text
    assert "PXE_HOST_LAN_IPV4" in page.text
    assert "host_lan_ipv4.py" not in page.text
