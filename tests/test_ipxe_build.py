import re
from datetime import UTC, datetime

import pytest
from tests.conftest import login

import src.ipxe_build as ipxe_build_module
from src.ipxe_build import (
    IPXE_COMMIT,
    IpxeBuildError,
    UsbBuildOptions,
    install_built_efi,
    render_usb_local_header,
    restore_stock_efi,
)
from src.models import IpxeBuildStatus


@pytest.fixture(autouse=True)
def _reset_ipxe_build_thread():
    ipxe_build_module._pending = False
    ipxe_build_module._thread = None
    yield
    ipxe_build_module._pending = False
    ipxe_build_module._thread = None


_SYMBOLS = {
    "USB_HCD_EHCI",
    "USB_HCD_UHCI",
    "USB_HCD_XHCI",
    "USB_HCD_USBIO",
    "USB_BLOCK",
    "USB_KEYBOARD",
}


def _efi(tag: bytes) -> bytes:
    pad = b"\x00" * 64
    return b"MZ" + tag + pad


def test_usb_header_is_allowlisted_and_keyboard_define_wins():
    header = render_usb_local_header(
        UsbBuildOptions(usb_keyboard=True, usb_block=False, hcd_xhci=False, hcd_usbio=True)
    )
    names = set(re.findall(r"#(?:define|undef) ([A-Z0-9_]+)", header))
    assert names == _SYMBOLS
    assert "#define USB_KEYBOARD" in header
    assert "#undef USB_BLOCK" in header
    assert "#undef USB_HCD_XHCI" in header
    assert "#define USB_HCD_USBIO" in header
    assert "#define USB_HCD_EHCI" in header

    stock = render_usb_local_header(UsbBuildOptions())
    assert "#undef USB_KEYBOARD" in stock
    assert "#undef USB_HCD_USBIO" in stock
    assert "#define USB_BLOCK" in stock


def test_install_backs_up_stock_once_and_rejects_a_bad_image(tmp_path):
    tftp = tmp_path
    original = _efi(b"stock")
    (tftp / "ipxe.efi").write_bytes(original)
    custom = _efi(b"custom")
    install_built_efi(
        tftp,
        custom,
        header="#define USB_KEYBOARD\n",
        commit=IPXE_COMMIT,
        built_at=datetime(2026, 9, 22, tzinfo=UTC),
        map_text="000000000011c9b0 g     O .data usbkbd_driver",
        require_usbkbd=True,
    )
    assert (tftp / "ipxe-native.efi").read_bytes() == original
    assert (tftp / "ipxe.efi").read_bytes() == custom
    assert (tftp / "ipxe-custom.efi").read_bytes() == custom
    note = (tftp / "ipxe-custom.txt").read_text(encoding="utf-8")
    assert IPXE_COMMIT in note
    assert "GPL-2.0-only" in note
    assert "#define USB_KEYBOARD" in note

    again = _efi(b"again")
    install_built_efi(
        tftp,
        again,
        header="#undef USB_KEYBOARD\n",
        commit=IPXE_COMMIT,
        built_at=datetime(2026, 9, 22, tzinfo=UTC),
        map_text="",
        require_usbkbd=False,
    )
    assert (tftp / "ipxe-native.efi").read_bytes() == original
    assert (tftp / "ipxe.efi").read_bytes() == again

    untouched = (tftp / "ipxe.efi").read_bytes()
    with pytest.raises(IpxeBuildError, match="EFI image"):
        install_built_efi(
            tftp,
            b"not-an-efi-image",
            header="",
            commit=IPXE_COMMIT,
            built_at=datetime(2026, 9, 22, tzinfo=UTC),
            map_text="usbkbd_driver",
            require_usbkbd=False,
        )
    assert (tftp / "ipxe.efi").read_bytes() == untouched
    assert not (tftp / ".ipxe.efi.partial").exists()


def test_keyboard_build_requires_the_linked_driver(tmp_path):
    with pytest.raises(IpxeBuildError, match="keyboard"):
        install_built_efi(
            tmp_path,
            _efi(b"nokey"),
            header="#define USB_KEYBOARD\n",
            commit=IPXE_COMMIT,
            built_at=datetime(2026, 9, 22, tzinfo=UTC),
            map_text="other_driver",
            require_usbkbd=True,
        )
    assert not (tmp_path / "ipxe.efi").exists()
    assert not (tmp_path / "ipxe-native.efi").exists()


def test_restore_stock_copies_the_backup(tmp_path):
    native = _efi(b"native")
    (tmp_path / "ipxe-native.efi").write_bytes(native)
    (tmp_path / "ipxe.efi").write_bytes(_efi(b"custom"))
    restore_stock_efi(tmp_path)
    assert (tmp_path / "ipxe.efi").read_bytes() == native
    with pytest.raises(IpxeBuildError, match="backup"):
        restore_stock_efi(tmp_path / "empty")


def test_boot_menu_shows_ipxe_build_defaults(client):
    login(client)
    page = client.get("/boot-menu")
    assert page.status_code == 200
    assert "iPXE build" in page.text
    assert "USB keyboard" in page.text
    assert "GPL-2.0-only" in page.text
    assert IPXE_COMMIT in page.text
    assert "Use stock" not in page.text
    assert 'name="usb_keyboard"' in page.text
    assert 'name="hcd_xhci" value="1" checked' in page.text
    keyboard = page.text.split('name="usb_keyboard"', 1)[1][:80]
    assert "checked" not in keyboard


def test_rebuild_saves_options_and_rejects_a_second_build(client, monkeypatch):
    import src.ipxe_build as build

    login(client)
    monkeypatch.setattr("src.routes.boot_menu.launch_rebuild", lambda: None)
    response = client.post(
        "/boot-menu/ipxe-build",
        data={
            "action": "rebuild",
            "usb_keyboard": "1",
            "usb_block": "1",
            "hcd_ehci": "1",
            "hcd_uhci": "1",
            "hcd_xhci": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/boot-menu?ipxe=1")
    from src.db import session_scope
    from src.ipxe_build import get_or_create_ipxe_build

    with session_scope() as db:
        row = get_or_create_ipxe_build(db)
        assert row.usb_keyboard
        assert not row.hcd_usbio
        assert row.status == IpxeBuildStatus.building.value

    monkeypatch.setattr(build, "rebuild_in_progress", lambda: True)
    page = client.get("/boot-menu?ipxe=1")
    assert "Rebuild is running" in page.text
    assert "location.reload" in page.text

    blocked = client.post(
        "/boot-menu/ipxe-build",
        data={"action": "rebuild", "usb_keyboard": "1"},
        follow_redirects=False,
    )
    assert blocked.status_code == 200
    assert "already running" in blocked.text


def test_use_stock_restores_the_served_file(client, tmp_path):
    login(client)
    tftp = tmp_path / "tftp"
    native = _efi(b"native")
    (tftp / "ipxe-native.efi").write_bytes(native)
    (tftp / "ipxe.efi").write_bytes(_efi(b"custom"))
    page = client.post("/boot-menu/ipxe-build", data={"action": "stock"}, follow_redirects=True)
    assert page.status_code == 200
    assert (tftp / "ipxe.efi").read_bytes() == native
    assert "Serving the stock" in page.text
