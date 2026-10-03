from tests.conftest import login

from src.settings_attention import load_settings_attention


def test_settings_section_order_and_rename(client):
    login(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Imaging default local/root account" in page.text
    assert "Lab default local accounts" not in page.text
    assert page.text.find('data-section="accounts"') < page.text.find('data-section="machines"')
    assert page.text.find('data-section="machines"') < page.text.find('data-section="pxe"')


def _tab_markup(text: str, tab_id: str) -> str:
    start = text.find(f'id="{tab_id}"')
    return text[start : text.find("</a>", start)]


def test_settings_subsections_are_tabs(client):
    login(client)
    page = client.get("/settings")
    ids = ["accounts-tab", "machines-tab", "network-tab", "security-tab", "media-tab"]
    positions = [page.text.find(f'id="{tab_id}"') for tab_id in ids]
    assert -1 not in positions
    assert positions == sorted(positions)
    assert 'aria-selected="true"' in _tab_markup(page.text, "accounts-tab")
    assert "count-attention" in _tab_markup(page.text, "accounts-tab")
    assert "count-attention" not in _tab_markup(page.text, "media-tab")
    for name in ("machines", "network", "security", "media"):
        assert f'data-tab-panel="{name}" hidden' in page.text
    assert 'data-tab-panel="accounts" hidden' not in page.text
    assert 'class="section-label">Accounts<' not in page.text


def test_settings_tab_follows_section_and_tab_query(client):
    login(client)
    for url, tab in (
        ("/settings?section=dhcp", "network"),
        ("/settings?section=smb", "media"),
        ("/settings?tab=security", "security"),
        ("/settings?tab=bogus", "accounts"),
    ):
        page = client.get(url)
        assert 'aria-selected="true"' in _tab_markup(page.text, f"{tab}-tab"), url
        assert f'data-tab-panel="{tab}" hidden' not in page.text, url


def test_settings_nav_badge_counts_unset_accounts(client):
    login(client)
    machines = client.get("/machines")
    assert 'class="nav-alert"' in machines.text
    assert 'aria-label="1 setting needs attention"' in machines.text
    settings = client.get("/settings")
    assert 'needs-attention" data-section="accounts"' in settings.text
    assert "section-alert" in settings.text
    accounts = settings.text.split('data-section="accounts"', 1)[1].split('data-section="machines"', 1)[0]
    assert "badge-attention" in accounts
    assert "not set" in accounts
    assert 'needs-attention" data-section="smb"' not in settings.text


def test_settings_nav_badge_counts_smb_and_accounts(client, monkeypatch):
    login(client)
    monkeypatch.delenv("PXE_SMB_PASSWORD", raising=False)
    from src.settings import clear_settings_cache
    from src.smb_runtime import password_path

    clear_settings_cache()
    persisted = password_path()
    if persisted.exists():
        persisted.unlink()
    page = client.get("/settings")
    assert 'aria-label="2 settings need attention"' in page.text
    assert 'needs-attention" data-section="smb"' in page.text
    assert 'needs-attention" data-section="accounts"' in page.text
    smb = page.text.split('data-section="smb"', 1)[1].split('data-section="nfs"', 1)[0]
    assert "badge-attention" in smb


def test_settings_attention_clears_after_accounts_saved(client):
    login(client)
    saved = client.post(
        "/settings/accounts",
        data={
            "linux_username": "root",
            "linux_password": "linux-default-pass",
            "windows_username": "Administrator",
            "windows_password": "windows-default-pass",
        },
        follow_redirects=False,
    )
    assert saved.status_code in {302, 303}
    assert "section=accounts" in saved.headers.get("location", "")
    page = client.get("/machines")
    assert 'aria-label="1 setting needs attention"' not in page.text
    settings = client.get("/settings")
    assert "needs-attention" not in settings.text
    assert "linux-default-pass" not in settings.text
    assert "windows-default-pass" not in settings.text


def test_load_settings_attention_counts_sections(client):
    from src.db import session_scope

    with session_scope() as db:
        flags = load_settings_attention(db)
    assert flags.smb_password is False
    assert flags.linux_account is True
    assert flags.windows_account is True
    assert flags.accounts is True
    assert flags.count == 1
