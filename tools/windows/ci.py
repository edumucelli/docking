"""Native Windows CI entry points, shared with the interactive Windows lab."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

_SHARED_TESTS = (
    "tests/core",
    "tests/platform/applications/test_windows.py",
    "tests/platform/applications/test_visibility.py",
    "tests/platform/applications/test_shell_identity_paths.py",
    "tests/platform/applications/test_listing.py",
    "tests/platform/applications/test_recents.py",
    "tests/platform/test_model.py",
    "tests/platform/test_application_icons.py",
    "tests/search/test_icons.py",
    "tests/applets/test_applications.py",
    "tests/applets/test_battery.py",
    "tests/ui/test_application_menu_icons.py",
    "tests/ui/test_hover_integration.py",
    "tests/ui/test_preview.py",
    "tests/ui/test_preview_artwork.py",
    "tests/ui/test_preview_popup_integration.py",
    "tests/ui/test_dnd.py",
    "tests/ui/test_dnd_integration.py",
)


def test_paths(root: Path) -> list[str]:
    """Include every Windows backend/applet test, plus portable shared contracts."""
    paths = {
        str(path.relative_to(root)).replace("\\", "/")
        for pattern in (
            "tests/platform/test_windows*.py",
            "tests/applets/*windows*.py",
            "tests/test_windows*.py",
        )
        for path in root.glob(pattern)
    }
    paths.update(_SHARED_TESTS)
    return sorted(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("preflight", "tests", "gtk"), default="tests"
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/windows"))
    parser.add_argument("--list", action="store_true", help="List native test targets")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.list:
        print("\n".join(test_paths(root)))
        return
    if sys.platform != "win32" or os.name != "nt":
        parser.error("Use native Windows Python, not MSYS Unix Python or WSL")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GDK_BACKEND", "win32")
    os.environ.setdefault("GSETTINGS_BACKEND", "memory")
    os.environ.setdefault("GTK_THEME", "Adwaita")
    from docking.launcher import prepare_display

    prepare_display()
    if args.stage == "preflight":
        import ctypes

        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gdk, Gst, Gtk

        assert Gtk.init_check()[0], "Native Windows GTK could not initialize"
        display = Gdk.Display.get_default()
        assert display is not None and display.__gtype__.name == "GdkWin32Display"
        assert ctypes.sizeof(ctypes.c_void_p) == 8, "This job targets 64-bit Windows"
        Gst.init(None)
        facts = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": sys.version,
            "executable": sys.executable,
            "gdk_backend": display.__gtype__.name,
            "gtk": [
                Gtk.get_major_version(),
                Gtk.get_minor_version(),
                Gtk.get_micro_version(),
            ],
            "gstreamer": Gst.version_string(),
        }
        (output / "environment.json").write_text(
            json.dumps(facts, indent=2), encoding="utf-8"
        )
        print(json.dumps(facts, indent=2))
        return
    report = output / f"{args.stage}-junit.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"--junitxml={report}",
        "-o",
        f"cache_dir={output / 'pytest-cache'}",
    ]
    if args.stage == "gtk":
        command += [
            "tests/visual/test_preview_gtk.py",
            "-m",
            "visual",
            "-o",
            "addopts=",
        ]
    else:
        command += test_paths(root)
    with tempfile.TemporaryDirectory(prefix="docking-windows-ci-") as temporary:
        command.append(f"--basetemp={Path(temporary) / 'tests'}")
        status = subprocess.run(command, cwd=root).returncode
    raise SystemExit(status)


if __name__ == "__main__":
    main()
