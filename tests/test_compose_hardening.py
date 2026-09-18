from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_seccomp_allows_ganesha_fhandle():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "no-new-privileges:true" in text
    assert "seccomp:unconfined" in text


def test_pxe_smoke_docker_run_seccomp_unconfined():
    text = (ROOT / ".github/workflows/pxe-smoke.yml").read_text(encoding="utf-8")
    assert "no-new-privileges:true" in text
    assert "seccomp=unconfined" in text


def test_docker_publish_smoke_seccomp_unconfined():
    text = (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    assert "no-new-privileges:true" in text
    assert "seccomp=unconfined" in text
