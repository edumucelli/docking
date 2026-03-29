"""Desktop-entry resolution, icon loading, file-target metadata, and launching.

Why this module exists

The dock cannot work directly with desktop IDs and file URIs as opaque strings.
To draw and launch something meaningful it needs to answer questions such as:

- what user-visible name should appear,
- which icon should be loaded,
- what WM_CLASS should be matched against running windows,
- what command or URI should actually be launched,
- how should a file/folder target be represented in the dock.

Those are not UI concerns and they are not model concerns. They are platform
integration concerns, which is why they live here.

Two major jobs

This module has two distinct responsibilities:

1. Resolve targets into metadata
   - desktop entry -> DesktopInfo
   - file/folder target -> FileTargetInfo

2. Execute launch/open actions
   - start applications from desktop files
   - open files/folders with the desktop environment

The rest of the dock should not need to know how XDG directories, Gio, icon
themes, or command placeholders work.

Desktop entry resolution

Application dock entries are identified by desktop IDs such as:

    firefox.desktop
    org.gnome.Nautilus.desktop

Those IDs are packaging/runtime artifacts, not rich metadata by themselves.
The dock needs to expand them into:

- display name
- icon name
- startup WM_CLASS
- exec line

That is the purpose of `DesktopInfo`.

Resolution flow:

    desktop_id
      |
      +--> Gio.DesktopAppInfo.new(...)
      |
      +--> fallback search in XDG desktop dirs if necessary
      |
      +--> extract name/icon/wm_class/exec

Why WM_CLASS fallback exists

Not every desktop file gives a clean startup WM_CLASS. When it is missing, the
dock still needs a plausible identity string for window matching. So this module
derives a fallback from the executable or desktop filename.

That fallback is not perfect, but it is far better than leaving the dock with no
runtime identity bridge at all.

Icon loading model

The dock consumes icons from several possible sources:

- named theme icon
- Gio.Icon
- absolute file path
- generic fallback icon

So icon loading works in layers:

    requested icon
      |
      +--> load from Gio.Icon if available
      |
      +--> otherwise load named icon from theme
      |
      +--> otherwise load absolute path
      |
      +--> otherwise fallback icon

Icons are cached by `(icon identity, size)` because the dock requests the same
art assets repeatedly during rendering and model refresh.

File and folder targets

The dock also supports file/folder entries. Those are different from apps:

- they are opened, not launched as desktop apps,
- their icon/name come from filesystem metadata,
- directories and regular files have different default icon semantics.

So this module normalizes file targets into `FileTargetInfo` with:

- resolved display name
- icon or theme fallback
- whether the target is a directory

That keeps file/folder handling aligned with the rest of the dock item model.

Why this module is deliberately defensive

Desktop metadata is not always clean:

- invalid desktop files exist,
- icons may be missing,
- files may disappear,
- Gio may fail to resolve specific targets.

The dock should degrade gracefully:

    no desktop info -> item may not resolve
    no icon         -> generic fallback icon
    bad file target -> no dock entry metadata

This module treats those failures as data-quality issues, not fatal UI errors.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.log import get_logger, with_context

DESKTOP_SUFFIX = ".desktop"
FALLBACK_ICON = "application-x-executable"
DEFAULT_XDG_DATA_DIRS = "/usr/local/share:/usr/share"
GNOME_APP_PREFIX = "org.gnome."
log = with_context(get_logger(name="launcher"))


class DesktopInfo(NamedTuple):
    """Resolved information from a .desktop file."""

    desktop_id: str
    name: str
    icon_name: str
    wm_class: str
    exec_line: str


class FileTargetInfo(NamedTuple):
    """Resolved file/folder metadata for dock entries."""

    target: str
    name: str
    icon_name: str
    icon: GdkPixbuf.Pixbuf | None
    is_dir: bool


class Launcher:
    """Resolves .desktop files via XDG_DATA_DIRS and loads icons."""

    def __init__(self) -> None:
        self._desktop_dirs = self._get_desktop_dirs()
        self._icon_cache: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}

    def resolve(self, desktop_id: str) -> DesktopInfo | None:
        """Resolve a desktop ID (e.g. 'firefox.desktop') to full info."""
        try:
            app_info = Gio.DesktopAppInfo.new(desktop_id)
        except (TypeError, GLib.Error) as exc:
            log.bind(desktop_id=desktop_id, action="resolve").warning(
                f"Failed to resolve desktop app info: {exc}"
            )
            app_info = None
        if app_info is None:
            # Try searching by filename in XDG dirs
            for d in self._desktop_dirs:
                path = d / desktop_id
                if path.exists():
                    try:
                        app_info = Gio.DesktopAppInfo.new_from_filename(str(path))
                    except (TypeError, GLib.Error) as exc:
                        log.bind(
                            desktop_id=desktop_id,
                            action="resolve_from_filename",
                            path=str(path),
                        ).warning(f"Failed to resolve desktop file by path: {exc}")
                        continue
                    break
        if app_info is None:
            return None

        wm_class = app_info.get_startup_wm_class() or ""
        if not wm_class:
            # Fallback: derive from executable name
            commandline = app_info.get_commandline() or ""
            exe = commandline.split()[0] if commandline else ""
            wm_class = (
                Path(exe).name if exe else desktop_id.removesuffix(DESKTOP_SUFFIX)
            )

        icon = app_info.get_icon()
        icon_name = icon.to_string() if icon else FALLBACK_ICON

        return DesktopInfo(
            desktop_id=desktop_id,
            name=app_info.get_display_name() or desktop_id,
            icon_name=icon_name,
            wm_class=wm_class,
            exec_line=app_info.get_commandline() or "",
        )

    def load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load an icon by name at the given size, with caching."""
        key = (icon_name, size)
        if key in self._icon_cache:
            return self._icon_cache[key]

        pixbuf = self._try_load_icon(icon_name=icon_name, size=size)
        self._icon_cache[key] = pixbuf
        return pixbuf

    def load_gicon(self, gicon: Gio.Icon | None, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load a pixbuf directly from a Gio.Icon when available."""
        if gicon is None:
            return None
        cache_key = (f"gicon:{gicon.to_string()}", size)
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]

        pixbuf = self._try_load_gicon(gicon=gicon, size=size)
        self._icon_cache[cache_key] = pixbuf
        return pixbuf

    def resolve_file(self, target: str, size: int) -> FileTargetInfo | None:
        """Resolve a file:// URI or local path into display metadata."""
        uri = normalize_file_target(target)
        if uri is None:
            return None
        try:
            gfile = Gio.File.new_for_uri(uri)
            info = gfile.query_info(
                "standard::display-name,standard::icon,standard::type,standard::content-type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except GLib.Error as exc:
            log.bind(target=target, action="resolve_file").warning(
                f"Failed to query file info: {exc}"
            )
            return None

        icon = info.get_icon()
        icon_name = (
            "folder"
            if info.get_file_type() == Gio.FileType.DIRECTORY
            else "text-x-generic"
        )
        return FileTargetInfo(
            target=uri,
            name=info.get_display_name()
            or Path(unquote(urlparse(uri).path)).name
            or uri,
            icon_name=icon_name,
            icon=self.resolve_file_icon(
                target=uri,
                gicon=icon,
                content_type=info.get_content_type() or "",
                size=size,
                is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
            ),
            is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
        )

    def resolve_file_icon(
        self,
        *,
        target: str,
        gicon: Gio.Icon | None,
        content_type: str,
        size: int,
        is_dir: bool,
    ) -> GdkPixbuf.Pixbuf | None:
        """Resolve a file target icon, preferring image thumbnails when possible."""
        if not is_dir and content_type.lower().startswith("image/"):
            uri = normalize_file_target(target)
            if uri is not None:
                path = Path(unquote(urlparse(uri).path))
                if path.exists():
                    try:
                        return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                            str(path),
                            size,
                            size,
                            True,
                        )
                    except GLib.Error as exc:
                        log.bind(target=target, action="resolve_file_icon").debug(
                            "Failed to load image thumbnail %s: %s",
                            path,
                            exc,
                        )

        icon_name = "folder" if is_dir else "text-x-generic"
        return self.load_gicon(gicon=gicon, size=size) or self.load_icon(
            icon_name=icon_name,
            size=size,
        )

    def default_directory_app_name(self) -> str | None:
        """Return the display name of the default app that opens folders."""
        try:
            app_info = Gio.AppInfo.get_default_for_type("inode/directory", False)
        except GLib.Error as exc:
            log.bind(action="default_directory_app_name").warning(
                "Failed to resolve default directory app: %s",
                exc,
            )
            return None
        if app_info is None:
            return None
        return app_info.get_display_name() or None

    def _try_load_gicon(self, gicon: Gio.Icon, size: int) -> GdkPixbuf.Pixbuf | None:
        theme = Gtk.IconTheme.get_default()
        if theme is None:
            theme = Gtk.IconTheme()
            theme.set_custom_theme("hicolor")

        lookup_by_gicon = getattr(theme, "lookup_by_gicon", None)
        if callable(lookup_by_gicon):
            try:
                info = lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
            except TypeError:
                info = None
            if info is not None:
                try:
                    return info.load_icon()
                except GLib.Error as exc:
                    log.bind(action="load_gicon").debug(
                        "Theme gicon not found (%s): %s", gicon.to_string(), exc
                    )

        return self.load_icon(icon_name=gicon.to_string(), size=size)

    def _try_load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Attempt to load icon from theme or file path."""
        theme = Gtk.IconTheme.get_default()
        if theme is None:
            theme = Gtk.IconTheme()
            theme.set_custom_theme("hicolor")

        # If it's an absolute path
        icon_path = Path(icon_name)
        if icon_path.is_absolute() and icon_path.exists():
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(icon_path), size, size, True
                )
            except GLib.Error as exc:
                log.bind(action="load_icon").debug(
                    "Failed to load icon file %s: %s",
                    icon_path,
                    exc,
                )

        # Try icon theme lookup
        try:
            return theme.load_icon(icon_name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error as exc:
            log.bind(action="load_icon").debug(
                "Theme icon not found (%s): %s",
                icon_name,
                exc,
            )

        # Fallback
        try:
            return theme.load_icon(FALLBACK_ICON, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error as exc:
            log.bind(action="load_icon").warning(
                f"Failed to load fallback icon {FALLBACK_ICON}: {exc}"
            )
            return None

    @staticmethod
    def _get_desktop_dirs() -> list[Path]:
        """Get application .desktop file directories from XDG_DATA_DIRS."""
        xdg = os.environ.get("XDG_DATA_DIRS", DEFAULT_XDG_DATA_DIRS)
        dirs = []
        for d in xdg.split(":"):
            p = Path(d) / "applications"
            if p.is_dir():
                dirs.append(p)
        # Also check user-local
        local = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        user_apps = local / "applications"
        if user_apps.is_dir():
            dirs.insert(0, user_apps)
        return dirs


class DesktopAction(NamedTuple):
    """A .desktop Actions entry (e.g. "New Window")."""

    action_id: str
    display_name: str


def get_actions(desktop_id: str) -> list[DesktopAction]:
    """Return .desktop Actions entries (e.g. "New Window", "New Incognito Window")."""
    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error) as exc:
        log.bind(desktop_id=desktop_id, action="get_actions").warning(
            f"Failed to read desktop actions: {exc}"
        )
        return []
    if not app_info:
        return []
    result = []
    for action_id in app_info.list_actions():
        name = app_info.get_action_name(action_id)
        if name:
            result.append(DesktopAction(action_id, name))
    return result


def launch_action(desktop_id: str, action_id: str) -> None:
    """Launch a named desktop action (from the .desktop [Desktop Action ...] group)."""
    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error) as exc:
        log.bind(desktop_id=desktop_id, action="launch_action").warning(
            f"Failed to resolve desktop app info for action {action_id}: {exc}"
        )
        return
    if app_info:
        try:
            app_info.launch_action(action_id, None)
        except GLib.Error as exc:
            log.bind(desktop_id=desktop_id, action="launch_action").warning(
                "Failed to launch action %s for %s: %s",
                action_id,
                desktop_id,
                exc,
            )


def launch(desktop_id: str) -> None:
    """Launch an application by its desktop ID.

    Uses subprocess with start_new_session=True so the child gets its own
    session and process group. This prevents the child from receiving
    SIGHUP/SIGINT when the dock exits or the terminal sends Ctrl+C.
    """
    app_info = Gio.DesktopAppInfo.new(desktop_id)
    if not app_info:
        return
    cmdline = app_info.get_commandline()
    if not cmdline:
        return
    cmd = re.sub(r"%[uUfFdDnNickvm]", "", cmdline).strip()
    if not cmd:
        return
    try:
        argv = [arg for arg in shlex.split(cmd, posix=True) if arg]
    except ValueError as e:
        log.bind(desktop_id=desktop_id, action="parse_exec").warning(
            f"Failed to parse launch command for {desktop_id}: {e}"
        )
        return
    if not argv:
        return
    try:
        subprocess.Popen(
            argv,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.bind(desktop_id=desktop_id, action="launch").warning(
            f"Failed to launch {desktop_id}: {e}"
        )


def normalize_file_target(target: str) -> str | None:
    """Normalize a local path or file:// URI into a file:// URI."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    elif parsed.scheme == "":
        path = Path(target).expanduser()
    else:
        return None
    try:
        return path.resolve().as_uri()
    except ValueError:
        return None


def open_target(target: str) -> bool:
    """Open a local file or directory with the default application."""
    uri = normalize_file_target(target)
    if uri is None:
        return False
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True
    except GLib.Error as exc:
        log.bind(target=target, action="open_target").warning(
            f"Failed to open target {target}: {exc}"
        )
        return False
