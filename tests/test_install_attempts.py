from datetime import UTC, datetime

from sqlmodel import select
from tests.conftest import login

from src.extract_worker import gc_extract_generations
from src.inventory.service import create_image, deploy_machine, get_open_attempt, mark_deployed, register_machine
from src.models import InstallAttempt, OsFamily


def test_attempt_pins_paths_across_image_edit(client):
    login(client)
    from src.db import session_scope

    with session_scope() as db:
        image = create_image(
            db,
            name="pin-linux",
            os_family=OsFamily.linux,
            kernel_path="ubuntu/vmlinuz",
            initrd_path="ubuntu/initrd",
            cmdline="quiet",
            actor="admin",
        )
        machine = register_machine(db, mac="02:00:00:00:00:41", actor="admin")
        deploy_machine(db, machine, image=image, actor="admin")
        attempt = get_open_attempt(db, machine)
        assert attempt is not None
        assert attempt.kernel_path == "ubuntu/vmlinuz"
        assert attempt.cmdline == "quiet"
        image.kernel_path = "other/vmlinuz"
        image.cmdline = "changed"
        db.add(image)
        db.commit()
        again = get_open_attempt(db, machine)
        assert again is not None
        assert again.kernel_path == "ubuntu/vmlinuz"
        assert again.cmdline == "quiet"
        mark_deployed(db, machine, actor="admin")
        db.commit()
        assert get_open_attempt(db, machine) is None


def test_gc_keeps_open_revision(client, tmp_path):
    login(client)
    from src.db import session_scope
    from src.models import Image

    root = tmp_path / "images"
    old = root / "uploads" / "1" / "extracts" / "1"
    new = root / "uploads" / "1" / "extracts" / "2"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "kernel").write_bytes(b"old")
    (new / "kernel").write_bytes(b"new")
    with session_scope() as db:
        image = create_image(db, name="gc", os_family=OsFamily.linux, actor="admin")
        image.extract_revision = 2
        image.extract_generation = "uploads/1/extracts/2"
        db.add(image)
        machine = register_machine(db, mac="02:00:00:00:00:42", actor="admin")
        db.add(
            InstallAttempt(
                machine_id=int(machine.id),
                instance_id=machine.instance_id,
                image_id=image.id,
                os_family="linux",
                extract_revision=1,
                kernel_path="uploads/1/extracts/1/kernel",
            )
        )
        db.commit()
        gc_extract_generations(db, image)
    assert old.exists()
    assert new.exists()
    with session_scope() as db:
        image = db.get(Image, 1)
        for row in db.exec(select(InstallAttempt)).all():
            row.completed_at = datetime.now(UTC)
            db.add(row)
        db.commit()
        gc_extract_generations(db, image)
    assert not old.exists()
    assert new.exists()
