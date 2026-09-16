"""Pinned boot artifacts for one install attempt."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Image, InstallAttempt, OsFamily


@dataclass(frozen=True)
class BootPayload:
    image_id: int
    os_family: str
    kernel_path: str = ""
    initrd_path: str = ""
    boot_wim_path: str = ""
    install_wim_path: str = ""
    iso_path: str = ""
    cmdline: str = ""
    wim_index: int = 1
    media_relative: str = ""
    extract_revision: int = 0
    extract_status: str = "idle"

    @classmethod
    def from_image(cls, image: Image) -> BootPayload:
        return cls(
            image_id=int(image.id or 0),
            os_family=image.os_family,
            kernel_path=image.kernel_path or "",
            initrd_path=image.initrd_path or "",
            boot_wim_path=image.boot_wim_path or "",
            install_wim_path=image.install_wim_path or "",
            iso_path=image.iso_path or "",
            cmdline=image.cmdline or "",
            wim_index=int(image.wim_index or 1),
            media_relative=(image.extract_generation or "").strip(),
            extract_revision=int(image.extract_revision or 0),
            extract_status=(image.extract_status or "idle"),
        )

    @classmethod
    def from_attempt(cls, attempt: InstallAttempt) -> BootPayload:
        family = attempt.os_family or OsFamily.linux.value
        return cls(
            image_id=int(attempt.image_id or 0),
            os_family=family,
            kernel_path=attempt.kernel_path or "",
            initrd_path=attempt.initrd_path or "",
            boot_wim_path=attempt.boot_wim_path or "",
            install_wim_path=attempt.install_wim_path or "",
            iso_path=attempt.iso_path or "",
            cmdline=attempt.cmdline or "",
            wim_index=int(attempt.wim_index or 1),
            media_relative=attempt.media_relative or "",
            extract_revision=int(attempt.extract_revision or 0),
            extract_status="ready",
        )
