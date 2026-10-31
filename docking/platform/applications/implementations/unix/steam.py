from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from shlex import join

from docking.platform.environment import is_flatpak

from ...steam import (
    STEAM_FLATPAK_ID,
    SteamDiscoveryResult,
    SteamGame,
    _read_vdf,
    valid_steam_app_id,
)
from ...types import ApplicationInfo, ApplicationLocation, ApplicationOrigin


def game_application(self: SteamGame) -> ApplicationInfo:
    """Build hidden-from-menu metadata that remains launchable and pinnable."""
    argv = ("flatpak", "run", STEAM_FLATPAK_ID) if self.flatpak else ("steam",)
    return ApplicationInfo(
        desktop_id=f"steam-{self.app_id}.desktop",
        name=self.name,
        declared_icon=self.icon or f"steam_icon_{self.app_id}",
        wm_class=f"steam_app_{self.app_id}",
        exec_line=join((*argv, f"steam://rungameid/{self.app_id}")),
        origin=ApplicationOrigin.STEAM,
        location=(
            ApplicationLocation.HOST if is_flatpak() else ApplicationLocation.SANDBOX
        ),
        desktop_file=None,
        executable_path=None,
        aliases=(f"steam-{self.app_id}", f"steam_app_{self.app_id}"),
        visible=False,
        has_gio_source=False,
        steam_app_id=self.app_id,
        steam_manifest=self.manifest,
    )


def default_steam_roots() -> tuple[tuple[Path, bool], ...]:
    """Return native roots first, followed by known Flatpak Steam layouts."""
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    native = [
        data / "Steam",
        data / "steam",
        home / ".steam/root",
        home / ".steam/steam",
        home / ".steam/debian-installation",
    ]
    sandbox = home / ".var/app" / STEAM_FLATPAK_ID
    return tuple((path, False) for path in native) + tuple(
        (sandbox / suffix, True)
        for suffix in (
            ".local/share/Steam",
            ".local/share/steam",
            "data/Steam",
            "data/steam",
        )
    )


class SteamDiscovery:
    """Scan Steam metadata during registry refresh, never during window scans."""

    def __init__(self, roots: Iterable[tuple[Path, bool]] | None = None) -> None:
        self._roots = tuple(roots) if roots is not None else None

    def __call__(self) -> SteamDiscoveryResult:
        games: list[SteamGame] = []
        watches: set[Path] = set()
        issues: list[str] = []
        roots_seen: set[Path] = set()
        manifests_seen: set[Path] = set()

        def watch(path: Path, boundary: Path) -> None:
            while not path.is_dir():
                if path == boundary or not path.is_relative_to(boundary):
                    return
                path = path.parent
            watches.add(path.resolve())

        roots = self._roots if self._roots is not None else default_steam_roots()
        for candidate, flatpak in sorted(roots, key=lambda entry: entry[1]):
            try:
                # Bootstrap only within Steam-specific parents, never HOME or
                # shared XDG data directories. Gio also reports new launchers.
                boundary = next(
                    (
                        parent
                        for parent in candidate.parents
                        if parent.name == ".steam"
                        or (
                            flatpak
                            and parent.name == "app"
                            and parent.parent.name == ".var"
                        )
                    ),
                    candidate,
                )
                watch(candidate, boundary)
                root = candidate.resolve()
                if root in roots_seen or not (root / "steamapps").is_dir():
                    continue
                roots_seen.add(root)
                libraries = [root]
                for config in (
                    root / "steamapps/libraryfolders.vdf",
                    root / "config/libraryfolders.vdf",
                ):
                    watch(config.parent, root)
                    if not config.is_file():
                        continue
                    try:
                        folders = _read_vdf(config).get("libraryfolders", {})
                        if not isinstance(folders, dict):
                            raise ValueError("Invalid library folders")
                        for key, value in folders.items():
                            if not key.isdecimal():
                                continue
                            path = (
                                value.get("path") if isinstance(value, dict) else value
                            )
                            if isinstance(path, str) and Path(path).is_absolute():
                                libraries.append(Path(path).resolve())
                    except (OSError, RuntimeError, ValueError) as exc:
                        issues.append(f"{config}: {type(exc).__name__}")
                    break
                cache = root / "appcache/librarycache"
                watch(cache, root)
                for library in dict.fromkeys(libraries):
                    apps = library / "steamapps"
                    watch(apps, library)
                    for manifest in sorted(apps.glob("appmanifest_*.acf")):
                        try:
                            manifest = manifest.resolve()
                            if manifest in manifests_seen:
                                continue
                            manifests_seen.add(manifest)
                            game = self._game(manifest, root, flatpak)
                            if game is not None:
                                games.append(game)
                                watch(cache / game.app_id, root)
                        except (OSError, RuntimeError, ValueError) as exc:
                            issues.append(f"{manifest}: {type(exc).__name__}")
            except (OSError, RuntimeError, ValueError) as exc:
                issues.append(f"{candidate}: {type(exc).__name__}")
        return SteamDiscoveryResult(tuple(games), tuple(sorted(watches)), tuple(issues))

    @staticmethod
    def _game(manifest: Path, root: Path, flatpak: bool) -> SteamGame | None:
        state = _read_vdf(manifest).get("appstate")
        if not isinstance(state, dict):
            raise ValueError("Missing AppState")
        app_id = valid_steam_app_id(str(state.get("appid", "")))
        name, directory = state.get("name"), state.get("installdir")
        if not app_id or manifest.name != f"appmanifest_{app_id}.acf":
            raise ValueError("Invalid manifest application ID")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(directory, str)
        ):
            raise ValueError("Incomplete manifest")
        if int(str(state.get("stateflags", "0"))) & 1:
            return None
        common = (manifest.parent / "common").resolve()
        relative = Path(directory)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Invalid installation directory")
        install = (common / relative).resolve()
        if not install.is_dir() or (install / "toolmanifest.vdf").exists():
            return None
        cache = root / "appcache/librarycache"
        icons = sorted((cache / app_id).glob("*.jpg"))
        icons.append(cache / f"{app_id}_icon.jpg")
        icon = next(
            (
                str(path)
                for path in icons
                if path.is_file()
                and (
                    path.name == f"{app_id}_icon.jpg"
                    or re.fullmatch(r"[a-f0-9]{40}\.jpg", path.name)
                )
            ),
            "",
        )
        return SteamGame(app_id, name.strip(), install, manifest, root, flatpak, icon)
