# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Privileged camera device lock helper for Cam Shield.

This module is intentionally small and command-shaped. The GTK applet remains
unprivileged and calls this helper through ``pkexec`` only when the user asks to
lock or unlock camera devices.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from docking.applets.camshield.backend import create_backend
from docking.core.paths import ensure_dir

STATE_DIR = Path("/var/lib/docking/camshield")
STATE_FILE = STATE_DIR / "devices.json"
DEV_ROOT = Path("/dev")

_STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """Stored device state needed to restore a camera node."""

    path: Path
    mode: int
    acl_file: Path | None = None


def discover_video_devices(*, dev_root: Path = DEV_ROOT) -> tuple[Path, ...]:
    """Return camera device nodes this helper is allowed to operate on."""
    try:
        entries = tuple(dev_root.iterdir())
    except OSError:
        return ()

    devices: list[Path] = []
    for entry in entries:
        if entry.name.startswith("video") and entry.name[5:].isdigit():
            devices.append(entry)
    return tuple(sorted(devices, key=lambda path: path.name))


def lock_devices(
    *,
    dev_root: Path = DEV_ROOT,
    state_dir: Path = STATE_DIR,
) -> int:
    """Lock current camera devices by removing all mode permissions."""
    _require_root()
    devices = discover_video_devices(dev_root=dev_root)
    if not devices:
        print("No camera devices found.", file=sys.stderr)
        return 1

    ensure_dir(state_dir)
    state = _read_state(state_dir=state_dir)
    changed = 0
    for device in devices:
        if not device.exists():
            continue
        device_key = str(device)
        if device_key not in state:
            state[device_key] = _snapshot_device(device=device, state_dir=state_dir)
        # Persist recovery data before removing access, including after a crash.
        _write_state(state=state, state_dir=state_dir)
        device.chmod(0)
        changed += 1

    _write_state(state=state, state_dir=state_dir)
    print(f"Locked {changed} camera device(s).")
    return 0


def unlock_devices(*, state_dir: Path = STATE_DIR) -> int:
    """Restore camera devices recorded by the last lock operation."""
    _require_root()
    state = _read_state(state_dir=state_dir)
    if not state:
        print("No locked camera devices recorded.", file=sys.stderr)
        return 1

    restored = 0
    failed: dict[str, DeviceSnapshot] = {}
    for device_path, snapshot in state.items():
        device = Path(device_path)
        if not _is_allowed_device_path(device) or not device.exists():
            continue
        device.chmod(snapshot.mode)
        if _restore_acl(snapshot=snapshot):
            restored += 1
            continue
        if (
            create_backend().requires_acl_backup(device)
            and snapshot.acl_file is not None
        ):
            failed[device_path] = snapshot
            continue
        device.chmod(snapshot.mode)
        restored += 1

    if failed:
        _write_state(state=failed, state_dir=state_dir)
        print("Camera ACL restoration failed; recovery data retained.", file=sys.stderr)
        return 1
    _clear_state(state_dir=state_dir)
    print(f"Unlocked {restored} camera device(s).")
    return 0


def status_devices(
    *,
    dev_root: Path = DEV_ROOT,
    state_dir: Path = STATE_DIR,
) -> int:
    """Print machine-readable current helper status."""
    devices = discover_video_devices(dev_root=dev_root)
    state = _read_state(state_dir=state_dir)
    payload = {
        "available": bool(devices),
        "locked": any(_device_locked(device) for device in devices),
        "devices": [
            {
                "path": str(device),
                "mode": f"{stat.S_IMODE(device.stat().st_mode):04o}",
                "locked": _device_locked(device),
                "recorded": str(device) in state,
            }
            for device in devices
            if device.exists()
        ],
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Camera lock/unlock must be run as root.")


def _snapshot_device(*, device: Path, state_dir: Path) -> DeviceSnapshot:
    mode = stat.S_IMODE(device.stat().st_mode)
    acl_file = _save_acl_snapshot(device=device, state_dir=state_dir)
    if acl_file is None and create_backend().requires_acl_backup(device):
        raise OSError(f"Cannot save the camera ACL: {device}")
    return DeviceSnapshot(path=device, mode=mode, acl_file=acl_file)


def _save_acl_snapshot(*, device: Path, state_dir: Path) -> Path | None:
    getfacl = shutil.which("getfacl")
    if getfacl is None:
        return None

    acl_dir = state_dir / "acl"
    ensure_dir(acl_dir)
    acl_file = acl_dir / f"{device.name}.acl"
    try:
        with acl_file.open("w", encoding="utf-8") as handle:
            subprocess.run(
                [getfacl, *create_backend().acl_commands(device, acl_file)[0][1:]],
                check=True,
                stdout=handle,
                stderr=subprocess.DEVNULL,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError):
        with suppress(OSError):
            acl_file.unlink()
        return None
    return acl_file


def _restore_acl(*, snapshot: DeviceSnapshot) -> bool:
    if snapshot.acl_file is None or not snapshot.acl_file.exists():
        return False
    setfacl = shutil.which("setfacl")
    if setfacl is None:
        return False
    try:
        for argv in create_backend().acl_commands(snapshot.path, snapshot.acl_file)[1]:
            subprocess.run(
                [setfacl, *argv[1:]],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _read_state(*, state_dir: Path) -> dict[str, DeviceSnapshot]:
    path = state_dir / STATE_FILE.name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise OSError("Cannot read saved camera recovery data") from exc

    if (
        not isinstance(raw, dict)
        or type(raw.get("version")) is not int
        or raw["version"] != _STATE_VERSION
    ):
        raise OSError("Unsupported camera recovery data")

    result: dict[str, DeviceSnapshot] = {}
    devices = raw.get("devices")
    if not isinstance(devices, dict):
        raise OSError("Invalid camera recovery data")

    for device_path, data in devices.items():
        if not isinstance(device_path, str) or not isinstance(data, dict):
            raise OSError("Invalid device snapshot in camera recovery data")
        device = Path(device_path)
        if not _is_allowed_device_path(device):
            raise OSError("Invalid device path in camera recovery data")
        mode = data.get("mode")
        if type(mode) is not int or not 0 <= mode <= 0o7777:
            raise OSError("Invalid device mode in camera recovery data")
        if "acl_file" not in data:
            raise OSError("Missing ACL snapshot in camera recovery data")
        acl_raw = data["acl_file"]
        if acl_raw is not None and (
            not isinstance(acl_raw, str) or not Path(acl_raw).is_absolute()
        ):
            raise OSError("Invalid ACL snapshot in camera recovery data")
        acl_file = Path(acl_raw) if isinstance(acl_raw, str) else None
        result[str(device)] = DeviceSnapshot(
            path=device,
            mode=mode,
            acl_file=acl_file,
        )
    return result


def _write_state(*, state: dict[str, DeviceSnapshot], state_dir: Path) -> None:
    payload: dict[str, object] = {
        "version": _STATE_VERSION,
        "devices": {
            device_path: {
                "mode": snapshot.mode,
                "acl_file": str(snapshot.acl_file) if snapshot.acl_file else None,
            }
            for device_path, snapshot in sorted(state.items())
        },
    }
    _atomic_write_json(state_dir / STATE_FILE.name, payload)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                temporary.unlink()


def _clear_state(*, state_dir: Path) -> None:
    with suppress(OSError):
        (state_dir / STATE_FILE.name).unlink()


def _device_locked(device: Path) -> bool:
    try:
        mode = stat.S_IMODE(device.stat().st_mode)
    except OSError:
        return False
    return mode & 0o777 == 0


def _is_allowed_device_path(path: Path) -> bool:
    return (
        path.parent == DEV_ROOT
        and path.name.startswith("video")
        and path.name[5:].isdigit()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docking-camshield-helper",
        description="Lock or unlock /dev/video* camera devices for Docking.",
    )
    parser.add_argument("command", choices=("lock", "unlock", "status"))
    args = parser.parse_args(argv)

    try:
        backend = create_backend()
        if args.command == "lock":
            return backend.lock()
        if args.command == "unlock":
            return backend.unlock()
        return backend.status()
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 77
    except OSError as exc:
        print(f"Camera helper failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
