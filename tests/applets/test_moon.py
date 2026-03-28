"""Tests for the Moon phase applet."""

from datetime import date
from typing import ClassVar
from unittest.mock import MagicMock

import docking.applets.moon.applet as moon_applet_mod
import docking.applets.moon.offline as moon_offline_mod
import docking.applets.moon.state as moon_state_mod
from docking.applets.moon.applet import MoonApplet
from docking.applets.moon.offline import (
    fetch_moon_offline,
    illumination_from_phase,
    moon_phase_from_date,
)
from docking.applets.moon.render import create_icon
from docking.applets.moon.state import MoonData, _parse_moon_html, phase_name

_SAMPLE_HTML = """
<html><head><title>The Moon's Phase</title></head>
<body>
<table><tr>
<td><img src="images/moon10b.gif" width=256 height=256 border=0></td>
<td>
<b><font size=+1>The Moon for Mar 4, 2026 </font><br>
(At Midnight, US Central time, as viewed from the Northern Hemisphere)
</b>
<br><hr noshade>
Illuminated Fraction: 0.962
<br>
1.8 days after full moon
<br>
</td>
</tr></table>
</body></html>
"""

_SAMPLE_MOON = MoonData(
    image_name="moon10b",
    illumination=0.962,
    description="1.8 days after full moon",
    date_label="Mar 4, 2026",
)


class TestParseMoonHtml:
    def test_extracts_image_name(self):
        data = _parse_moon_html(html=_SAMPLE_HTML)
        assert data is not None
        assert data.image_name == "moon10b"

    def test_extracts_illumination(self):
        data = _parse_moon_html(html=_SAMPLE_HTML)
        assert data is not None
        assert data.illumination == 0.962

    def test_extracts_description(self):
        data = _parse_moon_html(html=_SAMPLE_HTML)
        assert data is not None
        assert "after full moon" in data.description

    def test_extracts_date_label(self):
        data = _parse_moon_html(html=_SAMPLE_HTML)
        assert data is not None
        assert "Mar 4, 2026" in data.date_label

    def test_returns_none_for_garbage(self):
        assert _parse_moon_html(html="<html>nothing</html>") is None

    def test_fetch_moon_from_network(self, monkeypatch):
        class _Resp:
            def read(self):
                return _SAMPLE_HTML.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(moon_state_mod, "urlopen", lambda req, timeout=10: _Resp())
        data = moon_state_mod.fetch_moon(day=date(2026, 3, 4))
        assert data is not None
        assert data.image_name == "moon10b"

    def test_fetch_moon_falls_back_to_offline(self, monkeypatch):
        monkeypatch.setattr(
            moon_state_mod,
            "urlopen",
            lambda req, timeout=10: (_ for _ in ()).throw(OSError("offline")),
        )
        monkeypatch.setattr(
            "docking.applets.moon.offline.fetch_moon_offline",
            lambda d=None: _SAMPLE_MOON,
        )
        data = moon_state_mod.fetch_moon(day=date(2026, 3, 4))
        assert data == _SAMPLE_MOON


class TestPhaseName:
    def test_waning_gibbous(self):
        assert phase_name(0.99, "0.5 days after full moon") == "Waning Gibbous"

    def test_new_exact(self):
        assert phase_name(0.01, "new moon") == "New"

    def test_waxing_crescent(self):
        assert phase_name(0.2, "3 days after new moon") == "Waxing Crescent"

    def test_waning_from_description(self):
        assert phase_name(0.8, "2 days after full moon") == "Waning Gibbous"

    def test_full_exact(self):
        assert phase_name(0.99, "full moon") == "Full"

    def test_low_illumination_fallback(self):
        assert phase_name(0.01, "") == "New"

    def test_high_illumination_fallback(self):
        assert phase_name(0.99, "") == "Full"

    def test_first_quarter(self):
        assert phase_name(0.5, "first quarter") == "1st Quarter"

    def test_other_phase_name_paths(self):
        assert phase_name(0.6, "before full moon") == "Waxing Gibbous"
        assert phase_name(0.4, "before new moon") == "Waning Crescent"
        assert phase_name(0.4, "third quarter") == "3rd Quarter"


class TestAstronomicalCalculation:
    def test_known_full_moon(self):
        # Jan 13, 2025 was a full moon
        phase = moon_phase_from_date(d=date(2025, 1, 13))
        illum = illumination_from_phase(phase=phase)
        assert illum > 0.95

    def test_known_new_moon(self):
        # Jan 29, 2025 was a new moon
        phase = moon_phase_from_date(d=date(2025, 1, 29))
        illum = illumination_from_phase(phase=phase)
        assert illum < 0.05

    def test_offline_fallback_returns_data(self):
        data = fetch_moon_offline(d=date(2026, 3, 4))
        assert data.illumination > 0
        assert data.date_label == "Mar 4, 2026"
        assert data.description

    def test_phase_from_illumination_buckets(self):
        assert moon_offline_mod.phase_from_illumination(0.3) == "Crescent"
        assert moon_offline_mod.phase_from_illumination(0.7) == "Gibbous"

    def test_moon_phase_from_date_uses_today_when_none(self, monkeypatch):
        class _FakeDate:
            @staticmethod
            def today():
                return date(2026, 3, 4)

        monkeypatch.setattr(moon_offline_mod, "date", _FakeDate)
        phase = moon_offline_mod.moon_phase_from_date(d=None)
        assert 0.0 <= phase <= 1.0

    def test_fetch_moon_offline_description_branches(self, monkeypatch):
        monkeypatch.setattr(
            moon_offline_mod, "illumination_from_phase", lambda phase: 0.5
        )

        monkeypatch.setattr(moon_offline_mod, "moon_phase_from_date", lambda d: 0.01)
        assert (
            "new moon"
            in moon_offline_mod.fetch_moon_offline(d=date(2026, 1, 1)).description
        )

        monkeypatch.setattr(moon_offline_mod, "moon_phase_from_date", lambda d: 0.24)
        assert (
            "first quarter"
            in moon_offline_mod.fetch_moon_offline(d=date(2026, 1, 1)).description
        )

        monkeypatch.setattr(moon_offline_mod, "moon_phase_from_date", lambda d: 0.5)
        assert (
            "full moon"
            in moon_offline_mod.fetch_moon_offline(d=date(2026, 1, 1)).description
        )

        monkeypatch.setattr(moon_offline_mod, "moon_phase_from_date", lambda d: 0.74)
        assert (
            "last quarter"
            in moon_offline_mod.fetch_moon_offline(d=date(2026, 1, 1)).description
        )

        monkeypatch.setattr(moon_offline_mod, "moon_phase_from_date", lambda d: 0.3)
        assert (
            "after new moon"
            in moon_offline_mod.fetch_moon_offline(d=date(2026, 1, 1)).description
        )

        monkeypatch.setattr(moon_offline_mod, "moon_phase_from_date", lambda d: 0.8)
        assert (
            "after full moon"
            in moon_offline_mod.fetch_moon_offline(d=date(2026, 1, 1)).description
        )


class TestRenderIcon:
    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            pixbuf = create_icon(size=size, illumination=0.5)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_renders_full(self):
        assert create_icon(size=48, illumination=1.0) is not None

    def test_renders_new(self):
        assert create_icon(size=48, illumination=0.0) is not None

    def test_renders_waning(self):
        assert create_icon(size=48, illumination=0.7, waning=True) is not None

    def test_renders_with_label(self):
        assert create_icon(size=48, illumination=0.5, label="Waxing") is not None


class TestMoonApplet:
    def test_creates_with_icon(self):
        applet = MoonApplet(48)
        assert applet.item.icon is not None

    def test_tooltip_loading(self):
        applet = MoonApplet(48)
        assert "loading" in applet.item.name.lower()

    def test_tooltip_after_data(self):
        applet = MoonApplet(48)
        applet._moon = _SAMPLE_MOON
        applet.refresh_tooltip()
        assert "96%" in applet.item.name

    def test_menu_has_entries(self):
        applet = MoonApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert "Show Phase Name" in labels
        assert "Refresh" in labels

    def test_icon_renders_with_data(self):
        applet = MoonApplet(48)
        applet._moon = _SAMPLE_MOON
        assert applet.create_icon(size=48) is not None

    def test_init_config_and_toggle_phase(self):
        class _Cfg:
            applet_prefs: ClassVar = {"moon": {"show_phase": False}}

            def save(self):
                return None

        applet = MoonApplet(48, config=_Cfg())
        assert applet._show_phase is False
        applet.present = MagicMock()

        class _Widget:
            def __init__(self, active: bool):
                self._active = active

            def get_active(self):
                return self._active

        applet._on_toggle_phase(_Widget(active=True))
        assert applet._show_phase is True
        applet.present.assert_called_once()

    def test_start_stop_tick_clicked_and_fetch_async(self, monkeypatch):
        applet = MoonApplet(48)
        calls: list[str] = []
        removed: list[int] = []
        applet._fetch_async = lambda: calls.append("fetch")  # type: ignore[assignment]
        monkeypatch.setattr(
            moon_applet_mod.GLib,
            "timeout_add_seconds",
            lambda sec, cb: 55,
        )
        monkeypatch.setattr(
            moon_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 55
        assert calls == ["fetch"]
        assert applet._tick() is True
        assert calls[-1] == "fetch"
        applet.on_clicked()
        assert calls[-1] == "fetch"

        applet.stop()
        assert applet._timer_id == 0
        assert removed == [55]

    def test_fetch_async_and_on_result_branches(self, monkeypatch):
        applet = MoonApplet(48)
        calls: list[tuple[str, object]] = []
        monkeypatch.setattr(moon_applet_mod, "fetch_moon", lambda: _SAMPLE_MOON)

        def fake_run_guarded(*, key, name, fn, on_result=None, on_error=None):
            _ = name, on_error
            calls.append((key, fn()))
            if on_result is not None:
                on_result(calls[-1][1])
            return True

        applet._worker.run_guarded = fake_run_guarded  # type: ignore[method-assign]
        applet._fetch_async()
        assert calls == [("fetch", _SAMPLE_MOON)]

        applet.present = MagicMock()
        assert applet._on_result(None) is False
        applet.present.assert_not_called()
        assert applet._on_result(_SAMPLE_MOON) is False
        applet.present.assert_called_once()
