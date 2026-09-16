from tests.conftest import login

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_login_page_includes_logo_and_favicon(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert 'rel="icon"' in page.text
    assert 'href="/static/favicon.png"' in page.text
    assert 'src="/static/logo.png"' in page.text
    assert 'src="/static/favicon.png"' in page.text
    assert "brand-mark" in page.text


def test_header_logo_on_authenticated_console(client):
    login(client)
    page = client.get("/machines")
    assert page.status_code == 200
    assert "/static/favicon.png" in page.text
    assert "home-lab-pxe" in page.text
    assert 'href="/files"' in page.text


def test_brand_static_files_and_favicon_route(client):
    logo = client.get("/static/logo.png")
    assert logo.status_code == 200
    assert logo.content.startswith(PNG_MAGIC)
    favicon = client.get("/static/favicon.png")
    assert favicon.status_code == 200
    assert favicon.content.startswith(PNG_MAGIC)
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200
    assert ico.content.startswith(PNG_MAGIC)
    assert ico.content == favicon.content
