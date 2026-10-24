"""Linux camera/process inspection and camera-helper integration."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from docking.applets.camshield.backend import CamshieldBackend
from docking.applets.camshield.state import (
    CameraHolder,
    CamshieldState,
    _normalized_path,
    _video_devices,
)
from docking.i18n import _


def _probe_camera_state(
    *,
    dev_root: Path | str = "/dev",
    proc_root: Path | str = "/proc",
) -> CamshieldState:
    """Return whether any process currently holds a ``/dev/video*`` device."""
    video_devices = _video_devices(Path(dev_root))
    if not video_devices:
        return CamshieldState(available=False, active=False)

    holders = _camera_holders(
        proc_root=Path(proc_root),
        video_devices=video_devices,
    )
    return CamshieldState(
        available=True,
        active=bool(holders),
        devices=tuple(sorted(video_devices)),
        holders=tuple(sorted(holders, key=lambda holder: (holder.command, holder.pid))),
    )


def _camera_holders(
    *,
    proc_root: Path,
    video_devices: dict[str, Path],
) -> list[CameraHolder]:
    by_pid: dict[int, set[str]] = defaultdict(set)

    try:
        proc_entries = tuple(proc_root.iterdir())
    except OSError:
        proc_entries = ()

    for proc_entry in proc_entries:
        if not proc_entry.name.isdigit():
            continue
        pid = int(proc_entry.name)
        fd_dir = proc_entry / "fd"
        try:
            fd_entries = tuple(fd_dir.iterdir())
        except OSError:
            continue

        for fd_entry in fd_entries:
            target_name = _fd_video_device_name(fd_entry, video_devices)
            if target_name is not None:
                by_pid[pid].add(target_name)

    holders: list[CameraHolder] = []
    for pid, devices in by_pid.items():
        holders.append(
            CameraHolder(
                pid=pid,
                command=_process_command(proc_root / str(pid)),
                devices=tuple(sorted(devices)),
            )
        )
    return holders


def _fd_video_device_name(
    fd_entry: Path,
    video_devices: dict[str, Path],
) -> str | None:
    try:
        target = str(fd_entry.readlink())
    except OSError:
        return None

    if target.endswith(" (deleted)"):
        target = target[: -len(" (deleted)")]

    target_path = _normalized_path(Path(target))
    for name, device_path in video_devices.items():
        if target_path == device_path:
            return name
    return None


def _process_command(proc_entry: Path) -> str:
    comm = _read_first_line(proc_entry / "comm")
    if comm:
        return comm

    cmdline = _read_text(proc_entry / "cmdline")
    if cmdline:
        first = cmdline.split("\0", 1)[0]
        if first:
            return Path(first).name

    return _("Unknown")


def _read_first_line(path: Path) -> str:
    text = _read_text(path)
    if not text:
        return ""
    return text.splitlines()[0].strip()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


class LinuxCamshieldBackend(CamshieldBackend):
    def requires_acl_backup(self, device: Path) -> bool:
        return False

    def probe(self, *, dev_root: Path, proc_root: Path) -> CamshieldState:
        return _probe_camera_state(dev_root=dev_root, proc_root=proc_root)

    def lock(self) -> int:
        from docking.applets.camshield.helper import lock_devices

        return lock_devices()

    def unlock(self) -> int:
        from docking.applets.camshield.helper import unlock_devices

        return unlock_devices()

    def status(self) -> int:
        from docking.applets.camshield.helper import status_devices

        return status_devices()

    def acl_commands(
        self, device: Path, acl_file: Path
    ) -> tuple[list[str], list[list[str]]]:
        return (
            ["getfacl", "--absolute-names", str(device)],
            [["setfacl", "--restore", str(acl_file)]],
        )
