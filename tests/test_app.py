from tests.conftest import login


def test_console_flow_create_image_and_deploy(client):
    login(client)
    created = client.post(
        "/images",
        data={
            "name": "ubuntu-console",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
        },
        follow_redirects=False,
    )
    assert created.status_code in {302, 303}
    client.get("/ipxe/aa-bb-cc-dd-ee-01")
    machines = client.get("/api/machines").json()
    machine_id = machines[0]["id"]
    images_page = client.get("/images")
    assert "ubuntu-console" in images_page.text
    deploy = client.post(
        f"/machines/{machine_id}/deploy",
        data={"image_id": "1", "username": "root", "password": "console-pass"},
        follow_redirects=False,
    )
    assert deploy.status_code in {302, 303}
    detail = client.get(f"/api/machines/{machine_id}").json()
    assert detail["state"] == "deploying"
    assert "console-pass" not in str(detail)
    ipxe = client.get("/ipxe/aa-bb-cc-dd-ee-01").text
    assert "kernel" in ipxe
    assert "console-pass" not in ipxe
