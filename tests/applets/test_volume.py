"""Tests for the volume applet."""

from unittest.mock import patch

from docking.applets.volume import (
    VolumeApplet,
    VolumeState,
    _detect_backend,
    _parse_amixer,
    _parse_pactl_mute,
    _parse_pactl_volume,
    _volume_icon_name,
)

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
            "docking.applets.volume.shutil.which",
            side_effect=[None, "/usr/bin/amixer"],
        ):
            result = _detect_backend()
        assert result is not None
        assert result.command == "amixer"

    def test_returns_none_when_nothing_found(self):
        with patch("docking.applets.volume.shutil.which", return_value=None):
            assert _detect_backend() is None


# -- Applet -------------------------------------------------------------------

_MOCK_STATE = VolumeState(volume=45, muted=False)


def _make_applet(state: VolumeState = _MOCK_STATE) -> VolumeApplet:
    """Create applet with mocked backend."""
    with patch("docking.applets.volume._detect_backend") as mock_detect:
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

    def test_no_menu_items(self):
        applet = _make_applet()
        assert applet.get_menu_items() == []
