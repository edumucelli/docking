"""Tests for the Moon phase applet."""

from datetime import date

from docking.applets.moon import (
    MoonApplet,
    MoonData,
    fetch_moon_offline,
    illumination_from_phase,
    moon_phase_from_date,
    phase_name,
)
from docking.applets.moon.render import create_icon
from docking.applets.moon.state import _parse_moon_html

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
