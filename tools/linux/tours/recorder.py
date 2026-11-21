"""Record the Docking tours from this checkout in a private X11 desktop."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path

from tests.visual.backends.x11.run import (
    ROOT,
    Observer,
    SessionDirectory,
    private_environment,
    process,
    stop_process_group,
    wait_for,
)

WIDTH, HEIGHT = 1920, 1080
ICON_SIZE = 48
BAR_WIDTH, BAR_HEIGHT = 830, 100
TOURS = ("overview", "applets", "preferences")
DEMO_APP = "docking-tour.desktop"
DEMO_APP_NAME = "Tour Demo"
FOLDER_NAME = "Tour files"
DEMO_WINDOWS = ("Tour Notes", "Tour Checklist")


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def overlaps(self, other: Box) -> bool:
        return (
            self.x < other.x + other.width
            and other.x < self.x + self.width
            and self.y < other.y + other.height
            and other.y < self.y + self.height
        )


def bar_position(obstacles: list[Box]) -> Box:
    for y in (36, 150, 264):
        for x in ((WIDTH - BAR_WIDTH) // 2, 48, WIDTH - BAR_WIDTH - 48):
            bar = Box(x, y, BAR_WIDTH, BAR_HEIGHT)
            if not any(bar.overlaps(obstacle) for obstacle in obstacles):
                return bar
    raise RuntimeError("No open desktop area for the context bar")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    title: str
    detail: str
    bar: Box


def ass_time(seconds: float) -> str:
    centiseconds = round(max(0, seconds) * 100)
    minutes, remainder = divmod(centiseconds, 6000)
    whole, fraction = divmod(remainder, 100)
    return f"{minutes // 60}:{minutes % 60:02}:{whole:02}.{fraction:02}"


def subtitle_events(cue: Cue) -> str:
    bar = cue.bar
    start, end = ass_time(cue.start), ass_time(cue.end)
    box = (
        rf"{{\an7\pos({bar.x},{bar.y})\p1\c&H201B18&\alpha&H18&}}"
        rf"m 0 0 l {bar.width} 0 {bar.width} {bar.height} 0 {bar.height}"
    )
    # The strings are checked-in captions and catalog names. ASS treats braces
    # and physical newlines as override syntax and event boundaries.
    title_text, detail_text = (
        " ".join(text.splitlines()).translate(str.maketrans("{}\\", "()／"))
        for text in (cue.title, cue.detail)
    )
    title = rf"{{\pos({bar.x + 22},{bar.y + 12})\fs32}}{title_text}"
    detail = rf"{{\pos({bar.x + 23},{bar.y + 55})}}{detail_text}"
    return "".join(
        f"Dialogue: {layer},{start},{end},Tour,{line}\n"
        for layer, line in ((0, box), (1, title), (1, detail))
    )


def write_subtitles(path: Path, cues: list[Cue]) -> None:
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\nPlayResY: {HEIGHT}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: Tour,DejaVu Sans,23,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    )
    path.write_text(header + "".join(map(subtitle_events, cues)), encoding="utf-8")


def ffmpeg(*args: str, **kwargs):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        **kwargs,
    )


def snapshot(path: Path) -> None:
    ffmpeg(
        "-f",
        "x11grab",
        "-video_size",
        f"{WIDTH}x{HEIGHT}",
        "-i",
        os.environ["DISPLAY"],
        "-frames:v",
        "1",
        "-threads",
        "1",
        str(path),
        timeout=15,
    )


class Desktop:
    def __init__(self, observer: Observer):
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        Atspi.init()
        self.atspi = Atspi
        self.observer = observer
        self.pointer = observer.pointer
        self.dock_pid = 0
        self.cursor = (WIDTH // 2, HEIGHT // 2)
        self.cues: list[Cue] = []
        self.started = 0.0
        self.scene_start = 0.0
        self.scene_name = ""
        self.obstacles: list[Box] = []

    def item(self, desktop_id: str) -> tuple[int, int]:
        def anchor():
            with suppress(self.observer.GLib.Error):
                found, x, y, position = self.observer.remote(
                    "GetHoverAnchor", "(s)", (desktop_id,)
                )
                if found and position == "bottom":
                    offset = (
                        1
                        if desktop_id.startswith("applet://separator")
                        else ICON_SIZE // 2
                    )
                    return x + offset, y + ICON_SIZE // 2
            return None

        return wait_for(anchor, f"Dock item did not appear: {desktop_id}")

    def move(self, x: int, y: int) -> None:
        start_x, start_y = self.cursor
        if self.cursor == (x, y):
            return
        for step in range(1, 16):
            progress = step / 15
            eased = progress**2 * (3 - 2 * progress)
            self.pointer.move(
                round(start_x + (x - start_x) * eased),
                round(start_y + (y - start_y) * eased),
            )
            time.sleep(0.015)
        self.cursor = x, y

    def point(self, desktop_id: str, button: int = 0) -> None:
        self.move(*self.item(desktop_id))
        if button:
            self.pointer.click(button)

    def key(self, name: str) -> None:
        keyval = self.observer.Gdk.keyval_from_name(name)
        keycode = self.pointer.xlib.XKeysymToKeycode(self.pointer.display, keyval)
        if not keycode:
            raise RuntimeError(f"Key cannot be typed: {name}")
        for pressed in (1, 0):
            self.pointer.xtst.XTestFakeKeyEvent(
                self.pointer.display, keycode, pressed, 0
            )
        self.pointer.xlib.XFlush(self.pointer.display)

    def dismiss(self) -> None:
        self.key("Escape")
        self.move(WIDTH // 2, HEIGHT // 2)

    def launch_demo(self) -> None:
        self.point(DEMO_APP, 1)

    def shelf_menu(self) -> None:
        x, y = self.item(DEMO_APP)
        self.move(x - ICON_SIZE // 2 - 2, y)
        control = self.observer.Gdk.KEY_Control_L
        keycode = self.pointer.xlib.XKeysymToKeycode(self.pointer.display, control)
        self.pointer.xtst.XTestFakeKeyEvent(self.pointer.display, keycode, 1, 0)
        try:
            self.pointer.click(3)
        finally:
            self.pointer.xtst.XTestFakeKeyEvent(self.pointer.display, keycode, 0, 0)
            self.pointer.xlib.XFlush(self.pointer.display)

    def controls(self):
        atspi = self.atspi
        root = atspi.get_desktop(0)
        for index in range(root.get_child_count()):
            with suppress(self.observer.GLib.Error):
                app = root.get_child_at_index(index)
                if app and app.get_process_id() == self.dock_pid:
                    yield from self._visible_children(app)

    def _visible_children(self, parent):
        atspi = self.atspi
        for index in range(parent.get_child_count()):
            with suppress(self.observer.GLib.Error):
                child = parent.get_child_at_index(index)
                if child is None or not child.get_state_set().contains(
                    atspi.StateType.SHOWING
                ):
                    continue
                component = child.get_component_iface()
                if component is not None:
                    rect = component.get_extents(atspi.CoordType.SCREEN)
                    if (
                        rect.width > 0
                        and rect.height > 0
                        and rect.x >= 0
                        and rect.y >= 0
                    ):
                        yield child, Box(rect.x, rect.y, rect.width, rect.height)
                yield from self._visible_children(child)

    def find(self, label: str, *, contains: bool = False) -> Box:
        def match():
            for node, box in self.controls():
                name = node.get_name()
                if name and (label in name if contains else name == label):
                    return box
            return None

        return wait_for(match, f"Visible Docking control did not appear: {label}")

    def click(self, label: str) -> None:
        self.move(*self.find(label).center)
        self.pointer.click()

    def wait_role(self, role: str, *, popup: bool = False):
        return wait_for(
            lambda: next(
                (
                    node
                    for node, _ in self.controls()
                    if node.get_role_name() == role
                    and (not popup or self.in_popup(node))
                ),
                None,
            ),
            f"Docking {role} did not appear",
        )

    @staticmethod
    def in_popup(node) -> bool:
        parent = node.get_parent()
        while parent and parent.get_role_name() not in {
            "window",
            "frame",
            "application",
        }:
            parent = parent.get_parent()
        return bool(parent and parent.get_role_name() == "window")

    def popup_labels(self) -> list[str]:
        return [
            node.get_name()
            for node, _ in self.controls()
            if node.get_role_name() == "label"
            and self.in_popup(node)
            and node.get_name()
        ]

    def menu_labels(self) -> list[str]:
        return [
            node.get_name()
            for node, _ in self.controls()
            if node.get_role_name().endswith("menu item") and node.get_name()
        ]

    def show_submenus(self, menu=None) -> int:
        if menu is None:
            menu = self.wait_role("menu")
        opened = 0
        for item, box in list(self._visible_children(menu)):
            if item.get_parent() != menu or item.get_child_count() == 0:
                continue
            if item.get_role() not in {self.atspi.Role.MENU, self.atspi.Role.MENU_ITEM}:
                continue
            self.move(*box.center)

            wait_for(
                lambda item=item: list(self._visible_children(item)),
                f"Submenu did not open: {item.get_name()}",
            )
            self.hold(0.9)
            opened += 1 + self.show_submenus(item)
            self.move(*box.center)
        return opened

    def window(self, title: str):
        return wait_for(
            lambda: next(
                (
                    window
                    for window in self.observer.windows()
                    if title == window.get_name()
                ),
                None,
            ),
            f"Window did not open: {title}",
        )

    def wait_active(self, title: str, *, contains: bool = False):
        def active():
            self.observer.windows()
            window = self.observer.screen.get_active_window()
            if window is None:
                return None
            name = window.get_name() or ""
            if title in name if contains else title == name:
                return window
            return None

        return wait_for(active, f"Window did not become active: {title}")

    def move_below_bar(self, window) -> None:
        x, y, width, height = window.get_geometry()
        if y < 155 or y + height >= HEIGHT - 80:
            wnck = self.observer.Wnck
            mask = (
                wnck.WindowMoveResizeMask.Y
                | wnck.WindowMoveResizeMask.HEIGHT
                | wnck.WindowMoveResizeMask.X
                | wnck.WindowMoveResizeMask.WIDTH
            )
            window.set_geometry(
                wnck.WindowGravity.CURRENT,
                mask,
                max(10, x),
                160,
                min(width, WIDTH - 20),
                min(height, HEIGHT - 250),
            )
            time.sleep(0.3)
            # The window may briefly occupy the bar's space as it maps.
            self.scene_start = time.monotonic() - self.started

    def hold(self, seconds: float = 1.5) -> None:
        time.sleep(0.2)
        self.obstacles.extend(
            Box(*window.get_geometry())
            for window in self.observer.windows()
            if not window.is_minimized()
            and window.get_window_type() == self.observer.Wnck.WindowType.NORMAL
        )
        self.obstacles.extend(
            box
            for node, box in self.controls()
            if node.get_role_name() in {"menu", "tool tip", "dialog", "window"}
        )
        time.sleep(seconds)

    @contextmanager
    def scene(self, title: str, detail: str) -> Iterator[None]:
        self.scene_name = title
        self.obstacles.clear()
        self.scene_start = time.monotonic() - self.started
        yield
        end = time.monotonic() - self.started
        self.cues.append(
            Cue(self.scene_start, end, title, detail, bar_position(self.obstacles))
        )


@contextmanager
def capture(desktop: Desktop, directory: Path) -> Iterator[None]:
    progress = directory / "progress.txt"
    recording = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-stats_period",
        "0.05",
        "-progress",
        str(progress),
        "-f",
        "x11grab",
        "-draw_mouse",
        "1",
        "-framerate",
        "30",
        "-video_size",
        f"{WIDTH}x{HEIGHT}",
        "-i",
        os.environ["DISPLAY"],
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        str(directory / "raw.mkv"),
    ]
    desktop.cues.clear()
    with (
        (directory / "capture.log").open("w") as log,
        subprocess.Popen(
            recording,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            start_new_session=True,
        ) as recorder,
    ):
        try:

            def recording_time():
                if recorder.poll() is not None:
                    raise RuntimeError(f"FFmpeg exited. See {log.name}")
                if progress.exists():
                    for line in reversed(progress.read_text().splitlines()):
                        if line.startswith("out_time_us="):
                            value = line.partition("=")[2]
                            if value.isdecimal() and int(value) > 0:
                                return int(value) / 1_000_000
                return None

            elapsed = wait_for(recording_time, "FFmpeg did not capture a frame")
            desktop.started = time.monotonic() - elapsed
            yield
            recorder.communicate(b"q\n", timeout=15)
            if recorder.returncode:
                raise RuntimeError(f"FFmpeg could not finish capture. See {log.name}")
        finally:
            if recorder.poll() is None:
                stop_process_group(recorder)
    write_subtitles(directory / "captions.ass", desktop.cues)
    desktop.scene_name = "Encoding " + directory.name
    ffmpeg(
        "-i",
        "raw.mkv",
        "-vf",
        "ass=captions.ass",
        "-an",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-movflags",
        "+faststart",
        "clip.mp4",
        cwd=directory,
    )
    (directory / "raw.mkv").unlink()


def seed_desktop() -> str:
    data = Path(os.environ["XDG_DATA_HOME"])
    applications = data / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    executable = shlex.quote(sys.executable)
    (applications / DEMO_APP).write_text(
        f"[Desktop Entry]\nType=Application\nName={DEMO_APP_NAME}\n"
        f"Exec={executable} -m tools.linux.tours --demo\n"
        "Icon=accessories-text-editor\nStartupWMClass=DockingTour\n",
        encoding="utf-8",
    )
    folder = Path.home() / FOLDER_NAME
    folder.mkdir(exist_ok=True)
    for name in ("Ideas.txt", "Reading list.txt", "Tour checklist.txt"):
        (folder / name).write_text(f"{name.removesuffix('.txt')}\n", encoding="utf-8")
    trash = data / "Trash"
    for name in ("files", "info"):
        (trash / name).mkdir(parents=True, exist_ok=True)
    (trash / "files" / "Old draft.txt").write_text("An old demo draft\n")
    (trash / "info" / "Old draft.txt.trashinfo").write_text(
        "[Trash Info]\nPath=/tmp/Old%20draft.txt\nDeletionDate=2026-01-01T10:00:00\n"
    )
    return folder.as_uri()


def demo_windows() -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    GLib.set_prgname("DockingTour")
    windows = set()

    def close(window):
        windows.remove(window)
        if not windows:
            Gtk.main_quit()

    for index, (title, content) in enumerate(
        zip(
            DEMO_WINDOWS,
            (
                "Tour notes\n\nTry the window previews and folder stacks.",
                "Tour checklist\n\n• Open an app\n• Switch between its windows",
            ),
            strict=True,
        )
    ):
        window = Gtk.Window(title=title)
        window.set_wmclass("DockingTour", "DockingTour")
        window.set_default_size(480, 270)
        window.move(430 + 570 * index, 265 + 130 * index)
        window.add(Gtk.Label(label=content))
        window.connect("destroy", close)
        windows.add(window)
        window.show_all()
    Gtk.main()


def background_window() -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    window = Gtk.Window()
    window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
    window.set_decorated(False)
    window.set_accept_focus(False)
    window.set_default_size(WIDTH, HEIGHT)
    window.move(0, 0)
    color = Gtk.CssProvider()
    color.load_from_data(b"window { background-color: #27394d; }")
    window.get_style_context().add_provider(
        color, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    window.show_all()
    Gtk.main()


@contextmanager
def dock(
    desktop: Desktop,
    directory: Path,
    base: dict,
    items: list[str],
    *,
    tooltips: bool = True,
) -> Iterator[None]:
    path = Path(os.environ["XDG_CONFIG_HOME"]) / "docking" / "dock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        **base,
        "pinned": items,
        "icon_size": ICON_SIZE,
        "position": "bottom",
        "hide_mode": "none",
        "zoom_enabled": False,
        "tooltips_enabled": tooltips,
        "stack_unfold": "click",
        "window_preview_thumbnails_enabled": True,
        "window_preview_highlight_thumbnails_on_hover": True,
        "update_check_enabled": False,
        "startup_tips_enabled": False,
        "show_recent_apps": False,
        "global_search_enabled": False,
    }
    path.write_text(json.dumps(config), encoding="utf-8")
    with process(
        [sys.executable, str(ROOT / "run.py")],
        directory / "dock.log",
        dict(os.environ),
        cwd=Path.home(),
    ) as child:
        desktop.dock_pid = child.pid
        try:
            desktop.item(items[0])
            yield
            if child.poll() is not None:
                raise RuntimeError(f"Docking exited with status {child.returncode}")
        except Exception:
            with suppress(OSError, subprocess.SubprocessError):
                snapshot(desktop.observer.output / "failure.png")
            raise


def join_chapters(directory: Path, chapters: list[tuple[str, Path]]) -> Path:
    playlist, metadata = directory / "chapters.txt", directory / "chapters.ffmeta"
    playlist.write_text(
        "".join(
            f"file {clip.relative_to(directory).as_posix()}\n" for _, clip in chapters
        )
    )
    entries = [";FFMETADATA1"]
    start = 0
    for name, clip in chapters:
        duration = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(clip),
            ],
            text=True,
        )
        end = start + round(float(duration) * 1000)
        entries.extend(
            (
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start}",
                f"END={end}",
                f"title={name}",
            )
        )
        start = end
    metadata.write_text("\n".join(entries) + "\n", encoding="utf-8")
    result = directory / "applets.mp4"
    ffmpeg(
        "-f",
        "concat",
        "-i",
        playlist.name,
        "-i",
        metadata.name,
        "-map_chapters",
        "1",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        result.name,
        cwd=directory,
    )
    return result


def session(directory: Path, tours: list[str]) -> None:
    from docking.applets.identity import applet_desktop_id
    from tools.linux.tours.scenes import (
        OVERVIEW_ITEMS,
        applet_chapters,
        applets,
        overview,
        preferences,
    )

    config = Path(os.environ["XDG_CONFIG_HOME"]) / "docking" / "dock.json"
    base = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(base, dict):
        raise RuntimeError("The prepared dock.json must contain a configuration object")
    applet_prefs = base.get("applet_prefs")
    if not isinstance(applet_prefs, dict):
        applet_prefs = base["applet_prefs"] = {}
    bluetooth_prefs = applet_prefs.get("bluetooth")
    if not isinstance(bluetooth_prefs, dict):
        bluetooth_prefs = {}
    applet_prefs["bluetooth"] = {**bluetooth_prefs, "continuous_discovery": False}
    applet_prefs.setdefault(
        "quicknote",
        {"note": "Tour checklist\n• Open a window preview\n• Browse the applets"},
    )
    if "overview" in tours:
        applet_prefs.setdefault(
            "weather", {"city_display": "Paris, France", "lat": 48.8566, "lng": 2.3522}
        )
    folder = seed_desktop()
    env = dict(os.environ)
    startup = env.get("DOCKING_TOUR_STARTUP")
    with (
        process(["marco", "--composite", "--sm-disable"], directory / "wm.log", env),
        process(
            [sys.executable, "-m", "tools.linux.tours", "--background"],
            directory / "background.log",
            env,
        ),
        process([startup], directory / "startup.log", env)
        if startup
        else nullcontext(),
    ):
        observer = Observer(directory, None)
        try:
            wait_for(observer.composited, "Marco did not start compositing")
            observer.check_window_mapping()
            desktop = Desktop(observer)
            for tour in tours:
                desktop.scene_name = f"Starting the {tour} tour"
                print(desktop.scene_name, flush=True)
                try:
                    if tour == "applets":
                        chapters = []
                        for name, items in applet_chapters():
                            clip_dir = directory / f"chapter-{len(chapters) + 1}"
                            clip_dir.mkdir()
                            pins = [applet_desktop_id(meta.id) for meta in items]
                            with (
                                dock(desktop, clip_dir, base, pins),
                                capture(desktop, clip_dir),
                            ):
                                applets(desktop, items)
                            chapters.append((name, clip_dir / "clip.mp4"))
                        join_chapters(directory, chapters)
                    else:
                        clip_dir = directory / tour
                        clip_dir.mkdir()
                        entries = [DEMO_APP, folder]
                        if tour == "overview":
                            entries.extend(OVERVIEW_ITEMS)
                        else:
                            entries.extend(
                                map(applet_desktop_id, ("applications", "clock"))
                            )
                        with (
                            dock(
                                desktop,
                                clip_dir,
                                base,
                                entries,
                                tooltips=tour != "preferences",
                            ),
                            capture(desktop, clip_dir),
                        ):
                            if tour == "overview":
                                overview(desktop, folder)
                            else:
                                preferences(desktop)
                        (clip_dir / "clip.mp4").rename(directory / f"{tour}.mp4")
                except Exception as error:
                    try:
                        if not (directory / "failure.png").exists():
                            snapshot(directory / "failure.png")
                    except (OSError, subprocess.SubprocessError):
                        print(
                            "Could not capture the failure screenshot", file=sys.stderr
                        )
                    raise RuntimeError(f"{desktop.scene_name}: {error}") from error
        finally:
            observer.close()


def copy_profile(source: Path, target: Path, env: dict[str, str]) -> None:
    def ignore_special_files(directory: str, names: list[str]) -> list[str]:
        paths = (Path(directory) / name for name in names)
        return [
            path.name
            for path in paths
            if path.is_symlink()
            or not (path.is_dir() or path.is_file())
            or (
                path.parent == source / "data" / "docking"
                and path.name
                in {
                    "telegram",
                    "whatsapp",
                }
            )
        ]

    for name in ("home", "config", "data", "state"):
        path = source / name
        if path.is_dir() and not path.is_symlink():
            shutil.copytree(
                path, target / name, dirs_exist_ok=True, ignore=ignore_special_files
            )
    startup = source / "startup"
    if startup.is_file() and not startup.is_symlink():
        private_startup = target / startup.name
        shutil.copy2(startup, private_startup)
        env["DOCKING_TOUR_STARTUP"] = str(private_startup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, help="Prepared demo profile directory")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "tours")
    parser.add_argument(
        "--tour", choices=TOURS, action="append", help="Record selected tours"
    )
    parser.add_argument("--session", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    tours = list(dict.fromkeys(args.tour or TOURS))
    if (args.session or args.demo or args.background) and os.environ.get(
        "DOCKING_TOUR_PRIVATE"
    ) != "1":
        parser.error("The internal entry point requires a private tour session")
    if args.demo:
        demo_windows()
        return 0
    if args.background:
        background_window()
        return 0
    if args.session:
        session(output, tours)
        return 0
    for executable in (
        "Xvfb",
        "xvfb-run",
        "xauth",
        "dbus-run-session",
        "marco",
        "ffmpeg",
        "ffprobe",
    ):
        if shutil.which(executable) is None:
            parser.error(f"Missing executable: {executable}")
    if "applets" in tours and args.profile is None:
        parser.error("The complete applet tour requires --profile DIRECTORY")
    profile = args.profile.resolve() if args.profile else None
    if profile and not profile.is_dir():
        parser.error(f"Demo profile does not exist: {profile}")
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="recording-", dir=output))
    with SessionDirectory(prefix="docking-tour-") as private:
        env = private_environment(Path(private), "mate")
        for key in (
            "NO_AT_BRIDGE",
            "GIO_USE_VFS",
            "AT_SPI_BUS_ADDRESS",
            "XAUTHORITY",
            "SSH_AUTH_SOCK",
            "GNOME_KEYRING_CONTROL",
            "DOCKING_TOUR_STARTUP",
        ):
            env.pop(key, None)
        env["DOCKING_TOUR_PRIVATE"] = "1"
        env["DOCKING_LOG_LEVEL"] = "INFO"
        try:
            if profile:
                copy_profile(profile, Path(private), env)
            subprocess.run(
                [
                    "xvfb-run",
                    "-a",
                    "-e",
                    str(run / "xvfb.log"),
                    "-s",
                    f"-screen 0 {WIDTH}x{HEIGHT}x24 -nolisten tcp",
                    "dbus-run-session",
                    "--",
                    sys.executable,
                    "-m",
                    "tools.linux.tours",
                    "--session",
                    "--output",
                    str(run),
                    *[option for tour in tours for option in ("--tour", tour)],
                ],
                cwd=ROOT,
                env=env,
                check=True,
            )
            for tour in tours:
                (run / f"{tour}.mp4").replace(output / f"{tour}.mp4")
                print(output / f"{tour}.mp4")
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            for clip in run.rglob("raw.mkv"):
                clip.unlink(missing_ok=True)
            print(f"Tour generation failed: {error}. Evidence: {run}", file=sys.stderr)
            return 1
    shutil.rmtree(run)
    return 0
