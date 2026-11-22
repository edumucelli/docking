"""Record the Docking tours from this checkout in a private X11 desktop."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path

from docking.applets.identity import applet_desktop_id
from docking.platform.host.selection import host_services
from docking.search.config import DEFAULT_GLOBAL_SEARCH_SHORTCUT
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
TOURS = ("overview", "applets", "preferences", "search")
FOLDER_NAME = "Tour files"
NOTES_FOLDER_NAME = "Tour notes"
OVERVIEW_TRACKS = ("Daybreak", "Afterglow")
OVERVIEW_WEATHER_CITIES = (
    {"city_display": "Paris, France", "lat": 48.8566, "lng": 2.3522},
    {"city_display": "Belo Horizonte, Brazil", "lat": -19.92, "lng": -43.94},
)


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
    button: int | None = None
    mouse_actions: tuple[tuple[float, int], ...] = ()
    shortcut: str = ""


def ass_time(seconds: float) -> str:
    centiseconds = round(max(0, seconds) * 100)
    minutes, remainder = divmod(centiseconds, 6000)
    whole, fraction = divmod(remainder, 100)
    return f"{minutes // 60}:{minutes % 60:02}:{whole:02}.{fraction:02}"


def subtitle_events(cue: Cue) -> str:
    bar = cue.bar
    start, end = ass_time(cue.start), ass_time(cue.end)
    width, height = bar.width, bar.height
    radius, bend = 18, 10
    box = (
        rf"{{\an7\pos({bar.x},{bar.y})\p1\c&H201B18&\alpha&H18&}}"
        f"m {radius} 0 l {width - radius} 0 "
        f"b {width - radius + bend} 0 {width} {radius - bend} {width} {radius} "
        f"l {width} {height - radius} b {width} {height - radius + bend} "
        f"{width - radius + bend} {height} {width - radius} {height} "
        f"l {radius} {height} b {radius - bend} {height} 0 {height - radius + bend} "
        f"0 {height - radius} l 0 {radius} "
        f"b 0 {radius - bend} {radius - bend} 0 {radius} 0"
    )
    # The strings are checked-in captions and catalog names. ASS treats braces
    # and physical newlines as override syntax and event boundaries.
    title_text, detail_text, shortcut_text = (
        " ".join(text.splitlines()).translate(str.maketrans("{}\\", "()／"))
        for text in (cue.title, cue.detail, cue.shortcut)
    )
    title = rf"{{\pos({bar.x + 22},{bar.y + 12})\fs32}}{title_text}"
    detail = rf"{{\pos({bar.x + 23},{bar.y + 55})}}{detail_text}"
    layers = [(0, box), (1, title), (1, detail)]
    if shortcut_text:
        inset = 71 if cue.button == 3 else 24
        shortcut = rf"{{\an6\pos({bar.x + width - inset},{bar.y + 50})\fs26\b1}}"
        layers.append((1, f"{shortcut}{shortcut_text}"))
    highlights = []
    if cue.button in (1, 3) or cue.mouse_actions:
        mouse = rf"\an7\pos({bar.x + width - 61},{bar.y + 24})\p1"
        body = (
            "m 18 0 b 28 0 36 8 36 18 l 36 34 "
            "b 36 44 28 52 18 52 b 8 52 0 44 0 34 l 0 18 b 0 8 8 0 18 0"
        )
        buttons = {
            1: "m 16 4 b 9 5 4 11 4 18 l 4 23 16 23",
            3: "m 20 4 b 27 5 32 11 32 18 l 32 23 20 23",
        }
        if cue.mouse_actions:
            buttons[4] = "m 15 8 b 15 4 21 4 21 8 l 21 22 b 21 26 15 26 15 22"
        layers.append((1, rf"{{{mouse}\c&HEEEEEE&}}{body}"))
        for button, path in buttons.items():
            color = "F5AD60" if button == cue.button else "534940"
            layers.append((2, rf"{{{mouse}\c&H{color}&}}{path}"))
        for index, (when, button) in enumerate(cue.mouse_actions):
            first = max(cue.start, when)
            until = min(cue.end, first + 1)
            if index + 1 < len(cue.mouse_actions):
                until = min(until, cue.mouse_actions[index + 1][0])
            if round(first * 100) < round(until * 100):
                path = buttons[4 if button == 5 else button]
                highlights.append(
                    rf"Dialogue: 3,{ass_time(first)},{ass_time(until)},Tour,"
                    rf"{{{mouse}\c&HF5AD60&}}{path}" + "\n"
                )
    return "".join(
        f"Dialogue: {layer},{start},{end},Tour,{line}\n" for layer, line in layers
    ) + "".join(highlights)


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
        self.track_mouse = False
        self.mouse_actions: list[tuple[float, int]] = []
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

        return wait_for(anchor, f"Dock item did not appear: {desktop_id}", timeout=20)

    def move(self, x: int, y: int, *, duration: float = 0.225) -> None:
        start_x, start_y = self.cursor
        if self.cursor == (x, y):
            return
        steps = max(15, round(duration * 60))
        for step in range(1, steps + 1):
            progress = step / steps
            eased = progress**2 * (3 - 2 * progress)
            self.pointer.move(
                round(start_x + (x - start_x) * eased),
                round(start_y + (y - start_y) * eased),
            )
            time.sleep(duration / steps)
        self.cursor = x, y

    def point(self, desktop_id: str, button: int = 0) -> None:
        self.move(*self.item(desktop_id))
        if button:
            # Hover zoom can shift the item between the first move and the click.
            time.sleep(0.15)
            self.move(*self.item(desktop_id))
            self.press(button)

    def press(self, button: int = 1) -> None:
        self.pointer.click(button)
        if self.track_mouse and button in (1, 3, 4, 5):
            self.mouse_actions.append((time.monotonic() - self.started, button))

    def key(self, name: str, *, modifiers: tuple[str, ...] = ()) -> None:
        keycodes = []
        for symbol in (*modifiers, name):
            keyval = self.observer.Gdk.keyval_from_name(symbol)
            keycode = self.pointer.xlib.XKeysymToKeycode(self.pointer.display, keyval)
            if not keycode:
                raise RuntimeError(f"Key cannot be typed: {symbol}")
            keycodes.append(keycode)
        for keycode in keycodes:
            self.pointer.xtst.XTestFakeKeyEvent(self.pointer.display, keycode, 1, 0)
        for keycode in reversed(keycodes):
            self.pointer.xtst.XTestFakeKeyEvent(self.pointer.display, keycode, 0, 0)
        self.pointer.xlib.XFlush(self.pointer.display)

    def dismiss(self, *, recenter: bool = True) -> None:
        self.key("Escape")
        if recenter:
            self.move(WIDTH // 2, HEIGHT // 2)

    def focus_desktop(self) -> None:
        # The X11 shortcut needs input focus even on an empty desktop.
        xid = self.window("Docking tour desktop").get_xid()
        self.pointer.xlib.XSetInputFocus.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        revert_to_pointer_root = 1
        self.pointer.xlib.XSetInputFocus(
            self.pointer.display, xid, revert_to_pointer_root, 0
        )
        self.pointer.xlib.XFlush(self.pointer.display)

    def shelf_menu(self, anchor: str = applet_desktop_id("applications")) -> None:
        self.point(anchor)
        control = self.observer.Gdk.KEY_Control_L
        keycode = self.pointer.xlib.XKeysymToKeycode(self.pointer.display, control)
        self.pointer.xtst.XTestFakeKeyEvent(self.pointer.display, keycode, 1, 0)
        try:
            self.press(3)
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
        self.press()

    def wait_role(self, role: str, *, popup: bool = False, timeout: float = 12):
        def matching_role():
            for node, _ in self.controls():
                with suppress(self.observer.GLib.Error):
                    if node.get_role_name() == role and (
                        not popup or self.in_popup(node)
                    ):
                        return node
            return None

        return wait_for(
            matching_role, f"Docking {role} did not appear", timeout=timeout
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
        labels = []
        for node, _ in self.controls():
            with suppress(self.observer.GLib.Error):
                if (
                    node.get_role_name() == "label"
                    and self.in_popup(node)
                    and (name := node.get_name())
                ):
                    labels.append(name)
        return labels

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
        for node, box in self.controls():
            with suppress(self.observer.GLib.Error):
                if node.get_role_name() in {"menu", "tool tip", "dialog", "window"}:
                    self.obstacles.append(box)
        time.sleep(seconds)

    @contextmanager
    def scene(
        self,
        title: str,
        detail: str,
        *,
        button: int | None = None,
        shortcut: str = "",
    ) -> Iterator[None]:
        self.scene_name = title
        self.obstacles.clear()
        self.mouse_actions.clear()
        self.scene_start = time.monotonic() - self.started
        yield
        end = time.monotonic() - self.started
        self.cues.append(
            Cue(
                self.scene_start,
                end,
                title,
                detail,
                bar_position(self.obstacles),
                button,
                tuple(self.mouse_actions),
                shortcut,
            )
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
        "fps=30,ass=captions.ass",
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
    folder = Path.home() / FOLDER_NAME
    folder.mkdir(exist_ok=True)
    notes = folder / NOTES_FOLDER_NAME
    notes.mkdir(exist_ok=True)
    for file in (
        notes / "Ideas.txt",
        folder / "Reading list.txt",
        folder / "Tour checklist.txt",
    ):
        file.write_text(f"{file.stem}\n", encoding="utf-8")
    trash = data / "Trash"
    for name in ("files", "info"):
        (trash / name).mkdir(parents=True, exist_ok=True)
    (trash / "files" / "Old draft.txt").write_text("An old demo draft\n")
    (trash / "info" / "Old draft.txt.trashinfo").write_text(
        "[Trash Info]\nPath=/tmp/Old%20draft.txt\nDeletionDate=2026-01-01T10:00:00\n"
    )
    return folder.as_uri()


def background_window() -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf, Gtk

    window = Gtk.Window(title="Docking tour desktop")
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
    if wallpaper := os.environ.get("DOCKING_TOUR_WALLPAPER"):
        image = GdkPixbuf.Pixbuf.new_from_file(wallpaper)
        width, height = image.get_width(), image.get_height()
        scale = max(WIDTH / width, HEIGHT / height)

        def draw(_widget, cr):
            cr.translate((WIDTH - width * scale) / 2, (HEIGHT - height * scale) / 2)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, image, 0, 0)
            cr.paint()

        area = Gtk.DrawingArea()
        area.connect("draw", draw)
        window.add(area)
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
    pulse_server: str | None = None,
) -> Iterator[None]:
    path = Path(os.environ["XDG_CONFIG_HOME"]) / "docking" / "dock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        **base,
        "pinned": items,
        "icon_size": ICON_SIZE,
        "position": "bottom",
        "hide_mode": "none",
        "zoom_enabled": True,
        "tooltips_enabled": tooltips,
        "stack_unfold": "click",
        "window_preview_thumbnails_enabled": True,
        "window_preview_highlight_thumbnails_on_hover": True,
        "update_check_enabled": False,
        "startup_tips_enabled": False,
        "show_recent_apps": False,
        "global_search_enabled": applet_desktop_id("search") in items,
    }
    path.write_text(json.dumps(config), encoding="utf-8")
    env = dict(os.environ)
    if pulse_server:
        env["PULSE_SERVER"] = pulse_server
    with process(
        [sys.executable, str(ROOT / "run.py")],
        directory / "dock.log",
        env,
        cwd=Path.home(),
    ) as child:
        desktop.dock_pid = child.pid
        try:
            for item in items:
                desktop.item(item)
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


@contextmanager
def overview_audio(directory: Path) -> Iterator[str]:
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "tour-audio"
    runtime.mkdir(mode=0o700)
    server = f"unix:{runtime / 'native'}"
    env = dict(
        os.environ,
        PULSE_RUNTIME_PATH=str(runtime),
        PULSE_STATE_PATH=str(runtime),
        PULSE_SERVER=server,
    )
    with process(
        [
            "pulseaudio",
            "-n",
            "--daemonize=no",
            "--exit-idle-time=-1",
            "--use-pid-file=no",
            "--load=module-null-sink sink_name=docking_tour",
            "--load=module-native-protocol-unix auth-anonymous=1",
        ],
        directory / "audio.log",
        env,
    ):
        wait_for(
            lambda: (
                subprocess.run(
                    ["pactl", "get-default-sink"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=4,
                ).stdout.strip()
                == "docking_tour"
            ),
            "The private audio server did not start",
        )
        subprocess.run(
            ["pactl", "set-sink-volume", "docking_tour", "50%"], env=env, check=True
        )
        shutil.copyfile(
            ROOT / "docking/assets/icons/applets/music.png", directory / "cover.png"
        )
        tracks = []
        for title, frequency in zip(OVERVIEW_TRACKS, (440, 554), strict=True):
            track = directory / f"{title}.flac"
            # Leave time for live applets to load before the scene changes tracks.
            ffmpeg(
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=16000:duration=600",
                "-metadata",
                f"title={title}",
                "-metadata",
                "artist=Docking Demo",
                str(track),
            )
            tracks.append(str(track))
        with process(
            [
                "vlc",
                "--intf=dummy",
                "--extraintf=dbus",
                "--aout=pulse",
                "--ignore-config",
                "--no-one-instance",
                *tracks,
            ],
            directory / "vlc.log",
            env,
        ):
            yield server


def session(
    directory: Path, tours: list[str], *, available_controls: bool = False
) -> None:
    from docking.platform.applications.implementations.unix.catalog import (
        UnixApplicationCatalogBackend,
    )
    from tools.linux.tours.scenes import (
        OVERVIEW_APPLETS,
        applet_chapters,
        applets,
        input_then_hover,
        live_hover,
        overview,
        overview_items,
        preferences,
        prepare_live_applets,
        search,
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
    if {"overview", "applets"}.intersection(tours):
        applet_prefs.setdefault(
            "weather", {"city_display": "Paris, France", "lat": 48.8566, "lng": 2.3522}
        )
    if "overview" in tours:
        applet_prefs.setdefault(
            "certwatch", {"domains": [{"host": "www.eduar.do", "port": 443}]}
        )
        # MATE's keyboard backend queries settings in a separate process.
        os.environ["GSETTINGS_BACKEND"] = "keyfile"
    folder = seed_desktop()
    env = dict(os.environ)
    startup = env.get("DOCKING_TOUR_STARTUP")
    with (
        process(["marco", "--composite", "--sm-disable"], directory / "wm.log", env),
        process(
            [sys.executable, "-m", "tools.linux.tours", "--background"],
            directory / "background.log",
            env,
        ) as background,
        process([startup], directory / "startup.log", env)
        if startup
        else nullcontext(),
    ):
        observer = Observer(directory, None)
        try:
            wait_for(observer.composited, "Marco did not start compositing")
            observer.check_window_mapping()
            desktop = Desktop(observer)

            def background_ready():
                if background.poll() is not None:
                    raise RuntimeError(
                        f"Desktop background failed. See {directory / 'background.log'}"
                    )
                return any(
                    window.get_name() == "Docking tour desktop"
                    for window in observer.windows()
                )

            wait_for(background_ready, "Desktop background did not appear")
            for tour in tours:
                desktop.scene_name = f"Starting the {tour} tour"
                desktop.track_mouse = tour == "overview"
                print(desktop.scene_name, flush=True)
                try:
                    if tour == "applets":
                        chapters = []
                        for name, items in applet_chapters():
                            clip_dir = directory / f"chapter-{len(chapters) + 1}"
                            clip_dir.mkdir()
                            pins = [applet_desktop_id(meta.id) for meta in items]
                            with dock(desktop, clip_dir, base, pins):
                                prepare_live_applets(
                                    desktop,
                                    (
                                        meta.id
                                        for meta in items
                                        if not available_controls
                                        or meta.id in {"telegram", "whatsapp"}
                                    ),
                                )
                                desktop.point(pins[0])
                                with capture(desktop, clip_dir):
                                    applets(
                                        desktop,
                                        items,
                                        require_live_data=not available_controls,
                                    )
                                    (pinned,) = observer.remote("ListPinnedIds")
                                    if list(pinned) != pins:
                                        raise RuntimeError(
                                            f"Pinned applets changed during {name}"
                                        )
                            chapters.append((name, clip_dir / "clip.mp4"))
                        join_chapters(directory, chapters)
                    else:
                        clip_dir = directory / tour
                        clip_dir.mkdir()
                        settings = base
                        if tour == "overview":
                            subprocess.run(
                                [
                                    "gsettings",
                                    "set",
                                    "org.mate.peripherals-keyboard-xkb.kbd",
                                    "layouts",
                                    "['us', 'br']",
                                ],
                                check=True,
                            )
                            subprocess.run(
                                ["setxkbmap", "-layout", "us,br"], check=True
                            )
                            weather = applet_prefs.get("weather")
                            settings = {
                                **base,
                                "applet_prefs": {
                                    **applet_prefs,
                                    "weather": {
                                        **(
                                            weather if isinstance(weather, dict) else {}
                                        ),
                                        "cities": OVERVIEW_WEATHER_CITIES,
                                        "active_index": 0,
                                    },
                                },
                            }
                            catalog = UnixApplicationCatalogBackend().discover(
                                handle_epoch=0
                            )
                            entries = overview_items(
                                folder, {app.desktop_id for app in catalog.applications}
                            )
                        else:
                            entries = [
                                applet_desktop_id("applications"),
                                folder,
                                applet_desktop_id("clock"),
                            ]
                            if tour == "search":
                                subprocess.run(
                                    ["setxkbmap", "-layout", "us"], check=True
                                )
                                entries.insert(1, applet_desktop_id("search"))
                                settings = dict(
                                    base,
                                    dock_shortcut="",
                                    global_search_shortcut=DEFAULT_GLOBAL_SEARCH_SHORTCUT,
                                )
                        with (
                            (
                                overview_audio(clip_dir)
                                if tour == "overview"
                                else nullcontext()
                            ) as pulse_server,
                            dock(
                                desktop,
                                clip_dir,
                                settings,
                                entries,
                                tooltips=tour != "preferences",
                                pulse_server=pulse_server,
                            ),
                        ):
                            if tour == "overview":
                                prepare_live_applets(desktop, OVERVIEW_APPLETS)
                                desktop.scene_name = "Preparing music playback"
                                live_hover(
                                    desktop, "music", contains=OVERVIEW_TRACKS[0]
                                )
                                desktop.scene_name = "Preparing both weather forecasts"
                                live_hover(desktop, "weather", contains="Paris")
                                input_then_hover(
                                    desktop, "weather", contains="Belo Horizonte"
                                )
                                input_then_hover(
                                    desktop, "weather", contains="Paris", button=4
                                )
                                desktop.point(entries[0])
                            elif tour == "search":
                                desktop.focus_desktop()
                            with capture(desktop, clip_dir):
                                if tour == "overview":
                                    overview(desktop, folder)
                                elif tour == "search":
                                    search(desktop)
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


def export_gif(source: Path, destination: Path) -> None:
    decode = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-xerror",
        "-i",
        str(source),
        "-vf",
        "fps=8,scale=1280:-2",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "yuv4mpegpipe",
        "-",
    ]
    encode = [
        "gifski",
        "--fast",
        "--width",
        "1280",
        "--fps",
        "8",
        "--quality",
        "85",
        "--repeat",
        "0",
        "--output",
        str(destination),
        "-",
    ]
    with subprocess.Popen(decode, stdout=subprocess.PIPE) as decoder:
        assert decoder.stdout is not None
        with decoder.stdout:
            subprocess.run(encode, stdin=decoder.stdout, check=True)
        if status := decoder.wait():
            raise subprocess.CalledProcessError(status, decode)


def publish_tours(run: Path, output: Path, filenames: list[str]) -> None:
    lock_path = output / ".publish.lock"
    ready, previous = run / "publish", run / "previous"
    ready.mkdir()
    previous.mkdir()
    with (
        lock_path.open("a+b") as lock,
        host_services().writer_lock(lock.fileno(), path=lock_path),
    ):
        for filename in filenames:
            os.link(run / filename, ready / filename)
            with suppress(FileNotFoundError):
                os.link(output / filename, previous / filename, follow_symlinks=False)

        published = []
        interrupted = False

        def defer_interrupt(_number, _frame):
            nonlocal interrupted
            interrupted = True

        # Finish replacing or restoring every export before delivering Ctrl+C.
        previous_handler = signal.signal(signal.SIGINT, defer_interrupt)
        try:
            for filename in filenames:
                (ready / filename).replace(output / filename)
                published.append(filename)
        except OSError as error:
            failed = []
            for filename in reversed(published):
                backup, destination = previous / filename, output / filename
                try:
                    if os.path.lexists(backup):
                        backup.replace(destination)
                    else:
                        destination.unlink(missing_ok=True)
                except OSError:
                    failed.append(filename)
            if failed:
                raise OSError(
                    f"{error}. Could not restore: {', '.join(failed)}"
                ) from error
            raise
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            if interrupted:
                signal.raise_signal(signal.SIGINT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, help="Prepared demo profile directory")
    parser.add_argument(
        "--wallpaper", type=Path, help="Image for the recording desktop"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "tours")
    parser.add_argument(
        "--tour", choices=TOURS, action="append", help="Record selected tours"
    )
    parser.add_argument(
        "--gif",
        action="store_true",
        help="Also export GIFs using Gifski",
    )
    parser.add_argument(
        "--available-controls",
        action="store_true",
        help="Record the full applet catalog with the current device and service setup",
    )
    parser.add_argument("--session", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    tours = list(dict.fromkeys(args.tour or TOURS))
    if (args.session or args.background) and os.environ.get(
        "DOCKING_TOUR_PRIVATE"
    ) != "1":
        parser.error("The internal entry point requires a private tour session")
    if args.background:
        background_window()
        return 0
    if args.session:
        session(output, tours, available_controls=args.available_controls)
        return 0
    for executable in (
        "Xvfb",
        "xvfb-run",
        "xauth",
        "dbus-run-session",
        "marco",
        "ffmpeg",
        "ffprobe",
        *(("caja",) if {"overview", "applets", "search"}.intersection(tours) else ()),
        *(("gsettings", "pulseaudio", "pactl", "vlc") if "overview" in tours else ()),
        *(("setxkbmap",) if {"overview", "search"}.intersection(tours) else ()),
        *(("gifski",) if args.gif else ()),
    ):
        if shutil.which(executable) is None:
            parser.error(f"Missing executable: {executable}")
    if "applets" in tours and args.profile is None and not args.available_controls:
        parser.error("The complete applet tour requires --profile DIRECTORY")
    profile = args.profile.resolve() if args.profile else None
    if profile and not profile.is_dir():
        parser.error(f"Demo profile does not exist: {profile}")
    wallpaper = args.wallpaper.resolve() if args.wallpaper else None
    if wallpaper and not wallpaper.is_file():
        parser.error(f"Wallpaper file does not exist: {wallpaper}")
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="recording-", dir=output))
    with SessionDirectory(prefix="docking-tour-") as private:
        env = private_environment(Path(private), "mate")
        for key in (
            "NO_AT_BRIDGE",
            "LANGUAGE",
            "GIO_USE_VFS",
            "AT_SPI_BUS_ADDRESS",
            "XAUTHORITY",
            "SSH_AUTH_SOCK",
            "GNOME_KEYRING_CONTROL",
            "DOCKING_TOUR_STARTUP",
            "DOCKING_TOUR_WALLPAPER",
        ):
            env.pop(key, None)
        env["DOCKING_TOUR_PRIVATE"] = "1"
        env["DOCKING_LOG_LEVEL"] = "INFO"
        if wallpaper:
            env["DOCKING_TOUR_WALLPAPER"] = str(wallpaper)
        try:
            if profile:
                copy_profile(profile, Path(private), env)
            if "overview" in tours:
                from tools.linux.tours.scenes import OVERVIEW_LAUNCHERS

                source = Path(
                    os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
                )
                applications = Path(env["XDG_DATA_HOME"]) / "applications"
                applications.mkdir(exist_ok=True)
                for item in OVERVIEW_LAUNCHERS:
                    entry = source / "applications" / item
                    if entry.is_file() and not (applications / item).exists():
                        shutil.copyfile(entry, applications / item)
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
                    *(["--available-controls"] if args.available_controls else []),
                    *[option for tour in tours for option in ("--tour", tour)],
                ],
                cwd=ROOT,
                env=env,
                check=True,
            )
            if args.gif:
                for tour in tours:
                    print(f"Encoding {tour}.gif with Gifski", flush=True)
                    export_gif(run / f"{tour}.mp4", run / f"{tour}.gif")
            extensions = ("mp4", "gif") if args.gif else ("mp4",)
            filenames = [
                f"{tour}.{extension}" for tour in tours for extension in extensions
            ]
            publish_tours(run, output, filenames)
            for filename in filenames:
                print(output / filename)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            for clip in run.rglob("raw.mkv"):
                clip.unlink(missing_ok=True)
            print(f"Tour generation failed: {error}. Evidence: {run}", file=sys.stderr)
            return 1
    shutil.rmtree(run)
    return 0
