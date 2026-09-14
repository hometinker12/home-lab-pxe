from pathlib import Path

from src.dhcp_config import render_dnsmasq_conf
from src.settings import Settings


def test_proxy_dnsmasq_has_no_secrets(tmp_path: Path):
    settings = Settings(
        http_bind="0.0.0.0",
        http_port=8080,
        public_url="http://192.168.1.10:8080",
        dhcp_mode="proxy",
        bind_interface="eth0",
        tftp_root=tmp_path,
        image_root=tmp_path,
        database_url="sqlite://",
        enable_dhcp=True,
        dhcp_optional=False,
        dhcp_range="192.168.1.200,192.168.1.250,12h",
        dhcp_router="",
        dhcp_dns="",
        admin_user="admin",
        admin_password="should-not-appear",
        openapi_enabled=False,
        debug_errors=False,
    )
    text = render_dnsmasq_conf(settings, tftp_root=tmp_path, conf_path=tmp_path / "dnsmasq.conf")
    assert "enable-tftp" in text
    assert "proxy" in text
    assert "boot.ipxe" in text
    assert "should-not-appear" not in text
