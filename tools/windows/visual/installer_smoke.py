"""Exercise per-user setup, installed desktop, upgrades, startup and removal."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path

from packaging.version import Version

from docking.platform.backends.windows.shortcuts import read_shortcut
from tools.windows.bundle import filesystem_path, native_path, payload_root, walk_tree
from tools.windows.installer import removal_manifest
from tools.windows.release import archive_identity, digest

APP_ID = "org.docking.Docking"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def registry_value(key: str, name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    if not isinstance(value, str):
        raise ValueError("Unexpected installer registration type")
    return value


def run_setup(installer: Path, root: Path, startup: bool | None = None) -> int:
    arguments = [native_path(installer), "/S"]
    if startup is not None:
        arguments.append(f"/STARTUP={int(startup)}")
    # NSIS consumes /D as the unquoted remainder of its command line.
    command = subprocess.list2cmdline(arguments) + " /D=" + native_path(root)
    return subprocess.run(command, timeout=600).returncode


def disable_owned_startup(root: Path) -> None:
    """Leave a failed acceptance installation available without enabling login."""
    import winreg

    if registry_value(RUN_KEY, APP_ID) != '"' + native_path(root / "Docking.exe") + '"':
        return
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        RUN_KEY,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY,
    ) as handle:
        winreg.DeleteValue(handle, APP_ID)


def programs_directory() -> Path:
    shell = ctypes.WinDLL("shell32")
    shell.SHGetFolderPathW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
    ]
    shell.SHGetFolderPathW.restype = ctypes.c_int32
    result = ctypes.create_unicode_buffer(32768)
    if shell.SHGetFolderPathW(None, 2, None, 0, result) != 0:
        raise OSError("Could not locate the user's Start menu")
    return Path(result.value)


def verify_installation(
    root: Path, expected: dict[str, str | int] | None = None
) -> tuple[Path, int]:
    payload = payload_root(root)
    inventory_bytes = (payload / "files.sha256.json").read_bytes()
    inventory = json.loads(inventory_bytes)
    removal_manifest(list(inventory))
    for name, checksum in inventory.items():
        actual = hashlib.sha256(
            filesystem_path(payload / name).read_bytes()
        ).hexdigest()
        if actual != checksum:
            raise AssertionError(f"Installed file does not match the artifact: {name}")
    registered = registry_value(UNINSTALL_KEY, "InstallLocation")
    assert registered is not None and Path(registered).samefile(root)
    shortcut = programs_directory() / "Docking/Docking.lnk"
    assert read_shortcut(shortcut).executable.samefile(root / "Docking.exe")
    if expected is not None:
        metadata = json.loads(
            (payload / "runtime/share/docking/runtime.json").read_text(encoding="utf-8")
        )
        actual = {
            "payload_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
            "wheel_sha256": metadata["wheel_sha256"],
            "version": metadata["version"],
            "payload_files": len(inventory),
        }
        assert all(actual[key] == expected[key] for key in actual), (
            "Installed payload differs from the supplied portable archive"
        )
        assert registry_value(UNINSTALL_KEY, "DisplayVersion") == expected["version"]
    return payload, len(inventory)


def uninstall(root: Path, output: Path) -> int:
    # The normal stub copies itself to Temp. An explicit copy plus _?= lets us
    # wait for the real uninstaller while its installed executable is unlocked.
    with tempfile.TemporaryDirectory(prefix="docking-uninstall-") as temp:
        executable = Path(temp) / "Uninstall.exe"
        shutil.copy2(root / "Uninstall.exe", executable)
        command = (
            subprocess.list2cmdline([native_path(executable), "/S"])
            + " _?="
            + native_path(root)
        )
        with (output / "uninstall.log").open("w", encoding="utf-8") as log:
            return subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, timeout=600
            ).returncode


def exercise(
    installer: Path,
    output: Path,
    report: dict[str, object],
    *,
    archive: Path,
    previous_installer: Path,
    previous_archive: Path,
) -> None:
    import psutil

    assert registry_value(UNINSTALL_KEY, "InstallLocation") is None, (
        "Preserve the existing Docking installation"
    )
    assert registry_value(RUN_KEY, APP_ID) is None, (
        "Preserve the existing startup entry"
    )
    candidate = {**archive_identity(archive), "installer_sha256": digest(installer)}
    previous = {
        **archive_identity(previous_archive),
        "installer_sha256": digest(previous_installer),
    }
    assert Version(str(previous["version"])) < Version(str(candidate["version"])), (
        "Upgrade acceptance requires a distinct older installed version"
    )
    report.update(acceptance_schema=1, candidate=candidate, previous=previous)
    root = (
        Path(os.environ["LOCALAPPDATA"])
        / "Programs"
        / ("Docking acceptance 日本語 " + uuid.uuid4().hex[:8])
    )
    assert not root.exists()
    report["root"] = str(root)
    report["installer"] = str(installer)
    report["installer_sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
    assert run_setup(previous_installer, root, startup=True) == 0
    first, count = verify_installation(root, previous)
    report["previous_verified_payload_files"] = count
    expected_startup = '"' + native_path(root / "Docking.exe") + '"'
    assert registry_value(RUN_KEY, APP_ID) == expected_startup
    root_note = root / "acceptance-user-note.txt"
    payload_note = first / "runtime/acceptance-user-note.txt"
    for path in (root_note, payload_note):
        path.write_text("Preserve unknown files", encoding="utf-8")

    # A real stdin hook keeps the installed interpreter in use without opening
    # another dock. Setup must reject it without terminating it or switching.
    before = (root / "current.ini").read_bytes()
    with subprocess.Popen(
        [str(root / "docking-hook.exe"), "claude", "Stop"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as hook:
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                children = psutil.Process(hook.pid).children()
                if any(
                    Path(child.exe()) == first / "runtime/bin/python.exe"
                    for child in children
                ):
                    break
                assert hook.poll() is None
                time.sleep(0.1)
            else:
                raise AssertionError(
                    "Installed usage hook did not start its interpreter"
                )
            assert run_setup(installer, root) == 10
            assert uninstall(root, output) == 10
            assert (root / "current.ini").read_bytes() == before
            assert hook.poll() is None
        finally:
            _stdout, stderr = hook.communicate(b"{}", timeout=20)
        assert hook.returncode == 0, stderr.decode("utf-8", errors="replace")
    report["in_use_upgrade_rejected"] = True
    report["in_use_uninstall_rejected"] = True

    assert run_setup(installer, root) == 0
    second, count = verify_installation(root, candidate)
    report["verified_payload_files"] = count
    assert second != first and (first / "Docking.exe").is_file()
    assert registry_value(RUN_KEY, APP_ID) == expected_startup
    assert payload_note.read_text(encoding="utf-8") == "Preserve unknown files"
    report["upgrade_preserved_startup_and_previous_payload"] = True
    report["distinct_version_upgrade"] = True
    assert run_setup(installer, root, startup=False) == 0
    final, count = verify_installation(root, candidate)
    assert final != second and registry_value(RUN_KEY, APP_ID) is None
    report["startup_disabled"] = True
    report["verified_final_payload_files"] = count

    for module, directory in (("bundle_smoke", "runtime"), ("smoke", "desktop")):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.windows.visual." + module,
                "--bundle",
                str(root),
                "--output",
                str(output / directory),
            ],
            check=True,
        )
    preferences = output / "desktop/config/docking/dock.json"
    saved = preferences.read_bytes()
    assert json.loads(saved)["icon_size"] == 64
    report["installed_desktop"] = True
    assert uninstall(root, output) == 0
    assert registry_value(UNINSTALL_KEY, "InstallLocation") is None
    assert registry_value(RUN_KEY, APP_ID) is None
    assert not (programs_directory() / "Docking/Docking.lnk").exists()
    assert not (root / "Docking.exe").exists()
    assert preferences.read_bytes() == saved
    for path in (root_note, payload_note):
        assert path.read_text(encoding="utf-8") == "Preserve unknown files"
    report["uninstall_preserved_preferences_and_unknown_files"] = True
    # Remove only our two sentinels, then empty directories. Additional data
    # would stay available for inspection rather than being recursively erased.
    root_note.unlink()
    payload_note.unlink()
    for path in sorted(
        walk_tree(filesystem_path(root)), key=lambda item: len(item.parts), reverse=True
    ):
        if path.is_dir():
            with suppress(OSError):
                path.rmdir()
    root.rmdir()
    assert archive_identity(archive) == {
        key: value for key, value in candidate.items() if key != "installer_sha256"
    }
    assert digest(installer) == candidate["installer_sha256"]
    assert archive_identity(previous_archive) == {
        key: value for key, value in previous.items() if key != "installer_sha256"
    }
    assert digest(previous_installer) == previous["installer_sha256"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--previous-installer", type=Path, required=True)
    parser.add_argument("--previous-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error(
            "Requires a native Windows desktop without an existing Docking installation"
        )
    import psutil

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    try:
        exercise(
            args.installer.resolve(),
            output,
            report,
            archive=args.archive.resolve(),
            previous_installer=args.previous_installer.resolve(),
            previous_archive=args.previous_archive.resolve(),
        )
    except (
        AssertionError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        psutil.Error,
    ) as exc:
        report["error"] = str(exc)
        root = report.get("root")
        if isinstance(root, str):
            try:
                disable_owned_startup(Path(root))
            except OSError as cleanup_error:
                report["startup_cleanup_error"] = str(cleanup_error)
        raise
    finally:
        (output / "result.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
