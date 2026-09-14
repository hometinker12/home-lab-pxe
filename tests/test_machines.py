from tests.conftest import login


def test_add_machine_by_mac(client):
    login(client)
    created = client.post(
        "/machines",
        data={"mac": "DE-AD-BE-EF-10-01", "hostname": "lab-node"},
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    assert created.headers["location"].startswith("/machines/")
    listing = client.get("/api/machines").json()
    assert listing[0]["mac"] == "de:ad:be:ef:10:01"
    assert listing[0]["hostname"] == "lab-node"
    assert listing[0]["state"] == "pending"
    page = client.get("/machines")
    assert "Add machine" in page.text
    assert "lab-node" in page.text


def test_add_machine_rejects_duplicate_and_invalid_mac(client):
    login(client)
    first = client.post(
        "/machines",
        data={"mac": "02:00:00:00:00:aa"},
        follow_redirects=False,
    )
    assert first.status_code in {302, 303}
    duplicate = client.post("/machines", data={"mac": "02:00:00:00:00:aa"})
    assert duplicate.status_code == 200
    assert "already exists" in duplicate.text
    invalid = client.post("/machines", data={"mac": "not-a-mac"})
    assert invalid.status_code == 200
    assert "12 hex digits" in invalid.text
