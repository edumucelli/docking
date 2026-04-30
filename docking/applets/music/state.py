"""State and backend helpers for music control applet."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import gi

from docking.applets.tooltip import structured_tooltip
from docking.i18n import _

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.music import meta
from docking.log import get_logger, with_context

log = with_context(get_logger(name="music"), applet_id=meta.id)

VOLUME_STEP = 5

_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
_MPRIS_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
_RB_SERVICE = "org.gnome.Rhythmbox3"
_RB_MPRIS_SERVICE = f"{_MPRIS_PREFIX}rhythmbox"
_RB_VOLUME_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True, slots=True)
class MusicState:
    """Current selected media player state."""

    available: bool = False
    player_name: str = ""
    player_icon_name: str = ""
    player_bus_name: str = ""
    playback_status: str = "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    volume_percent: int = 0
    can_play_pause: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False
    art_url: str = ""
    track_url: str = ""


def clamp_percent(value: int) -> int:
    """Clamp integer percent to [0, 100]."""
    return max(0, min(100, int(value)))


def unavailable_state() -> MusicState:
    """Canonical unavailable state."""
    return MusicState()


def play_pause_menu_label(state: MusicState) -> str:
    """Menu label for transport toggle."""
    return _("Pause") if state.playback_status == "Playing" else _("Play")


def _normalize_playback_status(raw: str) -> str:
    """Normalize playback status values across backends."""
    value = raw.strip().lower()
    if value == "playing":
        return _("Playing")
    if value == "paused":
        return _("Paused")
    if value == "stopped":
        return _("Stopped")
    if value == "unknown":
        return _("Unknown")
    return raw.strip() or "Unknown"


def _normalize_desktop_entry(raw: str) -> str:
    """Normalize desktop-entry-like identifiers to GTK icon names."""
    value = raw.strip()
    if not value:
        return ""

    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if value.lower().endswith(".desktop"):
        value = value[: -len(".desktop")]
    if value.startswith(_MPRIS_PREFIX):
        value = value[len(_MPRIS_PREFIX) :]

    lowered = value.lower()
    if lowered in {"org.gnome.rhythmbox3", "rhythmbox3"}:
        return "rhythmbox"

    if "." in value:
        if lowered.startswith(("org.", "com.", "net.", "io.")):
            value = value.rsplit(".", 1)[-1]
        else:
            value = value.split(".", 1)[0]

    value = value.strip().lower()
    if value == "rhythmbox3":
        return "rhythmbox"
    return value


def _icon_name_from_bus_name(bus_name: str) -> str:
    if not bus_name:
        return ""
    tail = (
        bus_name[len(_MPRIS_PREFIX) :]
        if bus_name.startswith(_MPRIS_PREFIX)
        else bus_name
    )
    return _normalize_desktop_entry(tail)


def tooltip_text(state: MusicState) -> str:
    """Detailed tooltip text for the music applet."""
    if not state.available:
        return structured_tooltip(
            title=_("Music"),
            primary=_("No active player"),
        )

    details = " - ".join(part for part in [state.artist, state.title] if part)
    primary = None
    if details:
        primary = details
    elif state.title:
        primary = state.title
    secondary = []
    if state.album:
        secondary.append(f"Album: {state.album}")
    return structured_tooltip(
        title=_("Music"),
        primary=primary,
        details=secondary,
    )


def _unpack(value: Any) -> Any:
    if hasattr(value, "unpack"):
        try:
            return value.unpack()
        except Exception as exc:
            log.debug("Failed to unpack GLib value %r: %s", value, exc)
            return value
    return value


def _as_str(value: Any) -> str:
    value = _unpack(value)
    if value is None:
        return ""
    return str(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    value = _unpack(value)
    if isinstance(value, bool):
        return value
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    value = _unpack(value)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        log.debug("Failed to coerce music value %r to float: %s", value, exc)
        return default


def _normalize_volume_percent(value: float) -> int:
    """Normalize volume reported as ratio (0..1) or percent (0..100)."""
    # PulseAudio/MPRIS reports 0.0-1.0, ALSA reports 0-100; 1.5 threshold
    # distinguishes the two (no sane volume is between 1.5% and 150%).
    if value <= 1.5:
        return clamp_percent(round(value * 100))
    return clamp_percent(round(value))


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    return _as_str(metadata.get(key, ""))


def _metadata_artist(metadata: dict[str, Any]) -> str:
    value = _unpack(metadata.get("xesam:artist", []))
    if isinstance(value, list | tuple) and value:
        return str(value[0])
    return _as_str(value)


class MprisBackend:
    """Media control backend using the MPRIS DBus specification."""

    def __init__(self) -> None:
        self._last_active_bus_name = ""
        self._bus: Gio.DBusConnection | None = None
        self._dbus_proxy: Gio.DBusProxy | None = None
        self._player_proxies: dict[str, Gio.DBusProxy] = {}
        self._props_proxies: dict[str, Gio.DBusProxy] = {}

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._dbus_proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                None,
            )
        except GLib.Error as exc:
            log.debug("Failed to initialize MPRIS DBus proxies: %s", exc)
            self._bus = None
            self._dbus_proxy = None

    def get_state(self) -> MusicState:
        """Return selected active player state, or unavailable state."""
        states: list[MusicState] = []
        for bus_name in self.list_players():
            state = self._read_state(bus_name=bus_name)
            if state is not None:
                states.append(state)
        if not states:
            return unavailable_state()
        selected = self._select_player(states=states)
        self._last_active_bus_name = selected.player_bus_name
        return selected

    def get_state_for_bus_name(self, bus_name: str) -> MusicState:
        """Return state for an explicit MPRIS bus name if owned."""
        if not self.has_owner(bus_name=bus_name):
            return unavailable_state()
        state = self._read_state(bus_name=bus_name)
        if state is None:
            return unavailable_state()
        self._last_active_bus_name = state.player_bus_name
        return state

    def has_owner(self, bus_name: str) -> bool:
        """Check if a DBus name currently has an owner."""
        if self._dbus_proxy is None:
            return False
        try:
            result = self._dbus_proxy.call_sync(
                "NameHasOwner",
                GLib.Variant("(s)", (bus_name,)),
                Gio.DBusCallFlags.NONE,
                1200,
                None,
            )
            unpacked = result.unpack() if result is not None else ()
            return bool(unpacked[0]) if unpacked else False
        except GLib.Error as exc:
            log.debug("DBus NameHasOwner failed for %s: %s", bus_name, exc)
            return False

    def list_players(self) -> list[str]:
        """List available MPRIS player bus names."""
        if self._dbus_proxy is None:
            return []
        try:
            result = self._dbus_proxy.call_sync(
                "ListNames",
                None,
                Gio.DBusCallFlags.NONE,
                1200,
                None,
            )
            names = result.unpack()[0] if result is not None else []
            return sorted(
                name
                for name in names
                if isinstance(name, str) and name.startswith(_MPRIS_PREFIX)
            )
        except GLib.Error as exc:
            log.debug("DBus ListNames failed while listing MPRIS players: %s", exc)
            return []

    def play_pause(self, player_bus_name: str) -> bool:
        return self._call_player_method(
            player_bus_name=player_bus_name,
            method="PlayPause",
        )

    def next_track(self, player_bus_name: str) -> bool:
        return self._call_player_method(player_bus_name=player_bus_name, method="Next")

    def previous_track(self, player_bus_name: str) -> bool:
        return self._call_player_method(
            player_bus_name=player_bus_name, method="Previous"
        )

    def set_volume(self, player_bus_name: str, volume_percent: int) -> bool:
        props = self._get_props_proxy(bus_name=player_bus_name)
        if props is None:
            return False
        value = clamp_percent(volume_percent) / 100.0
        try:
            props.call_sync(
                "Set",
                GLib.Variant(
                    "(ssv)",
                    (_MPRIS_PLAYER_IFACE, "Volume", GLib.Variant("d", float(value))),
                ),
                Gio.DBusCallFlags.NONE,
                1200,
                None,
            )
            return True
        except GLib.Error as exc:
            log.debug("Failed to set MPRIS volume for %s: %s", player_bus_name, exc)
            return False

    def _select_player(self, states: list[MusicState]) -> MusicState:
        # Currently-playing players take priority; among those, prefer
        # the last-active one for session continuity.
        playing = [state for state in states if state.playback_status == "Playing"]
        if playing:
            return next(
                (
                    state
                    for state in playing
                    if state.player_bus_name == self._last_active_bus_name
                ),
                playing[0],
            )
        # No player is playing - fall back to last-active, then first available.
        return next(
            (
                state
                for state in states
                if state.player_bus_name == self._last_active_bus_name
            ),
            states[0],
        )

    def _player_display_name(self, bus_name: str) -> str:
        identity = self._get_property(
            bus_name=bus_name,
            interface_name=_MPRIS_ROOT_IFACE,
            property_name="Identity",
        )
        if identity:
            return _as_str(identity)

        if bus_name.startswith(_MPRIS_PREFIX):
            tail = bus_name[len(_MPRIS_PREFIX) :]
        else:
            tail = bus_name
        return tail.split(".", 1)[0].capitalize()

    def _read_state(self, bus_name: str) -> MusicState | None:
        if self._get_props_proxy(bus_name=bus_name) is None:
            return None

        player_icon_name = _normalize_desktop_entry(
            _as_str(
                self._get_property(
                    bus_name=bus_name,
                    interface_name=_MPRIS_ROOT_IFACE,
                    property_name="DesktopEntry",
                )
            )
        ) or _icon_name_from_bus_name(bus_name)

        metadata_raw = self._get_property(
            bus_name=bus_name,
            interface_name=_MPRIS_PLAYER_IFACE,
            property_name="Metadata",
        )
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        playback_status = _normalize_playback_status(
            _as_str(
                self._get_property(
                    bus_name=bus_name,
                    interface_name=_MPRIS_PLAYER_IFACE,
                    property_name="PlaybackStatus",
                )
            )
        )
        volume = _as_float(
            self._get_property(
                bus_name=bus_name,
                interface_name=_MPRIS_PLAYER_IFACE,
                property_name="Volume",
            ),
            default=0.0,
        )
        can_play = _as_bool(
            self._get_property(
                bus_name=bus_name,
                interface_name=_MPRIS_PLAYER_IFACE,
                property_name="CanPlay",
            ),
            default=True,
        )
        can_pause = _as_bool(
            self._get_property(
                bus_name=bus_name,
                interface_name=_MPRIS_PLAYER_IFACE,
                property_name="CanPause",
            ),
            default=True,
        )
        can_go_next = _as_bool(
            self._get_property(
                bus_name=bus_name,
                interface_name=_MPRIS_PLAYER_IFACE,
                property_name="CanGoNext",
            ),
            default=True,
        )
        can_go_previous = _as_bool(
            self._get_property(
                bus_name=bus_name,
                interface_name=_MPRIS_PLAYER_IFACE,
                property_name="CanGoPrevious",
            ),
            default=True,
        )

        return MusicState(
            available=True,
            player_name=self._player_display_name(bus_name=bus_name),
            player_icon_name=player_icon_name,
            player_bus_name=bus_name,
            playback_status=playback_status,
            title=_metadata_str(metadata=metadata, key="xesam:title"),
            artist=_metadata_artist(metadata=metadata),
            album=_metadata_str(metadata=metadata, key="xesam:album"),
            volume_percent=_normalize_volume_percent(volume),
            can_play_pause=can_play and can_pause,
            can_go_next=can_go_next,
            can_go_previous=can_go_previous,
            art_url=_metadata_str(metadata=metadata, key="mpris:artUrl"),
            track_url=_metadata_str(metadata=metadata, key="xesam:url"),
        )

    def _get_property(
        self,
        *,
        bus_name: str,
        interface_name: str,
        property_name: str,
    ) -> Any | None:
        props = self._get_props_proxy(bus_name=bus_name)
        if props is None:
            return None
        try:
            # "(ss)" = GLib.Variant type string for two strings (interface, property).
            # 1200ms timeout keeps UI responsive if a player is hung.
            result = props.call_sync(
                "Get",
                GLib.Variant("(ss)", (interface_name, property_name)),
                Gio.DBusCallFlags.NONE,
                1200,
                None,
            )
            unpacked = result.unpack() if result is not None else ()
            if not unpacked:
                return None
            return _unpack(unpacked[0])
        except GLib.Error as exc:
            log.debug(
                "Failed to read MPRIS property %s from %s: %s",
                property_name,
                bus_name,
                exc,
            )
            return None

    def _call_player_method(self, player_bus_name: str, method: str) -> bool:
        player = self._get_player_proxy(bus_name=player_bus_name)
        if player is None:
            return False
        try:
            player.call_sync(method, None, Gio.DBusCallFlags.NONE, 1200, None)
            return True
        except GLib.Error as exc:
            log.debug(
                "Failed to call MPRIS method %s on %s: %s",
                method,
                player_bus_name,
                exc,
            )
            return False

    def _get_player_proxy(self, bus_name: str) -> Gio.DBusProxy | None:
        # Player interface proxy for transport methods (PlayPause, Next, etc).
        if self._bus is None:
            return None
        proxy = self._player_proxies.get(bus_name)
        if proxy is not None:
            return proxy
        try:
            # DO_NOT_AUTO_START prevents launching the player process if it exited.
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                bus_name,
                _MPRIS_OBJECT_PATH,
                _MPRIS_PLAYER_IFACE,
                None,
            )
            self._player_proxies[bus_name] = proxy
            return proxy
        except GLib.Error as exc:
            log.debug("Failed to create MPRIS player proxy for %s: %s", bus_name, exc)
            return None

    def _get_props_proxy(self, bus_name: str) -> Gio.DBusProxy | None:
        """Separate Properties interface proxy - needed because Get/Set live on
        org.freedesktop.DBus.Properties, not on the Player interface."""
        if self._bus is None:
            return None
        proxy = self._props_proxies.get(bus_name)
        if proxy is not None:
            return proxy
        try:
            proxy = Gio.DBusProxy.new_sync(
                self._bus,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                bus_name,
                _MPRIS_OBJECT_PATH,
                _PROPERTIES_IFACE,
                None,
            )
            self._props_proxies[bus_name] = proxy
            return proxy
        except GLib.Error as exc:
            log.debug(
                "Failed to create MPRIS properties proxy for %s: %s",
                bus_name,
                exc,
            )
            return None


class PlayerctlBackend:
    """Media control backend using playerctl CLI."""

    def __init__(self) -> None:
        self._last_active_player = ""
        self._binary = shutil.which("playerctl")

    def get_state(
        self,
        preferred: str | None = None,
        strict_preferred: bool = False,
    ) -> MusicState:
        """Return selected playerctl state."""
        player = self._select_player(
            preferred=preferred,
            strict_preferred=strict_preferred,
        )
        if not player:
            return unavailable_state()
        state = self._read_state(player=player)
        if state.available:
            self._last_active_player = player
        return state

    def play_pause(self, preferred: str | None = None) -> bool:
        return self._run_action(
            player=self._select_player(
                preferred=preferred,
                strict_preferred=bool(preferred),
            ),
            action="play-pause",
        )

    def next_track(self, preferred: str | None = None) -> bool:
        return self._run_action(
            player=self._select_player(
                preferred=preferred,
                strict_preferred=bool(preferred),
            ),
            action="next",
        )

    def previous_track(self, preferred: str | None = None) -> bool:
        return self._run_action(
            player=self._select_player(
                preferred=preferred,
                strict_preferred=bool(preferred),
            ),
            action="previous",
        )

    def set_volume(self, preferred: str | None, volume_percent: int) -> bool:
        player = self._select_player(
            preferred=preferred,
            strict_preferred=bool(preferred),
        )
        if not player and preferred:
            # Fallback to relaxed matching when player naming differs
            # between backends (e.g. mpris bus name vs playerctl alias).
            player = self._select_player(
                preferred=preferred,
                strict_preferred=False,
            )
        if not player:
            return False
        out = self._run(
            cmd=[
                self._binary or "playerctl",
                "-p",
                player,
                "volume",
                f"{clamp_percent(volume_percent) / 100.0:.2f}",
            ],
            timeout=1.5,
        )
        return out is not None

    def _read_state(self, player: str) -> MusicState:
        status = self._run(
            cmd=[self._binary or "playerctl", "-p", player, "status"], timeout=1.5
        )
        if not status:
            return unavailable_state()
        metadata = self._run(
            cmd=[
                self._binary or "playerctl",
                "-p",
                player,
                "metadata",
                "--format",
                (
                    "{{artist}}\t{{title}}\t{{album}}\t{{mpris:artUrl}}\t"
                    "{{xesam:url}}\t{{playerName}}\t{{mpris:desktopEntry}}"
                ),
            ],
            timeout=1.8,
        )
        parts = [
            *(metadata or "").strip().split("\t"),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ][:7]
        artist, title, album, art_url, track_url, player_name, desktop_entry = parts

        volume_raw = self._run(
            cmd=[self._binary or "playerctl", "-p", player, "volume"], timeout=1.5
        )
        volume_percent = 0
        if volume_raw:
            try:
                volume_percent = _normalize_volume_percent(float(volume_raw.strip()))
            except ValueError as exc:
                log.debug("Invalid playerctl volume output %r: %s", volume_raw, exc)
                volume_percent = 0

        return MusicState(
            available=True,
            player_name=(player_name or player),
            player_icon_name=(
                _normalize_desktop_entry(desktop_entry)
                or _normalize_desktop_entry(player)
            ),
            player_bus_name=player,
            playback_status=_normalize_playback_status(status),
            title=title,
            artist=artist,
            album=album,
            volume_percent=volume_percent,
            can_play_pause=True,
            can_go_next=True,
            can_go_previous=True,
            art_url=art_url,
            track_url=track_url,
        )

    def _run_action(self, player: str | None, action: str) -> bool:
        if not player:
            return False
        out = self._run(
            cmd=[self._binary or "playerctl", "-p", player, action],
            timeout=1.5,
        )
        return out is not None

    def _select_player(
        self,
        preferred: str | None = None,
        strict_preferred: bool = False,
    ) -> str | None:
        players = self._list_players()
        if not players:
            return None

        preferred_match = self._match_player_name(players=players, preferred=preferred)
        if preferred_match:
            return preferred_match
        if strict_preferred and preferred:
            return None

        if self._last_active_player in players:
            return self._last_active_player

        playing = []
        for player in players:
            status = self._run(
                cmd=[self._binary or "playerctl", "-p", player, "status"], timeout=1.0
            )
            if status and status.strip() == "Playing":
                playing.append(player)
        if playing:
            return playing[0]
        return players[0]

    def _match_player_name(
        self,
        players: list[str],
        preferred: str | None,
    ) -> str | None:
        if not preferred:
            return None
        preferred_norm = preferred.strip()
        if not preferred_norm:
            return None
        if preferred_norm == _RB_SERVICE:
            preferred_norm = "rhythmbox"

        if preferred_norm in players:
            return preferred_norm

        lowered = preferred_norm.lower()
        for player in players:
            if player.lower() == lowered:
                return player

        if preferred_norm.startswith(_MPRIS_PREFIX):
            tail = preferred_norm[len(_MPRIS_PREFIX) :]
            base = tail.split(".", 1)[0]
            for player in players:
                if player.lower() == base.lower():
                    return player

        base_word = preferred_norm.split(" ", 1)[0]
        for player in players:
            if player.lower().startswith(base_word.lower()):
                return player
        return None

    def _list_players(self) -> list[str]:
        if not self._binary:
            return []
        out = self._run(cmd=[self._binary, "-l"], timeout=1.5)
        if not out:
            return []
        seen: set[str] = set()
        ordered: list[str] = []
        for line in out.splitlines():
            name = line.strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    def _run(self, cmd: list[str], timeout: float) -> str | None:
        if not self._binary:
            return None
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.bind(action="playerctl").debug("Failed to run %s: %s", cmd, exc)
        return None


class RhythmboxClientBackend:
    """Fallback backend using rhythmbox-client when MPRIS is unavailable."""

    def __init__(self) -> None:
        self._binary = shutil.which("rhythmbox-client")
        self._gdbus_binary = shutil.which("gdbus")
        self._settings: Gio.Settings | None = None
        schema_source = Gio.SettingsSchemaSource.get_default()
        if schema_source is not None:
            schema = schema_source.lookup("org.gnome.rhythmbox.player", False)
            if schema is not None:
                self._settings = Gio.Settings.new("org.gnome.rhythmbox.player")

    def get_state(self) -> MusicState:
        if not self._binary or not self._is_running():
            return unavailable_state()

        track_out = self._run(
            cmd=[
                self._binary,
                "--no-start",
                "--print-playing",
                "--print-playing-format",
                "%tt\t%ta\t%at\t%tu",
            ],
            timeout=1.8,
        )
        if track_out is None:
            return unavailable_state()

        text = track_out.strip()
        title = ""
        artist = ""
        album = ""
        track_url = ""
        playback_status = "Unknown"
        has_track_payload = text.count("\t") >= 3
        if has_track_payload:
            playback_status = "Playing"
            parts = [*text.split("\t"), "", "", "", ""][:4]
            title, artist, album, track_url = (part.strip() for part in parts)

        volume_percent = self._read_volume_percent()
        return MusicState(
            available=True,
            player_name="Rhythmbox",
            player_icon_name="rhythmbox",
            player_bus_name=_RB_SERVICE,
            playback_status=playback_status,
            title=title,
            artist=artist,
            album=album,
            volume_percent=volume_percent,
            can_play_pause=True,
            can_go_next=True,
            can_go_previous=True,
            art_url="",
            track_url=track_url,
        )

    def play_pause(self) -> bool:
        return self._run_action("--play-pause", gtk_action="play")

    def next_track(self) -> bool:
        return self._run_action("--next", gtk_action="play-next")

    def previous_track(self) -> bool:
        return self._run_action("--previous", gtk_action="play-previous")

    def set_volume(self, volume_percent: int) -> bool:
        if not self._binary:
            return False
        raw = f"{clamp_percent(volume_percent) / 100.0:.2f}"
        out = self._run(
            cmd=[self._binary, "--no-start", "--set-volume", raw],
            timeout=1.5,
        )
        return out is not None

    def _is_running(self) -> bool:
        if not self._binary:
            return False
        try:
            result = subprocess.run(
                [self._binary, "--no-start", "--check-running"],
                capture_output=True,
                text=True,
                timeout=1.2,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Failed to probe Rhythmbox running state: %s", exc)
            return False

    def _read_volume_percent(self) -> int:
        if self._settings is not None:
            try:
                return _normalize_volume_percent(self._settings.get_double("volume"))
            except Exception as exc:
                log.debug("Failed to read Rhythmbox GSettings volume: %s", exc)
        if not self._binary:
            return 0
        out = self._run(
            cmd=[self._binary, "--no-start", "--print-volume"],
            timeout=1.5,
        )
        if not out:
            return 0
        match = _RB_VOLUME_RE.search(out)
        if not match:
            return 0
        try:
            return _normalize_volume_percent(float(match.group(1)))
        except ValueError as exc:
            log.debug("Invalid Rhythmbox volume output %r: %s", out, exc)
            return 0

    def _run_action(self, action: str, gtk_action: str) -> bool:
        if self._binary:
            out = self._run(
                cmd=[self._binary, "--no-start", action],
                timeout=1.5,
            )
            if out is not None:
                return True
        return self._activate_gtk_action(action_name=gtk_action)

    def _activate_gtk_action(self, action_name: str) -> bool:
        if not self._gdbus_binary:
            return False
        try:
            result = subprocess.run(
                [
                    self._gdbus_binary,
                    "call",
                    "--session",
                    "--dest",
                    _RB_SERVICE,
                    "--object-path",
                    "/org/gnome/Rhythmbox3",
                    "--method",
                    "org.gtk.Actions.Activate",
                    action_name,
                    "[]",
                    "{}",
                ],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.bind(action="rhythmbox_gdbus").debug(
                "Failed to invoke GTK action %s: %s",
                action_name,
                exc,
            )
            return False

    def _run(self, cmd: list[str], timeout: float) -> str | None:
        if not self._binary:
            return None
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.bind(action="rhythmbox_client").debug("Failed to run %s: %s", cmd, exc)
        return None


class HybridBackend:
    """MPRIS-first backend with playerctl fallback."""

    def __init__(
        self,
        mpris: MprisBackend | None = None,
        playerctl: PlayerctlBackend | None = None,
        rhythmbox: RhythmboxClientBackend | None = None,
    ) -> None:
        self._mpris = mpris or MprisBackend()
        self._playerctl = playerctl or PlayerctlBackend()
        self._rhythmbox = rhythmbox or RhythmboxClientBackend()
        self._last_state = unavailable_state()
        self._last_source = ""

    def poll(self) -> MusicState:
        """Poll MPRIS/playerctl/rhythmbox and choose best active state."""
        preferred = self._last_state.player_bus_name or self._last_state.player_name
        candidates: list[tuple[str, MusicState]] = []

        rb_mpris_state = self._mpris.get_state_for_bus_name(_RB_MPRIS_SERVICE)
        if rb_mpris_state.available:
            candidates.append(("mpris-rhythmbox", rb_mpris_state))

        mpris_state = self._mpris.get_state()
        if mpris_state.available:
            candidates.append(("mpris", mpris_state))

        rb_state = self._rhythmbox.get_state()

        preferred_for_playerctl = preferred
        strict_playerctl = self._is_rhythmbox_hint(preferred)
        # If Rhythmbox is running but its MPRIS service is missing,
        # avoid accidentally selecting an unrelated player from playerctl.
        if rb_state.available and not rb_mpris_state.available:
            preferred_for_playerctl = "rhythmbox"
            strict_playerctl = True

        playerctl_state = self._playerctl.get_state(
            preferred=preferred_for_playerctl,
            strict_preferred=strict_playerctl,
        )
        if playerctl_state.available:
            candidates.append(("playerctl", playerctl_state))

        if rb_state.available:
            candidates.append(("rhythmbox", rb_state))

        if not candidates:
            self._last_state = unavailable_state()
            self._last_source = ""
            return self._last_state

        source, selected = max(
            candidates,
            key=lambda item: self._state_score(source=item[0], state=item[1]),
        )
        self._last_source = source
        self._last_state = selected
        return selected

    def play_pause(self, state: MusicState) -> bool:
        if not state.available:
            return False
        if self._is_rhythmbox_state(state) and self._rhythmbox.play_pause():
            return True
        if state.player_bus_name.startswith(_MPRIS_PREFIX) and self._mpris.play_pause(
            state.player_bus_name
        ):
            return True
        if self._playerctl.play_pause(
            preferred=state.player_bus_name or state.player_name,
        ):
            return True
        return self._rhythmbox.play_pause()

    def next_track(self, state: MusicState) -> bool:
        if not state.available:
            return False
        if self._is_rhythmbox_state(state) and self._rhythmbox.next_track():
            return True
        if state.player_bus_name.startswith(_MPRIS_PREFIX) and self._mpris.next_track(
            state.player_bus_name
        ):
            return True
        if self._playerctl.next_track(
            preferred=state.player_bus_name or state.player_name,
        ):
            return True
        return self._rhythmbox.next_track()

    def previous_track(self, state: MusicState) -> bool:
        if not state.available:
            return False
        if self._is_rhythmbox_state(state) and self._rhythmbox.previous_track():
            return True
        if state.player_bus_name.startswith(
            _MPRIS_PREFIX
        ) and self._mpris.previous_track(state.player_bus_name):
            return True
        if self._playerctl.previous_track(
            preferred=state.player_bus_name or state.player_name,
        ):
            return True
        return self._rhythmbox.previous_track()

    def set_volume(self, state: MusicState, volume_percent: int) -> bool:
        if not state.available:
            return False
        if self._is_rhythmbox_state(state) and self._rhythmbox.set_volume(
            volume_percent=volume_percent
        ):
            return True
        if state.player_bus_name.startswith(_MPRIS_PREFIX) and self._mpris.set_volume(
            state.player_bus_name, volume_percent
        ):
            return True
        if self._playerctl.set_volume(
            preferred=state.player_bus_name or state.player_name,
            volume_percent=volume_percent,
        ):
            return True
        if self._playerctl.set_volume(
            preferred=None,
            volume_percent=volume_percent,
        ):
            return True
        return self._rhythmbox.set_volume(volume_percent=volume_percent)

    def _state_score(self, source: str, state: MusicState) -> tuple[int, int, int, int]:
        status_score = {
            "Playing": 3,
            "Paused": 2,
            "Stopped": 1,
            "Unknown": 0,
        }.get(state.playback_status, 1)
        metadata_score = 1 if (state.title or state.artist) else 0
        source_score = {
            "mpris-rhythmbox": 4,
            "mpris": 3,
            "rhythmbox": 2,
            "playerctl": 1,
        }.get(source, 0)
        continuity_score = (
            1 if state.player_bus_name == self._last_state.player_bus_name else 0
        )
        return (status_score, metadata_score, source_score, continuity_score)

    def _is_rhythmbox_state(self, state: MusicState) -> bool:
        return state.player_bus_name == _RB_SERVICE

    def _is_rhythmbox_hint(self, preferred: str) -> bool:
        if not preferred:
            return False
        value = preferred.lower()
        return "rhythmbox" in value or preferred == _RB_SERVICE
