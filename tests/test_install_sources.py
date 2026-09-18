from src.install_sources import (
    apply_catalog,
    catalog_from_json,
    linux_sources_from_tree,
    parse_ubuntu_install_sources,
    parse_wim_image_xml,
    pick_source_id,
    windows_sources_from_wim,
)
from src.models import ExtractStatus, Image, OsFamily


def minimal_wim_bytes(images: list[tuple[int, str, str]]) -> bytes:
    parts = ["<WIM>"]
    for index, name, description in images:
        parts.append(f'<IMAGE INDEX="{index}"><NAME>{name}</NAME><DESCRIPTION>{description}</DESCRIPTION></IMAGE>')
    parts.append("</WIM>")
    xml = b"\xff\xfe" + "".join(parts).encode("utf-16-le")
    header = bytearray(208)
    header[0:8] = b"MSWIM\x00\x00\x00"
    header[8:12] = (208).to_bytes(4, "little")
    header[44:48] = (len(images)).to_bytes(4, "little")
    header[72:80] = (len(xml)).to_bytes(8, "little")
    header[80:88] = (208).to_bytes(8, "little")
    header[88:96] = (len(xml)).to_bytes(8, "little")
    return bytes(header) + xml


def test_parse_ubuntu_install_sources_v2():
    text = """
kernel:
  default: linux-generic
sources:
- description:
    en: minimized
  id: ubuntu-server-minimal
  name:
    en: Ubuntu Server (minimized)
- default: true
  id: ubuntu-server
  name:
    en: Ubuntu Server
version: 2
"""
    items = parse_ubuntu_install_sources(text)
    assert [item.source_id for item in items] == ["ubuntu-server-minimal", "ubuntu-server"]
    assert items[0].label == "Ubuntu Server (minimized)"
    assert items[1].default is True
    assert pick_source_id(items, "") == "ubuntu-server"
    assert pick_source_id(items, "ubuntu-server-minimal") == "ubuntu-server-minimal"


def test_parse_ubuntu_install_sources_legacy_list():
    text = """
- id: ubuntu-server-minimal
  name: {en: Ubuntu Server (minimized)}
- id: ubuntu-server
  default: true
  name: {en: Ubuntu Server}
"""
    items = parse_ubuntu_install_sources(text)
    assert items[1].source_id == "ubuntu-server"
    assert items[1].default is True


def test_linux_sources_from_tree(tmp_path):
    casper = tmp_path / "casper"
    casper.mkdir()
    (casper / "install-sources.yaml").write_text(
        "- id: ubuntu-server-minimal\n  name: {en: Minimal}\n",
        encoding="utf-8",
    )
    items = linux_sources_from_tree(tmp_path)
    assert items[0].source_id == "ubuntu-server-minimal"


def test_parse_wim_xml_and_header(tmp_path):
    xml = """<WIM>
      <IMAGE INDEX="1"><NAME>Windows Server 2022 SERVERSTANDARDCORE</NAME>
        <DESCRIPTION>Windows Server 2022 Standard</DESCRIPTION></IMAGE>
      <IMAGE INDEX="2"><NAME>Windows Server 2022 SERVERSTANDARD</NAME>
        <DESCRIPTION>Windows Server 2022 Standard (Desktop Experience)</DESCRIPTION></IMAGE>
    </WIM>"""
    items = parse_wim_image_xml(xml)
    assert items[0].wim_index == 1
    assert items[0].default is True
    assert items[1].source_id == "Windows Server 2022 SERVERSTANDARD"
    wim = tmp_path / "install.wim"
    wim.write_bytes(
        minimal_wim_bytes(
            [
                (1, "Windows Server 2022 SERVERSTANDARDCORE", "Standard Core"),
                (2, "Windows Server 2022 SERVERDATACENTER", "Datacenter"),
            ]
        )
    )
    from_file = windows_sources_from_wim(wim)
    assert [item.wim_index for item in from_file] == [1, 2]
    assert from_file[1].source_id == "Windows Server 2022 SERVERDATACENTER"


def test_apply_catalog_keeps_current_choice():
    image = Image(name="img", os_family=OsFamily.linux.value, source_id="ubuntu-server-minimal")
    image.extract_status = ExtractStatus.ready.value
    items = parse_ubuntu_install_sources(
        "- id: ubuntu-server-minimal\n  name: {en: Minimal}\n"
        "- id: ubuntu-server\n  default: true\n  name: {en: Server}\n"
    )
    assert apply_catalog(image, items) is True
    assert image.source_id == "ubuntu-server-minimal"
    round_trip = catalog_from_json(image.source_options)
    assert [item.source_id for item in round_trip] == ["ubuntu-server-minimal", "ubuntu-server"]
