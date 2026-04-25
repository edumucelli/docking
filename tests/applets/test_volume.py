"""Tests for the volume applet."""

from unittest.mock import MagicMock, patch

import docking.applets.volume.applet as volume_applet_mod
import docking.applets.volume.state as volume_state_mod
from docking.applets.volume.applet import VolumeApplet
from docking.applets.volume.state import (
    VolumeState,
    _detect_backend,
    _parse_amixer,
    _parse_pactl_mute,
    _parse_pactl_volume,
    _volume_icon_name,
    open_volume_settings,
    volume_settings_command,
)


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)


# -- Parsers ------------------------------------------------------------------


class TestParsePactlVolume:
    def test_stereo_output(self):
        output = (
            "Volume: front-left: 29479 /  45% / -20.82 dB,"
            "   front-right: 29479 /  45% / -20.82 dB\n"
            "        balance 0.00"
        )
        assert _parse_pactl_volume(output=output) == 45

    def test_mono_output(self):
        assert _parse_pactl_volume(output="Volume: mono: 65536 / 100% / 0.00 dB") == 100

    def test_zero(self):
        assert _parse_pactl_volume(output="Volume: mono: 0 /   0% / -inf dB") == 0

    def test_garbage(self):
        assert _parse_pactl_volume(output="no volume here") is None


class TestParsePactlMute:
    def test_muted(self):
        assert _parse_pactl_mute(output="Mute: yes") is True

    def test_not_muted(self):
        assert _parse_pactl_mute(output="Mute: no") is False

    def test_garbage(self):
        assert _parse_pactl_mute(output="something else") is None


class TestParseAmixer:
    def test_mono_on(self):
        output = (
            "Simple mixer control 'Master',0\n"
            "  Capabilities: pvolume pvolume-joined pswitch pswitch-joined\n"
            "  Playback channels: Mono\n"
            "  Limits: Playback 0 - 87\n"
            "  Mono: Playback 60 [69%] [-20.25dB] [on]\n"
        )
        assert _parse_amixer(output=output) == VolumeState(volume=69, muted=False)

    def test_stereo_off(self):
        output = (
            "  Front Left: Playback 0 [0%] [off]\n"
            "  Front Right: Playback 0 [0%] [off]\n"
        )
        assert _parse_amixer(output=output) == VolumeState(volume=0, muted=True)

    def test_garbage(self):
        assert _parse_amixer(output="no data") is None


# -- Icon name ----------------------------------------------------------------


class TestVolumeIconName:
    def test_muted(self):
        assert _volume_icon_name(volume=75, muted=True) == "audio-volume-muted"

    def test_zero(self):
        assert _volume_icon_name(volume=0, muted=False) == "audio-volume-muted"

    def test_low(self):
        assert _volume_icon_name(volume=20, muted=False) == "audio-volume-low"

    def test_medium(self):
        assert _volume_icon_name(volume=50, muted=False) == "audio-volume-medium"

    def test_high(self):
        assert _volume_icon_name(volume=80, muted=False) == "audio-volume-high"

    def test_boundary_33(self):
        assert _volume_icon_name(volume=33, muted=False) == "audio-volume-low"

    def test_boundary_34(self):
        assert _volume_icon_name(volume=34, muted=False) == "audio-volume-medium"

    def test_boundary_66(self):
        assert _volume_icon_name(volume=66, muted=False) == "audio-volume-medium"

    def test_boundary_67(self):
        assert _volume_icon_name(volume=67, muted=False) == "audio-volume-high"


# -- Backend detection --------------------------------------------------------


class TestDetectBackend:
    def test_returns_first_available(self):
        with patch(
            "docking.applets.volume.state.shutil.which",
            side_effect=[None, "/usr/bin/amixer"],
        ):
            result = _detect_backend()
        assert result is not None
        assert result.command == "amixer"

    def test_returns_none_when_nothing_found(self):
        with patch("docking.applets.volume.state.shutil.which", return_value=None):
            assert _detect_backend() is None


class TestVolumeSettingsLauncher:
    def test_prefers_first_available_volume_settings_command(self, monkeypatch):
        monkeypatch.setattr(
            volume_state_mod.shutil,
            "which",
            lambda cmd: (
                "/usr/bin/gnome-control-center"
                if cmd == "gnome-control-center"
                else None
            ),
        )

        assert volume_settings_command() == ["gnome-control-center", "sound"]

    def test_returns_none_when_no_volume_settings_tool_exists(self, monkeypatch):
        monkeypatch.setattr(volume_state_mod.shutil, "which", lambda _cmd: None)

        assert volume_settings_command() is None

    def test_open_volume_settings_launches_detected_command(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            volume_state_mod,
            "volume_settings_command",
            lambda: ["mate-volume-control"],
        )
        monkeypatch.setattr(
            volume_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(cmd),
        )

        assert open_volume_settings() is True
        assert launched == [["mate-volume-control"]]


# -- Applet -------------------------------------------------------------------

_MOCK_STATE = VolumeState(volume=45, muted=False)


def _make_applet(state: VolumeState = _MOCK_STATE) -> VolumeApplet:
    """Create applet with mocked backend."""
    with (
        patch("docking.applets.volume.applet.BackgroundWorker", _ImmediateWorker),
        patch("docking.applets.volume.applet._detect_backend") as mock_detect,
    ):
        mock_backend = mock_detect.return_value
        mock_backend.command = "pactl"
        mock_backend.get_state.return_value = state
        applet = VolumeApplet(48)
    # Re-attach the mock backend so tests can inspect calls
    applet._backend = mock_backend
    return applet


class TestVolumeApplet:
    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None
        assert applet.item.name == "Volume: 45%"

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet()
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_tooltip_when_muted(self):
        applet = _make_applet(state=VolumeState(volume=45, muted=True))
        assert applet.item.name == "Muted"

    def test_on_clicked_toggles_mute(self):
        applet = _make_applet()
        applet.on_clicked()
        applet._backend.toggle_mute.assert_called_once()

    def test_scroll_up_increases_volume(self):
        applet = _make_applet()
        applet.on_scroll(direction_up=True)
        applet._backend.set_volume.assert_called_once_with(50)

    def test_scroll_down_decreases_volume(self):
        applet = _make_applet()
        applet.on_scroll(direction_up=False)
        applet._backend.set_volume.assert_called_once_with(40)

    def test_scroll_clamps_at_100(self):
        applet = _make_applet(state=VolumeState(volume=98, muted=False))
        applet.on_scroll(direction_up=True)
        applet._backend.set_volume.assert_called_once_with(100)

    def test_scroll_clamps_at_0(self):
        applet = _make_applet(state=VolumeState(volume=2, muted=False))
        applet.on_scroll(direction_up=False)
        applet._backend.set_volume.assert_called_once_with(0)

    def test_menu_shows_volume_settings_when_available(self, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr(
            volume_applet_mod,
            "volume_settings_command",
            lambda: ["mate-volume-control"],
        )
        monkeypatch.setattr(
            volume_applet_mod,
            "open_volume_settings",
            lambda: opened.append("opened") or True,
        )

        applet = _make_applet()
        items = applet.get_menu_items()

        assert [item.get_label() for item in items] == ["Volume Settings"]
        callback, args = items[0]._signals["activate"][0]
        callback(None, *args)
        assert opened == ["opened"]

    def test_menu_is_empty_when_no_volume_settings_tool_is_available(self, monkeypatch):
        monkeypatch.setattr(volume_applet_mod, "volume_settings_command", lambda: None)

        applet = _make_applet()
        assert applet.get_menu_items() == []


def _make_applet_no_backend() -> VolumeApplet:
    with patch("docking.applets.volume.applet._detect_backend", return_value=None):
        return VolumeApplet(48)


class TestVolumeStateBackendHelpers:
    def test_run_returns_stdout_on_success(self, monkeypatch):
        result = MagicMock(returncode=0, stdout="ok")
        monkeypatch.setattr(volume_state_mod.subprocess, "run", lambda *a, **k: result)
        assert volume_state_mod._run(["echo", "ok"], "test") == "ok"

    def test_run_returns_none_on_nonzero_exit(self, monkeypatch):
        result = MagicMock(returncode=1, stdout="")
        monkeypatch.setattr(volume_state_mod.subprocess, "run", lambda *a, **k: result)
        assert volume_state_mod._run(["bad"], "test") is None

    def test_run_returns_none_on_oserror(self, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(volume_state_mod.subprocess, "run", boom)
        assert volume_state_mod._run(["bad"], "test") is None

    def test_pactl_get_state_success(self, monkeypatch):
        calls = [
            "Volume: mono: 12345 / 70% / 0.00 dB",
            "Mute: no",
        ]
        monkeypatch.setattr(volume_state_mod, "_run", lambda *a, **k: calls.pop(0))
        assert volume_state_mod._pactl_get_state() == VolumeState(
            volume=70, muted=False
        )

    def test_pactl_get_state_returns_none_on_invalid_parse(self, monkeypatch):
        calls = ["no volume", "Mute: ???"]
        monkeypatch.setattr(volume_state_mod, "_run", lambda *a, **k: calls.pop(0))
        assert volume_state_mod._pactl_get_state() is None

    def test_pactl_get_state_returns_none_on_missing_command_output(self, monkeypatch):
        calls = [None, "Mute: yes"]
        monkeypatch.setattr(volume_state_mod, "_run", lambda *a, **k: calls.pop(0))
        assert volume_state_mod._pactl_get_state() is None

    def test_pactl_set_and_toggle_call_run(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            volume_state_mod,
            "_run",
            lambda cmd, action: seen.append((cmd, action)),
        )
        volume_state_mod._pactl_set_volume(33)
        volume_state_mod._pactl_toggle_mute()
        assert seen[0][0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "33%"]
        assert seen[1][0] == ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]

    def test_amixer_get_state_none_when_run_fails(self, monkeypatch):
        monkeypatch.setattr(volume_state_mod, "_run", lambda *a, **k: None)
        assert volume_state_mod._amixer_get_state() is None

    def test_amixer_set_and_toggle_call_run(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            volume_state_mod,
            "_run",
            lambda cmd, action: seen.append((cmd, action)),
        )
        volume_state_mod._amixer_set_volume(55)
        volume_state_mod._amixer_toggle_mute()
        assert seen[0][0] == ["amixer", "set", "Master", "55%"]
        assert seen[1][0] == ["amixer", "set", "Master", "toggle"]


class TestVolumeAppletInternals:
    def test_start_and_stop_manage_timer(self, monkeypatch):
        applet = _make_applet()
        monkeypatch.setattr(
            volume_applet_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 321
        )
        removed = []
        monkeypatch.setattr(
            volume_applet_mod.GLib, "source_remove", lambda i: removed.append(i)
        )

        applet.start(lambda: None)
        assert applet._timer_id == 321

        applet.stop()
        assert removed == [321]
        assert applet._timer_id == 0

    def test_no_backend_branches_are_safe(self):
        applet = _make_applet_no_backend()
        assert applet._tick() is True
        applet.on_scroll(direction_up=True)
        applet.on_clicked()

    def test_on_poll_result_handles_none_and_no_change(self):
        applet = _make_applet()
        assert applet._on_poll_result(None) is False
        applet._volume = 45
        applet._muted = False
        applet.present = MagicMock()
        assert applet._on_poll_result(VolumeState(volume=45, muted=False)) is False
        applet.present.assert_not_called()

    def test_on_poll_result_updates_when_changed(self):
        applet = _make_applet()
        applet._volume = 10
        applet._muted = False
        applet.present = MagicMock()
        assert applet._on_poll_result(VolumeState(volume=60, muted=True)) is False
        assert applet._volume == 60
        assert applet._muted is True
        applet.present.assert_called_once()

    def test_tick_worker_posts_idle_callback(self, monkeypatch):
        applet = _make_applet()
        applet._backend.get_state.return_value = VolumeState(volume=70, muted=True)
        calls = []
        monkeypatch.setattr(
            applet, "_on_poll_result", lambda state: calls.append(state) or False
        )
        assert applet._tick() is True
        assert calls == [VolumeState(volume=70, muted=True)]
