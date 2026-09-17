from tests.conftest import login


def test_settings_saves_dhcp_toggle_and_options(client, tmp_path):
    login(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "DHCP" in page.text
    assert "TFTP" in page.text
    assert "tftp-browser" not in page.text
    assert 'href="/files"' in page.text
    assert "<details" in page.text
    assert "Option 60 (PXEClient)" in page.text
    assert "Option 66 (Next Server)" in page.text
    assert "Option 67 (Boot File Name)" in page.text
    assert "same physical machine" in page.text
    assert "mandatory" in page.text
    assert "undionly.kpxe" in page.text
    assert "Already iPXE" in page.text
    assert "exec format error" in page.text
    assert 'data-dhcp-mode="authoritative"' in page.text
    assert "dhcp-form" in page.text
    pxe = client.post(
        "/settings/pxe",
        data={
            "bind_interface": "eth0",
            "extra_options": "dhcp-option=15,lab.home\n# comment\n",
        },
        follow_redirects=False,
    )
    assert pxe.status_code in {302, 303}
    saved = client.post(
        "/settings/dhcp",
        data={
            "enabled": "1",
            "mode": "authoritative",
            "dhcp_range": "192.168.1.200,192.168.1.250,12h",
            "dhcp_router": "192.168.1.1",
            "dhcp_dns": "192.168.1.1",
        },
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    again = client.get("/settings")
    assert "authoritative" in again.text
    assert "lab.home" in again.text
    from src.dhcp_runtime import conf_path, enabled_path

    conf = conf_path().read_text(encoding="utf-8")
    assert "dhcp-authoritative" in conf
    assert "dhcp-option=15,lab.home" in conf
    assert enabled_path().read_text(encoding="utf-8").strip() == "1"


def test_settings_rejects_dhcp_script_option(client):
    login(client)
    response = client.post(
        "/settings/pxe",
        data={
            "bind_interface": "eth0",
            "extra_options": "dhcp-script=/usr/bin/evil",
        },
    )
    assert response.status_code == 200
    assert "not allowed" in response.text.lower()


def test_settings_can_disable_dhcp(client):
    login(client)
    response = client.post(
        "/settings/dhcp",
        data={
            "mode": "proxy",
            "dhcp_range": "192.168.1.200,192.168.1.250,12h",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    from src.dhcp_runtime import enabled_path

    assert enabled_path().read_text(encoding="utf-8").strip() == "0"


def test_settings_can_toggle_tftp_separately(client):
    login(client)
    client.post(
        "/settings/dhcp",
        data={
            "enabled": "1",
            "mode": "proxy",
            "dhcp_range": "192.168.1.200,192.168.1.250,12h",
        },
        follow_redirects=False,
    )
    disabled = client.post(
        "/settings/tftp",
        data={},
        follow_redirects=False,
    )
    assert disabled.status_code in {302, 303}
    from src.dhcp_runtime import conf_path, enabled_path, tftp_enabled_path

    assert enabled_path().read_text(encoding="utf-8").strip() == "1"
    assert tftp_enabled_path().read_text(encoding="utf-8").strip() == "0"
    assert "enable-tftp" not in conf_path().read_text(encoding="utf-8")
    assert "tftp-single-port" not in conf_path().read_text(encoding="utf-8")
    enabled = client.post(
        "/settings/tftp",
        data={"tftp_enabled": "1"},
        follow_redirects=False,
    )
    assert enabled.status_code in {302, 303}
    assert tftp_enabled_path().read_text(encoding="utf-8").strip() == "1"
    assert "enable-tftp" in conf_path().read_text(encoding="utf-8")
    assert "tftp-single-port" in conf_path().read_text(encoding="utf-8")


def test_external_dhcp_hints_include_option_60(client):
    from src.dhcp_runtime import external_dhcp_hints

    hints = external_dhcp_hints()
    assert hints["vendor_class"] == "PXEClient"
    assert hints["bios_filename"] == "undionly.kpxe"
    assert hints["efi_filename"] == "ipxe.efi"
    assert hints["arm_filename"] == "snponly.efi"
    assert hints["ipxe_filename"] == "boot.ipxe"
    assert hints["ipxe_script"].endswith("/boot.ipxe")
    assert hints["next_server"]
