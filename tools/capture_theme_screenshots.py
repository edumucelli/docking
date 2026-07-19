#!/usr/bin/env python3
"""Capture the live Docking desktop once for every installed theme.

The tool is intended for updating screenshots such as ``images/all.png``. It
temporarily shows the MATE desktop, moves the pointer away from the dock,
restarts Docking with each theme, and captures a monitor-width strip at the
bottom of the screen. The original Docking configuration, process, desktop
visibility state, and pointer position are restored before the tool exits.

Run it from the repository root::

    .venv/bin/python tools/capture_theme_screenshots.py

By default the PNG files are written to ``images/themes``. Pass
theme names to capture only a subset, or use ``--output-dir`` and ``--height``
to customize the result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, Gtk, Wnck

ROOT = Path(__file__).resolve().parents[1]
BUILTIN_THEMES_DIR = ROOT / "docking/assets/themes"
DEFAULT_CONFIG_PATH = Path.home() / ".config/docking/dock.json"
DEFAULT_OUTPUT_DIR = ROOT / "images/themes"
DEFAULT_CAPTURE_HEIGHT = 512
DOCK_STOP_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class CaptureRect:
    """Root-window rectangle captured for one theme."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class PointerPosition:
    """Pointer state restored after all screenshots are complete."""

    screen: Gdk.Screen
    x: int
    y: int


@dataclass(frozen=True)
class DockLaunch:
    """Information needed to relaunch the user's existing Docking process."""

    pid: int
    argv: tuple[str, ...]
    cwd: Path
    environ: dict[str, str]


def _theme_names(*, config_path: Path) -> list[str]:
    names = {path.stem for path in BUILTIN_THEMES_DIR.glob("*.json")}
    user_dir = config_path.parent / "themes"
    names.update(
        path.stem for path in user_dir.glob("*.json") if path.stem != "template"
    )
    return sorted(names)


def _capture_config(original: dict[str, Any], *, theme: str) -> dict[str, Any]:
    """Return the temporary, deterministic configuration for one capture."""
    updated = dict(original)
    updated.update(
        {
            "theme": theme,
            "position": "bottom",
            "hide_mode": "none",
            "startup_tips_enabled": False,
            "update_check_enabled": False,
        }
    )
    return updated


def _bottom_capture_rect(
    *,
    monitor_x: int,
    monitor_y: int,
    monitor_width: int,
    monitor_height: int,
    requested_height: int,
) -> CaptureRect:
    height = min(max(requested_height, 1), monitor_height)
    return CaptureRect(
        x=monitor_x,
        y=monitor_y + monitor_height - height,
        width=monitor_width,
        height=height,
    )


def _safe_theme_filename(theme: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", theme).strip("-.")
    if not safe:
        raise ValueError(f"Theme name has no safe filename characters: {theme!r}")
    return f"{safe}.png"


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_theme_config(
    *,
    path: Path,
    original: dict[str, Any],
    theme: str,
    mode: int,
) -> None:
    content = json.dumps(
        _capture_config(original, theme=theme),
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    _atomic_write(path, content + b"\n", mode=mode)


def _gtk_initialized() -> bool:
    result = Gtk.init_check()
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def _drain_gtk_events() -> None:
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def _refresh_screen(screen: Wnck.Screen) -> None:
    _drain_gtk_events()
    screen.force_update()


def _dock_window(screen: Wnck.Screen, *, pid: int | None = None) -> Wnck.Window | None:
    _refresh_screen(screen)
    for window in screen.get_windows():
        if window.get_window_type() != Wnck.WindowType.DOCK:
            continue
        if window.get_name() != "Docking":
            continue
        if pid is not None and window.get_pid() != pid:
            continue
        return window
    return None


def _read_process_launch(*, pid: int) -> DockLaunch:
    proc_dir = Path("/proc") / str(pid)
    argv_parts = (proc_dir / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    if not argv_parts:
        raise RuntimeError(f"Could not read the Docking command line for PID {pid}")
    argv = [part.decode("utf-8", errors="surrogateescape") for part in argv_parts]
    argv[0] = str((proc_dir / "exe").resolve())
    environ_parts = (proc_dir / "environ").read_bytes().rstrip(b"\0").split(b"\0")
    environ: dict[str, str] = {}
    for part in environ_parts:
        if not part or b"=" not in part:
            continue
        key, value = part.split(b"=", 1)
        environ[key.decode(errors="surrogateescape")] = value.decode(
            errors="surrogateescape"
        )
    return DockLaunch(
        pid=pid,
        argv=tuple(argv),
        cwd=(proc_dir / "cwd").resolve(),
        environ=environ,
    )


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_existing_process(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + DOCK_STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"Docking PID {pid} did not stop after SIGTERM")


def _stop_child(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=DOCK_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Temporary Docking PID {process.pid} did not stop after SIGTERM"
        ) from error


def _start_dock(launch: DockLaunch) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        launch.argv,
        cwd=launch.cwd,
        env=launch.environ,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_for_process_settle(
    process: subprocess.Popen[bytes], *, seconds: float
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Temporary Docking process exited with status {process.returncode}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def _pointer_position() -> tuple[Gdk.Device | None, PointerPosition | None]:
    display = Gdk.Display.get_default()
    if display is None:
        return None, None
    seat = display.get_default_seat()
    pointer = seat.get_pointer() if seat is not None else None
    if pointer is None:
        return None, None
    screen, x, y = pointer.get_position()
    if screen is None:
        return pointer, None
    return pointer, PointerPosition(screen=screen, x=x, y=y)


def _move_pointer_away(
    pointer: Gdk.Device | None, position: PointerPosition | None
) -> None:
    if pointer is None or position is None:
        return
    pointer.warp(position.screen, 1, 1)


def _restore_pointer(
    pointer: Gdk.Device | None, position: PointerPosition | None
) -> None:
    if pointer is None or position is None:
        return
    pointer.warp(position.screen, position.x, position.y)


def _request_shutdown(_signum: int, _frame: object) -> None:
    """Turn SIGTERM into an exception so the restoration path still runs."""
    raise KeyboardInterrupt


def _exit_without_native_teardown(exit_code: int) -> NoReturn:
    """Exit after explicit restoration without destructing stale Wnck wrappers."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


def _capture_rect_for_dock(
    *,
    dock_window: Wnck.Window,
    height: int,
) -> CaptureRect:
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError("The X11 display is unavailable")

    dock_x, dock_y, dock_width, dock_height = dock_window.get_geometry()
    monitor = display.get_monitor_at_point(
        dock_x + max(dock_width // 2, 0),
        dock_y + max(dock_height // 2, 0),
    )
    if monitor is None:
        raise RuntimeError("Could not find the monitor containing Docking")
    geometry = monitor.get_geometry()
    return _bottom_capture_rect(
        monitor_x=geometry.x,
        monitor_y=geometry.y,
        monitor_width=geometry.width,
        monitor_height=geometry.height,
        requested_height=height,
    )


def _capture_theme(
    *,
    theme: str,
    output_path: Path,
    rect: CaptureRect,
) -> None:
    root_window = Gdk.get_default_root_window()
    if root_window is None:
        raise RuntimeError("The X11 root window is unavailable")
    pixbuf = Gdk.pixbuf_get_from_window(
        root_window,
        rect.x,
        rect.y,
        rect.width,
        rect.height,
    )
    if pixbuf is None:
        raise RuntimeError(f"Could not capture the desktop for theme {theme!r}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixbuf.savev(str(output_path), "png", [], [])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "themes",
        nargs="*",
        help="Theme names to capture (default: every installed theme)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Docking config path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Screenshot directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_CAPTURE_HEIGHT,
        help=f"Bottom crop height in pixels (default: {DEFAULT_CAPTURE_HEIGHT})",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Wait after each dock appears before capturing (default: 2.0)",
    )
    parser.add_argument(
        "--list-themes",
        action="store_true",
        help="Print installed theme names without touching the desktop",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config_path = args.config.expanduser().resolve()
    available_themes = _theme_names(config_path=config_path)
    if args.list_themes:
        print("\n".join(available_themes))
        return 0

    selected_themes = args.themes or available_themes
    unknown = sorted(set(selected_themes) - set(available_themes))
    if unknown:
        raise SystemExit(f"Unknown theme(s): {', '.join(unknown)}")
    if args.height <= 0:
        raise SystemExit("--height must be greater than zero")
    if args.settle_seconds < 0:
        raise SystemExit("--settle-seconds cannot be negative")
    if not config_path.is_file():
        raise SystemExit(f"Docking config does not exist: {config_path}")
    if not _gtk_initialized():
        raise SystemExit("Could not connect to the graphical desktop session")

    screen = Wnck.Screen.get_default()
    if screen is None:
        raise SystemExit("Could not connect to the MATE window manager")
    existing_window = _dock_window(screen)
    if existing_window is None or existing_window.get_pid() <= 0:
        raise SystemExit("Could not find a running Docking dock window")

    launch = _read_process_launch(pid=existing_window.get_pid())
    original_config_bytes = config_path.read_bytes()
    original_config = json.loads(original_config_bytes.decode("utf-8"))
    config_mode = config_path.stat().st_mode & 0o777
    capture_rect = _capture_rect_for_dock(
        dock_window=existing_window,
        height=args.height,
    )
    original_showing_desktop = bool(screen.get_showing_desktop())
    pointer, pointer_position = _pointer_position()
    output_dir = args.output_dir.expanduser().resolve()
    temporary_dock: subprocess.Popen[bytes] | None = None
    original_dock_stopped = False
    signal.signal(signal.SIGTERM, _request_shutdown)

    try:
        screen.toggle_showing_desktop(True)
        _move_pointer_away(pointer, pointer_position)
        _drain_gtk_events()
        time.sleep(0.5)

        _stop_existing_process(launch.pid)
        original_dock_stopped = True

        for theme in selected_themes:
            _write_theme_config(
                path=config_path,
                original=original_config,
                theme=theme,
                mode=config_mode,
            )
            temporary_dock = _start_dock(launch)
            _move_pointer_away(pointer, pointer_position)
            _wait_for_process_settle(
                temporary_dock,
                seconds=args.settle_seconds,
            )
            _drain_gtk_events()
            output_path = output_dir / _safe_theme_filename(theme)
            _capture_theme(
                theme=theme,
                output_path=output_path,
                rect=capture_rect,
            )
            print(f"Captured {theme}: {output_path}")
            _stop_child(temporary_dock)
            temporary_dock = None

        print(f"Captured {len(selected_themes)} theme(s) in {output_dir}")
    finally:
        try:
            _stop_child(temporary_dock)
        finally:
            try:
                _atomic_write(
                    config_path,
                    original_config_bytes,
                    mode=config_mode,
                )
                if original_dock_stopped:
                    restored_dock = _start_dock(launch)
                    _wait_for_process_settle(restored_dock, seconds=0.5)
            finally:
                screen.toggle_showing_desktop(original_showing_desktop)
                _restore_pointer(pointer, pointer_position)
                _drain_gtk_events()

    _exit_without_native_teardown(0)


if __name__ == "__main__":
    exit_code = main()
    _exit_without_native_teardown(exit_code)
