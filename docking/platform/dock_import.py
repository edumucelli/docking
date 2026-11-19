"""Read Plank and Cairo-Dock settings without changing their source files."""

from __future__ import annotations

import math
import stat
import subprocess
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

from docking.applets import get_applet_catalog
from docking.applets.identity import applet_desktop_id, parse_applet_id
from docking.core.config import Config, PinnedEntry
from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND, ItemKind
from docking.core.paths import local_path_from_uri_or_path
from docking.i18n import _
from docking.platform.host.base import HostFamily
from docking.platform.host.selection import host_services

if TYPE_CHECKING:
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.applications.types import ApplicationInfo

_PLANK_SCHEMA = "net.launchpad.plank"
_PLANK_PATH = "/net/launchpad/plank/"
_CAIRO_ROOT = "_MainDock_"
_MAX_CONFIG_BYTES = 1024 * 1024
_PLANK_DOCKLETS = {
    name: name
    for name in (
        "applications",
        "battery",
        "clippy",
        "clock",
        "desktop",
        "separator",
        "notifications",
        "trash",
        "workspaces",
    )
} | {"cpumonitor": "systemmonitor"}
_CAIRO_APPLETS = {
    "gmenu": "applications",
    "clock": "clock",
    "clipper": "clippy",
    "showdesktop": "desktop",
    "switcher": "workspaces",
    "dustbin": "trash",
    "weather": "weather",
    "musicplayer": "music",
    "alsamixer": "volume",
    "powermanager": "battery",
    "logout": "session",
    "status-notifier": "systemtray",
    "system-monitor": "systemmonitor",
    "calendar": "calendar",
    "brightness": "brightness",
    "moon": "moon",
    "cpuwatcher": "systemmonitor",
    "notifications_history": "notifications",
}
_PLANK_PREFERENCES = {
    "position": ("position", "s"),
    "icon-size": ("icon_size", "i"),
    "zoom-enabled": ("zoom_enabled", "b"),
    "hide-delay": ("hide_delay_ms", "i"),
    "unhide-delay": ("unhide_delay_ms", "i"),
    "pressure-reveal": ("pressure_reveal_enabled", "b"),
    "lock-items": ("lock_icons", "b"),
    "pinned-only": ("pinned_only", "b"),
    "current-workspace-only": ("current_workspace_only", "b"),
    "tooltips-enabled": ("tooltips_enabled", "b"),
    "active-display": ("active_display", "b"),
    "previews-enabled": ("window_preview_thumbnails_enabled", "b"),
    "preview-size": ("window_preview_thumbnail_width", "i"),
    "anchor-docklets": ("anchor_applets", "b"),
    "anchor-files": ("anchor_files", "b"),
}
_CAIRO_POSITIONS = {0: "bottom", 1: "top", 2: "right", 3: "left"}
_CAIRO_HIDE_MODES = {
    0: "always-on-top",
    1: "none",
    2: "always-behind",
    3: "dodge-active",
    4: "window-dodge",
    5: "autohide",
}

Scalar = bool | int | float | str
ReadPlank = Callable[[str, str, str], "GLib.Variant | None"]


@dataclass(frozen=True, slots=True)
class SourcePin:
    kind: ItemKind
    label: str
    targets: tuple[str, ...]


@dataclass(slots=True)
class DockImportSource:
    name: str
    profile: str = ""
    pins: list[SourcePin] = field(default_factory=list)
    settings: dict[str, Scalar] = field(default_factory=dict)
    monitor: str | None = None
    edge_gap: int | None = None
    empty_layout: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(
            self.pins
            or self.settings
            or self.monitor is not None
            or self.edge_gap is not None
            or self.empty_layout
        )


@dataclass(frozen=True, slots=True)
class DockImport:
    config: Config
    added: list[PinnedEntry]
    labels: list[str]
    changes: dict[str, Scalar | None]
    notes: list[str]
    empty_layout: bool


def dock_import_supported() -> bool:
    return host_services().family in (HostFamily.LINUX, HostFamily.BSD)


def read_dock_import_sources() -> list[DockImportSource]:
    root = host_services().config_home()
    plank_root = root / "plank"
    cairo_root = root / "cairo-dock" / "current_theme"
    sources = (_read_plank(plank_root), _read_cairo(cairo_root))
    return [source for source in sources if source.available or source.notes]


def _key_file(path: Path, root: Path) -> GLib.KeyFile | None:
    try:
        path = path.resolve(strict=True)
        if not path.is_relative_to(root):
            return None
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CONFIG_BYTES:
            return None
        key_file = GLib.KeyFile()
        key_file.load_from_file(str(path), GLib.KeyFileFlags.NONE)
    except (OSError, RuntimeError, GLib.Error):
        return None
    return key_file


def _files(folder: Path, root: Path) -> list[Path] | None:
    try:
        if folder.resolve(strict=True).is_relative_to(root):
            return sorted(
                path for path in folder.iterdir() if not path.name.startswith(".")
            )
    except (OSError, RuntimeError):
        pass
    return None


def _text(key_file: GLib.KeyFile, group: str, key: str) -> str:
    try:
        return key_file.get_string(group, key).strip()
    except GLib.Error:
        return ""


def _list(key_file: GLib.KeyFile, group: str, key: str) -> tuple[str, ...] | None:
    try:
        return tuple(key_file.get_string_list(group, key))
    except GLib.Error:
        return None


def _number(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _read_plank_settings() -> ReadPlank | None:
    schemas = Gio.SettingsSchemaSource.get_default()
    global_schema = schemas.lookup(_PLANK_SCHEMA, True) if schemas is not None else None
    dock_schema = (
        schemas.lookup(f"{_PLANK_SCHEMA}.dock.settings", True)
        if schemas is not None
        else None
    )
    if global_schema is not None and dock_schema is not None:
        global_settings = Gio.Settings.new_full(global_schema, None, None)
        docks: dict[str, Gio.Settings] = {}

        def read(group: str, key: str, signature: str) -> GLib.Variant | None:
            if group == "/":
                settings, schema = global_settings, global_schema
            else:
                schema = dock_schema
                if group not in docks:
                    docks[group] = Gio.Settings.new_full(
                        schema, None, f"{_PLANK_PATH}{group}/"
                    )
                settings = docks[group]
            if schema.has_key(key):
                value = settings.get_user_value(key)
                if value is not None and value.get_type_string() == signature:
                    return value
            return None

        return read

    try:
        result = subprocess.run(
            ["dconf", "dump", _PLANK_PATH],
            capture_output=True,
            check=True,
            timeout=1,
            text=True,
        )
        data = result.stdout
        if len(data) > _MAX_CONFIG_BYTES:
            return None
        key_file = GLib.KeyFile()
        key_file.load_from_data(data, len(data.encode()), GLib.KeyFileFlags.NONE)
    except (OSError, subprocess.SubprocessError, UnicodeError, GLib.Error):
        return None

    def read_dump(group: str, key: str, signature: str) -> GLib.Variant | None:
        try:
            value = key_file.get_value(group, key)
            return GLib.Variant.parse(
                GLib.VariantType.new(signature), value, None, None
            )
        except GLib.Error:
            return None

    return read_dump


def _profile_name_valid(name: str) -> bool:
    return bool(
        name and not name.startswith(".") and "/" not in name and "\0" not in name
    )


def _read_plank(root: Path) -> DockImportSource:
    source = DockImportSource(_("Plank / Plank Reloaded"))
    try:
        root = root.resolve()
    except (OSError, RuntimeError):
        return source
    read = _read_plank_settings()

    def value(group: str, key: str, signature: str) -> object:
        variant = read(group, key, signature) if read is not None else None
        return None if variant is None else variant.unpack()

    def has_saved_settings(name: str) -> bool:
        group = f"docks/{name}"
        return any(
            value(group, key, signature) is not None
            for key, (_, signature) in _PLANK_PREFERENCES.items()
        ) or any(
            value(group, key, signature) is not None
            for key, signature in (
                ("dock-items", "as"),
                ("alignment", "s"),
                ("offset", "i"),
                ("hide-mode", "s"),
                ("monitor", "s"),
                ("gap-size", "i"),
                ("zoom-percent", "i"),
            )
        )

    enabled = value("/", "enabled-docks", "as")
    names = (
        [
            name
            for name in enabled[:8]
            if isinstance(name, str) and _profile_name_valid(name)
        ]
        if isinstance(enabled, list)
        else []
    )
    if names:
        name = next(
            (
                name
                for name in names
                if value(f"docks/{name}", "pinned-only", "b") is not True
            ),
            names[0],
        )
    else:
        names = [
            path.name
            for path in _files(root, root) or ()
            if _profile_name_valid(path.name)
            and (
                has_saved_settings(path.name)
                or any(
                    item.suffix == ".dockitem"
                    for item in _files(path / "launchers", root) or ()
                )
            )
        ]
        if "dock1" in names or has_saved_settings("dock1"):
            name = "dock1"
        elif len(names) == 1:
            name = names[0]
        else:
            if len(names) > 1:
                source.notes.append(_("Plank’s primary dock could not be determined."))
            return source

    source.profile = name
    group = f"docks/{name}"
    for key, (field_name, signature) in _PLANK_PREFERENCES.items():
        stored = value(group, key, signature)
        if isinstance(stored, (str, int, bool)) and (
            key != "position" or stored in _CAIRO_POSITIONS.values()
        ):
            source.settings[field_name] = stored
    if isinstance(zoom := value(group, "zoom-percent", "i"), int):
        source.settings["zoom_percent"] = zoom / 100
    if isinstance(gap := value(group, "gap-size", "i"), int):
        source.edge_gap = gap
    if isinstance(monitor := value(group, "monitor", "s"), str):
        source.monitor = monitor
    hide_mode = value(group, "hide-mode", "s")
    if isinstance(hide_mode, str) and hide_mode in (
        "none",
        "intelligent",
        "auto",
        "window-dodge",
        "dodge-active",
        "dodge-maximized",
    ):
        source.settings["hide_mode"] = "autohide" if hide_mode == "auto" else hide_mode
    alignment = value(group, "alignment", "s")
    offset = value(group, "offset", "i")
    if alignment in ("start", "end"):
        source.settings["dock_alignment"] = 0.0 if alignment == "start" else 1.0
    elif alignment == "center" or (alignment is None and isinstance(offset, int)):
        source.settings["dock_alignment"] = (
            100 + (offset if isinstance(offset, int) else 0)
        ) / 200
    elif alignment == "fill":
        source.notes.append(_("Skipped Plank’s full-edge layout."))

    order = value(group, "dock-items", "as")
    files = _files(root / name / "launchers", root)
    launchers = {path.name: path for path in files or () if path.suffix == ".dockitem"}
    source.empty_layout = order == [] and files is not None and not launchers
    if read is None and launchers:
        source.notes.append(
            _("Plank settings were unavailable. Launcher files are listed by filename.")
        )
    saved_order = (
        [name for name in order if isinstance(name, str)]
        if isinstance(order, list)
        else []
    )
    ordered = dict.fromkeys([*saved_order, *launchers])
    for filename in ordered:
        path = launchers.get(filename)
        shell = _key_file(path, root) if path is not None else None
        target = ""
        if shell is not None:
            target = _text(shell, "PlankDockItemPreferences", "Launcher") or _text(
                shell,
                "PlankItemsDockItemPreferences",
                "Launcher",
            )
        if target.startswith("docklet://"):
            applet = _PLANK_DOCKLETS.get(target.removeprefix("docklet://"))
            if applet is not None:
                source.pins.append(
                    SourcePin(APPLET_KIND, Path(filename).stem, (applet,))
                )
                continue
        elif target:
            pin = _launcher_or_file(target, Path(filename).stem)
            if pin is not None:
                source.pins.append(pin)
                continue
        source.notes.append(_("Skipped {item}.").format(item=Path(filename).name))
    return source


def _launcher_or_file(target: str, label: str) -> SourcePin | None:
    if target.endswith(".desktop"):
        return SourcePin(APP_KIND, label, (target,))
    return _local_pin(target, label)


def _local_pin(target: str, label: str) -> SourcePin | None:
    path = local_path_from_uri_or_path(target)
    if path is None or not path.is_absolute():
        return None
    try:
        path = path.resolve(strict=True)
        mode = path.stat().st_mode
    except (OSError, RuntimeError):
        return None
    if stat.S_ISDIR(mode):
        return SourcePin(FOLDER_KIND, label, (path.as_uri(),))
    if stat.S_ISREG(mode):
        return SourcePin(FILE_KIND, label, (path.as_uri(),))
    return None


@dataclass(frozen=True, slots=True)
class _CairoIcon:
    container: str
    order: float
    filename: str
    pin: SourcePin | None = None
    child: str = ""


def _read_cairo(root: Path) -> DockImportSource:
    source = DockImportSource(_("Cairo-Dock"), _("Main dock"))
    try:
        root = root.resolve()
    except (OSError, RuntimeError):
        return source
    config = _key_file(root / "cairo-dock.conf", root)
    launchers = _files(root / "launchers", root)
    if config is None and launchers is None:
        return source

    icons: list[_CairoIcon] = []
    if config is not None:
        _cairo_preferences(source, config)
        icons.extend(_cairo_applets(source, config, root))
    desktop_files = [path for path in launchers or () if path.suffix == ".desktop"]
    for path in desktop_files:
        icon = _cairo_launcher(source, path, root)
        if icon is not None:
            icons.append(icon)

    groups: dict[str, list[_CairoIcon]] = defaultdict(list)
    for icon in sorted(icons, key=lambda icon: (icon.order, icon.filename)):
        groups[icon.container].append(icon)

    seen = {_CAIRO_ROOT}
    stack = [iter(groups[_CAIRO_ROOT])]
    while stack:
        icon = next(stack[-1], None)
        if icon is None:
            stack.pop()
        elif icon.pin is not None:
            source.pins.append(icon.pin)
        elif icon.child in seen:
            source.notes.append(
                _("Skipped repeated sub-dock {item}.").format(item=icon.child)
            )
        else:
            seen.add(icon.child)
            stack.append(iter(groups.get(icon.child, ())))
    for name in sorted(groups.keys() - seen):
        source.notes.append(_("Skipped separate dock {item}.").format(item=name))

    source.empty_layout = (
        config is not None
        and launchers is not None
        and not desktop_files
        and not icons
        and not source.notes
    )
    return source


def _cairo_launcher(
    source: DockImportSource, path: Path, root: Path
) -> _CairoIcon | None:
    key_file = _key_file(path, root)
    group = "Desktop Entry"
    if key_file is None or not key_file.has_group(group):
        source.notes.append(_("Skipped {item}.").format(item=path.name))
        return None

    def read(key: str) -> str:
        return _text(key_file, group, key)

    order = _number(read("Order")) or 0.0
    container = read("Container") or _CAIRO_ROOT
    name = read("Name") or path.stem
    icon_type = read("Icon Type")
    entry_type = read("Type")
    if icon_type == "1" or (
        not icon_type
        and (
            entry_type in ("Container", "1")
            or read("Is container") == "true"
            or (_number(read("Nb subicons")) or 0) != 0
        )
    ):
        if child := read("Name"):
            return _CairoIcon(container, order, path.name, child=child)
    elif icon_type == "2" or (not icon_type and entry_type == "Separator"):
        return _CairoIcon(
            container, order, path.name, SourcePin(APPLET_KIND, name, ("separator",))
        )
    elif icon_type in ("", "0"):
        if read("Exec") or read("Path") or read("Terminal") == "true":
            source.notes.append(_("Skipped custom launcher {item}.").format(item=name))
            return None
        if entry_type == "Link":
            pin = _local_pin(read("URL"), name)
            if pin is not None:
                return _CairoIcon(container, order, path.name, pin)
        elif origins := _list(key_file, group, "Origin"):
            return _CairoIcon(
                container, order, path.name, SourcePin(APP_KIND, name, origins)
            )
        elif not icon_type and not entry_type and (read("Order") or read("Container")):
            return _CairoIcon(
                container,
                order,
                path.name,
                SourcePin(APPLET_KIND, name, ("separator",)),
            )
    source.notes.append(_("Skipped {item}.").format(item=name))
    return None


def _cairo_applets(
    source: DockImportSource, config: GLib.KeyFile, root: Path
) -> list[_CairoIcon]:
    enabled = _list(config, "System", "modules")
    if enabled is None:
        enabled = _list(config, "Applets", "modules_0") or ()
    folders = {
        path.name.casefold(): path for path in _files(root / "plug-ins", root) or ()
    }
    icons: list[_CairoIcon] = []
    for module in dict.fromkeys(name.strip() for name in enabled if name.strip()):
        applet = _CAIRO_APPLETS.get(module.casefold())
        is_folder = module.casefold() == "folders"
        directory = folders.get(module.casefold())
        if applet is None and not is_folder:
            source.notes.append(_("Skipped plug-in {item}.").format(item=module))
            continue
        files = (
            [
                path
                for path in (_files(directory, root) or ())
                if (path.name.endswith(".conf") or ".conf-" in path.name)
            ]
            if directory is not None
            else []
        )
        if not files:
            if applet is not None:
                icons.append(
                    _CairoIcon(
                        _CAIRO_ROOT,
                        math.inf,
                        module,
                        SourcePin(APPLET_KIND, module, (applet,)),
                    )
                )
            else:
                source.notes.append(_("Skipped plug-in {item}.").format(item=module))
            continue
        for path in files:
            key_file = _key_file(path, root)
            if key_file is None or not key_file.has_group("Icon"):
                source.notes.append(_("Skipped plug-in {item}.").format(item=module))
                continue
            if _text(key_file, "Desklet", "initially detached") == "true":
                source.notes.append(
                    _("Skipped detached desklet {item}.").format(item=module)
                )
                continue
            label = _text(key_file, "Icon", "name") or module
            container = _text(key_file, "Icon", "dock name") or _CAIRO_ROOT
            order = _number(_text(key_file, "Icon", "order"))
            pin = (
                SourcePin(APPLET_KIND, label, (applet,))
                if applet is not None
                else _local_pin(_text(key_file, "Configuration", "dir path"), label)
            )
            if pin is None or (is_folder and pin.kind != FOLDER_KIND):
                source.notes.append(_("Skipped plug-in {item}.").format(item=module))
                continue
            icons.append(
                _CairoIcon(
                    container,
                    math.inf if order is None else order,
                    f"{module}/{path.name}",
                    pin,
                )
            )
    return icons


def _cairo_preferences(source: DockImportSource, config: GLib.KeyFile) -> None:
    position = _number(_text(config, "Position", "screen border"))
    if (
        position is not None
        and position == int(position)
        and int(position) in _CAIRO_POSITIONS
    ):
        source.settings["position"] = _CAIRO_POSITIONS[int(position)]
    if (alignment := _number(_text(config, "Position", "alignment"))) is not None:
        source.settings["dock_alignment"] = alignment
    if (gap := _number(_text(config, "Position", "y gap"))) is not None:
        source.edge_gap = int(gap)
    if screen := _text(config, "Position", "screen_name"):
        source.monitor = screen
    elif _text(config, "Position", "num_screen") not in ("", "0"):
        source.notes.append(_("Skipped Cairo-Dock’s numeric display selection."))

    size = _list(config, "Icons", "launcher size") or ()
    if size and (height := _number(size[-1])) is not None and height > 0:
        source.settings["icon_size"] = int(height)
    zoom = _number(_text(config, "Icons", "zoom max"))
    if (
        zoom is None
        and (amplitude := _number(_text(config, "Icons", "amplitude"))) is not None
    ):
        zoom = 1 + amplitude
    if zoom is not None and zoom >= 1:
        source.settings["zoom_enabled"] = zoom > 1
        source.settings["zoom_percent"] = zoom

    for group, key, attribute in (
        ("Accessibility", "lock icons", "lock_icons"),
        ("TaskBar", "current desktop only", "current_workspace_only"),
        ("TaskBar", "show applications", "pinned_only"),
    ):
        value = _text(config, group, key)
        if value in ("true", "false"):
            source.settings[attribute] = value == (
                "false" if attribute == "pinned_only" else "true"
            )

    mode = _number(_text(config, "Accessibility", "visibility"))
    if mode is not None and mode == int(mode):
        if (hide := _CAIRO_HIDE_MODES.get(int(mode))) is not None:
            source.settings["hide_mode"] = hide
        if mode == 2:
            source.notes.append(
                _("Always Behind does not reveal the dock on edge hover.")
            )
        elif mode == 6:
            source.notes.append(
                _("Set up the dock reveal shortcut in Docking Preferences.")
            )


def _resolve_application(
    registry: ApplicationRegistry, target: str
) -> ApplicationInfo | None:
    target = target.strip().removeprefix("application://")
    path = local_path_from_uri_or_path(target)
    if path is not None and path.is_absolute() and path.suffix == ".desktop":
        return registry.resolve_by_desktop_file(path)
    if "/" not in target and target.endswith(".desktop"):
        return registry.get(target)
    return None


def prepare_dock_import(
    source: DockImportSource,
    base: Config,
    registry: ApplicationRegistry,
    *,
    connectors: set[str],
    theme_gap: int,
    first_run: bool,
) -> DockImport:
    """Build and normalize a reviewable change using the current config."""
    notes = list(source.notes)
    additions: list[PinnedEntry] = []
    labels: list[str] = []
    existing = {entry.id for entry in base.pinned}
    separators = sum(parse_applet_id(entry.id) == "separator" for entry in base.pinned)
    seen_separators = 0
    used_separator_ids = 0
    catalog = get_applet_catalog()
    for pin in source.pins:
        target = pin.targets[0]
        label = pin.label
        if pin.kind == APP_KIND:
            application = next(
                (
                    app
                    for target in pin.targets
                    if (app := _resolve_application(registry, target)) is not None
                ),
                None,
            )
            if application is None:
                notes.append(
                    _("Application unavailable for {item}.").format(item=label)
                )
                continue
            target, label = application.desktop_id, application.name
        elif pin.kind == APPLET_KIND:
            meta = catalog.get(target)
            if meta is None or not meta.available_on_host:
                notes.append(_("Skipped {item}.").format(item=label))
                continue
            label = _(meta.name)
            if target == "separator":
                seen_separators += 1
                if seen_separators <= separators:
                    continue
                while applet_desktop_id(target, used_separator_ids) in existing:
                    used_separator_ids += 1
                target = applet_desktop_id(target, used_separator_ids)
            elif any(parse_applet_id(item) == target for item in existing):
                continue
            else:
                target = applet_desktop_id(target)
        if target in existing:
            continue
        existing.add(target)
        additions.append(PinnedEntry(pin.kind, target))
        labels.append(label)

    settings = dict(source.settings)
    if not first_run:
        settings.pop("anchor_applets", None)
        settings.pop("anchor_files", None)
    if source.monitor is not None:
        if source.monitor == "" or source.monitor in connectors:
            settings["monitor_index"] = -1
            settings["monitor_connector"] = source.monitor
        else:
            notes.append(_("The source display is unavailable."))
    if source.edge_gap is not None:
        settings["additional_distance_from_edge"] = max(0, source.edge_gap - theme_gap)

    added_apps = {entry.id for entry in additions if entry.kind == APP_KIND}
    candidate = replace(
        base,
        pinned=base.pinned + additions,
        recent_apps=[
            entry for entry in base.recent_apps if entry["desktop_id"] not in added_apps
        ],
        **settings,
    )
    before, after = base.to_dict(), candidate.to_dict()
    changes = {key: after[key] for key in settings if before[key] != after[key]}
    return DockImport(candidate, additions, labels, changes, notes, source.empty_layout)
