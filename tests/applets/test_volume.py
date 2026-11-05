"""Tests for the volume applet."""

from unittest.mock import MagicMock, patch

import pytest

import docking.applets.volume.applet as volume_applet_mod
import docking.applets.volume.implementations.linux.backend as linux_mod
import docking.applets.volume.implementations.unix.command as volume_command_mod
import docking.applets.volume.implementations.unix.desktop_settings as volume_settings_mod
import docking.applets.volume.implementations.unix.pulseaudio as pulseaudio_mod
from docking.applets.volume.applet import VolumeApplet
from docking.applets.volume.backend import VolumeBackend, VolumeSettings
from docking.applets.volume.implementations.linux.backend import _parse_amixer
from docking.applets.volume.implementations.unix.desktop_settings import (
    open_volume_settings,
    volume_settings_command,
)
from docking.applets.volume.implementations.unix.pulseaudio import (
    _parse_pactl_mute,
    _parse_pactl_volume,
)
from docking.applets.volume.state import (
    VolumeState,
    _volume_icon_name,
)
from docking.core.config import Config
from docking.platform import environment
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


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

    def run_guarded(self, *, key, **kwargs):
        self.run(**kwargs)
        return True


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
    @pytest.fixture(autouse=True)
    def unix_environment(self, monkeypatch):
        monkeypatch.setattr(environment, "is_windows", lambda: False)
        monkeypatch.setattr(environment, "is_bsd", lambda: False)

    def test_returns_first_available(self):
        with patch(
            "docking.applets.volume.implementations.linux.backend.shutil.which",
            side_effect=[None, "/usr/bin/amixer"],
        ):
            result = TestAppletBackendFactories().volume()
        assert result.controls is not None
        assert result.controls.command == "amixer"

    def test_returns_none_when_nothing_found(self):
        with patch(
            "docking.applets.volume.implementations.linux.backend.shutil.which",
            return_value=None,
        ):
            assert TestAppletBackendFactories().volume().controls is None

    def test_settings_work_without_mixer_commands(self, monkeypatch):
        launched = []
        monkeypatch.setattr(
            linux_mod.shutil,
            "which",
            lambda cmd: (
                "/bin/mate-volume-control" if cmd == "mate-volume-control" else None
            ),
        )
        monkeypatch.setattr(
            volume_settings_mod.subprocess,
            "Popen",
            lambda cmd, **_kwargs: launched.append(cmd),
        )
        backend = TestAppletBackendFactories().volume()
        assert backend.controls is None and backend.settings.available()
        assert backend.settings.open()
        assert launched == [["mate-volume-control"]]


class TestVolumeSettingsLauncher:
    def test_prefers_first_available_volume_settings_command(self, monkeypatch):
        monkeypatch.setattr(
            volume_settings_mod.shutil,
            "which",
            lambda cmd: (
                "/usr/bin/gnome-control-center"
                if cmd == "gnome-control-center"
                else None
            ),
        )

        assert volume_settings_command() == ["gnome-control-center", "sound"]

    def test_returns_none_when_no_volume_settings_tool_exists(self, monkeypatch):
        monkeypatch.setattr(volume_settings_mod.shutil, "which", lambda _cmd: None)

        assert volume_settings_command() is None

    def test_open_volume_settings_launches_detected_command(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            volume_settings_mod,
            "volume_settings_command",
            lambda: ["mate-volume-control"],
        )
        monkeypatch.setattr(
            volume_settings_mod.subprocess,
            "Popen",
            lambda cmd, **_kwargs: launched.append(cmd),
        )

        assert open_volume_settings() is True
        assert launched == [["mate-volume-control"]]


# -- Applet -------------------------------------------------------------------

_MOCK_STATE = VolumeState(volume=45, muted=False)


def _make_applet(
    state: VolumeState = _MOCK_STATE,
    *,
    config: Config | None = None,
) -> VolumeApplet:
    """Create applet with mocked backend."""
    controls = MagicMock()
    controls.command = "pactl"
    controls.get_state.return_value = state
    backend = VolumeBackend(controls, VolumeSettings(lambda: False, lambda: False))
    with (
        patch("docking.applets.volume.applet.BackgroundWorker", _ImmediateWorker),
        patch(
            "tests.platform_factories.TestAppletBackendFactories.volume",
            return_value=backend,
        ),
    ):
        return VolumeApplet(
            48, config=config or Config(), platform_services=make_applet_platform()
        )


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

    def test_loads_show_level_pref(self):
        config = Config(applet_prefs={"volume": {"show_level": True}})
        applet = _make_applet(config=config)
        assert applet._show_level is True

    def test_tooltip_when_muted(self):
        applet = _make_applet(state=VolumeState(volume=45, muted=True))
        assert applet.item.name == "Muted"

    def test_on_clicked_toggles_mute(self):
        applet = _make_applet()
        applet.on_clicked()
        applet._backend.controls.toggle_mute.assert_called_once()

    def test_scroll_up_increases_volume(self):
        applet = _make_applet()
        applet.on_scroll(direction_up=True)
        applet._backend.controls.set_volume.assert_called_once_with(50)

    def test_scroll_down_decreases_volume(self):
        applet = _make_applet()
        applet.on_scroll(direction_up=False)
        applet._backend.controls.set_volume.assert_called_once_with(40)

    def test_scroll_clamps_at_100(self):
        applet = _make_applet(state=VolumeState(volume=98, muted=False))
        applet.on_scroll(direction_up=True)
        applet._backend.controls.set_volume.assert_called_once_with(100)

    def test_scroll_clamps_at_0(self):
        applet = _make_applet(state=VolumeState(volume=2, muted=False))
        applet.on_scroll(direction_up=False)
        applet._backend.controls.set_volume.assert_called_once_with(0)

    def test_menu_shows_volume_settings_when_available(self):
        applet = _make_applet()
        opened = MagicMock(return_value=True)
        applet._backend = applet._backend._replace(
            settings=VolumeSettings(lambda: True, opened)
        )
        items = applet.get_menu_items()
        assert [item.get_label() for item in items] == [
            "Show Level",
            "",
            "Volume Settings",
        ]
        callback, args = items[2]._signals["activate"][0]
        callback(None, *args)
        opened.assert_called_once_with()

    def test_menu_keeps_show_level_without_volume_settings_tool(self):
        applet = _make_applet()
        assert [item.get_label() for item in applet.get_menu_items()] == ["Show Level"]

    def test_settings_remain_available_without_an_audio_mixer(self):
        applet = _make_applet_no_backend()
        opened = MagicMock(return_value=True)
        applet._backend = VolumeBackend(None, VolumeSettings(lambda: True, opened))
        items = applet.get_menu_items()
        callback, args = items[-1]._signals["activate"][0]
        callback(None, *args)
        opened.assert_called_once_with()
        applet.on_clicked()
        applet.on_scroll(direction_up=True)
        assert applet._tick()

    def test_toggle_show_level_saves_and_refreshes(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config(applet_prefs={})
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)
        applet.present = MagicMock()

        widget = MagicMock()
        widget.get_active.return_value = True
        applet._on_toggle_level(widget)

        assert applet._show_level is True
        assert config.applet_prefs["volume"] == {"show_level": True}
        applet.present.assert_called_once()


def _make_applet_no_backend() -> VolumeApplet:
    with patch(
        "tests.platform_factories.TestAppletBackendFactories.volume",
        return_value=VolumeBackend(None, VolumeSettings(lambda: False, lambda: False)),
    ):
        return VolumeApplet(
            48, config=Config(), platform_services=make_applet_platform()
        )


class TestVolumeStateBackendHelpers:
    def test_run_returns_stdout_on_success(self, monkeypatch):
        result = MagicMock(returncode=0, stdout="ok")
        monkeypatch.setattr(
            volume_command_mod.subprocess, "run", lambda *a, **k: result
        )
        assert volume_command_mod.run_command(["echo", "ok"], "test") == "ok"

    def test_run_returns_none_on_nonzero_exit(self, monkeypatch):
        result = MagicMock(returncode=1, stdout="")
        monkeypatch.setattr(
            volume_command_mod.subprocess, "run", lambda *a, **k: result
        )
        assert volume_command_mod.run_command(["bad"], "test") is None

    def test_run_returns_none_on_oserror(self, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(volume_command_mod.subprocess, "run", boom)
        assert volume_command_mod.run_command(["bad"], "test") is None

    def test_pactl_get_state_success(self, monkeypatch):
        calls = [
            "Volume: mono: 12345 / 70% / 0.00 dB",
            "Mute: no",
        ]
        monkeypatch.setattr(pulseaudio_mod, "run_command", lambda *a, **k: calls.pop(0))
        assert pulseaudio_mod._pactl_get_state() == VolumeState(volume=70, muted=False)

    def test_pactl_get_state_returns_none_on_invalid_parse(self, monkeypatch):
        calls = ["no volume", "Mute: ???"]
        monkeypatch.setattr(pulseaudio_mod, "run_command", lambda *a, **k: calls.pop(0))
        assert pulseaudio_mod._pactl_get_state() is None

    def test_pactl_get_state_returns_none_on_missing_command_output(self, monkeypatch):
        calls = [None, "Mute: yes"]
        monkeypatch.setattr(pulseaudio_mod, "run_command", lambda *a, **k: calls.pop(0))
        assert pulseaudio_mod._pactl_get_state() is None

    def test_pactl_set_and_toggle_call_run(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            pulseaudio_mod,
            "run_command",
            lambda cmd, action: seen.append((cmd, action)),
        )
        pulseaudio_mod._pactl_set_volume(33)
        pulseaudio_mod._pactl_toggle_mute()
        assert seen[0][0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "33%"]
        assert seen[1][0] == ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]

    def test_amixer_get_state_none_when_run_fails(self, monkeypatch):
        monkeypatch.setattr(linux_mod, "run_command", lambda *a, **k: None)
        assert linux_mod._amixer_get_state() is None

    def test_amixer_set_and_toggle_call_run(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            linux_mod,
            "run_command",
            lambda cmd, action: seen.append((cmd, action)),
        )
        linux_mod._amixer_set_volume(55)
        linux_mod._amixer_toggle_mute()
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
        applet._backend.controls.get_state.return_value = VolumeState(
            volume=70, muted=True
        )
        calls = []
        monkeypatch.setattr(
            applet,
            "_on_poll_result",
            lambda state, **_kwargs: calls.append(state) or False,
        )
        assert applet._tick() is True
        assert calls == [VolumeState(volume=70, muted=True)]
