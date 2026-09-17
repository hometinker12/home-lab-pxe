from pathlib import Path

from src.dhcp_config import render_dnsmasq_conf
from src.settings import Settings


def test_proxy_dnsmasq_has_no_secrets(tmp_path: Path):
    settings = Settings(
        http_bind="0.0.0.0",
        http_port=8080,
        https_port=8443,
        ssl_dir=tmp_path / "ssl",
        public_url="http://192.168.1.10:8080",
        dhcp_mode="proxy",
        bind_interface="eth0",
        tftp_root=tmp_path,
        image_root=tmp_path,
        database_url="sqlite://",
        enable_dhcp=True,
        enable_tftp=True,
        dhcp_optional=False,
        dhcp_range="192.168.1.200,192.168.1.250,12h",
        dhcp_router="",
        dhcp_dns="",
        admin_user="admin",
        admin_password="should-not-appear",
        openapi_enabled=False,
        debug_errors=False,
        data_dir=tmp_path,
        max_upload_bytes=1024,
        max_seed_bytes=1024,
        max_extract_bytes=1024,
        extract_timeout_seconds=60,
        seven_z_bin="7z",
        smb_host="192.168.1.10",
        smb_user="pxemedia",
        smb_password="",
        nfs_host="192.168.1.10",
        nfs_export="/var/lib/pxe/images/nfs",
    )
    text = render_dnsmasq_conf(settings, tftp_root=tmp_path, conf_path=tmp_path / "dnsmasq.conf")
    assert "enable-tftp" in text
    assert "tftp-single-port" in text
    assert "proxy" in text
    assert "boot.ipxe" in text
    assert "should-not-appear" not in text


def test_extra_options_are_appended(tmp_path: Path):
    from src.dhcp_config import DnsmasqSpec

    spec = DnsmasqSpec(
        public_url="http://pxe.test:8080",
        dhcp_mode="authoritative",
        bind_interface="eth0",
        dhcp_range="192.168.1.200,192.168.1.250,12h",
        dhcp_router="192.168.1.1",
        dhcp_dns="192.168.1.1",
        extra_options=("dhcp-option=15,lan.home",),
    )
    text = render_dnsmasq_conf(spec, tftp_root=tmp_path, conf_path=tmp_path / "dnsmasq.conf")
    assert "dhcp-authoritative" in text
    assert "dhcp-option=3,192.168.1.1" in text
    assert "dhcp-option=15,lan.home" in text


def test_tftp_can_be_omitted(tmp_path: Path):
    from src.dhcp_config import DnsmasqSpec

    spec = DnsmasqSpec(
        public_url="http://pxe.test:8080",
        dhcp_mode="proxy",
        bind_interface="eth0",
        dhcp_range="192.168.1.200,192.168.1.250,12h",
        dhcp_router="",
        dhcp_dns="",
        dhcp_enabled=True,
        tftp_enabled=False,
    )
    text = render_dnsmasq_conf(spec, tftp_root=tmp_path, conf_path=tmp_path / "dnsmasq.conf")
    assert "enable-tftp" not in text
    assert "tftp-single-port" not in text
    assert "proxy" in text


def test_dhcp_can_be_omitted(tmp_path: Path):
    from src.dhcp_config import DnsmasqSpec

    spec = DnsmasqSpec(
        public_url="http://pxe.test:8080",
        dhcp_mode="proxy",
        bind_interface="eth0",
        dhcp_range="192.168.1.200,192.168.1.250,12h",
        dhcp_router="",
        dhcp_dns="",
        dhcp_enabled=False,
        tftp_enabled=True,
    )
    text = render_dnsmasq_conf(spec, tftp_root=tmp_path, conf_path=tmp_path / "dnsmasq.conf")
    assert "enable-tftp" in text
    assert "tftp-single-port" in text
    assert "dhcp-range" not in text
    assert "dhcp-boot" not in text
