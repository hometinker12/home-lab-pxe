from tests.conftest import login


def test_cross_origin_login_is_rejected(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret"},
        headers={"Origin": "http://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code in {303, 403}
    assert "session" not in response.cookies


def test_same_origin_login_sets_cookie(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret"},
        headers={"Origin": "http://testserver", "Referer": "http://testserver/login"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert response.cookies.get("session")


def test_authenticated_api_post_is_not_csrf_exempt(client):
    login(client)
    client.get("/ipxe/02-00-00-00-00-21")
    machines = client.get("/api/machines").json()
    mid = machines[0]["id"]
    response = client.post(
        f"/machines/{mid}/stage",
        headers={"Origin": "http://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code in {303, 403}
