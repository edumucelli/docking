"""Tests for the indicator fill branch (flat vs glow halo)."""

from __future__ import annotations

from unittest.mock import MagicMock

import cairo
import pytest

import docking.ui.renderer as renderer_mod
from docking.core.theme import IndicatorFill


def _surface_context(width: int = 200, height: int = 80):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


class TestDotsFillBranch:
    """``_draw_indicator_dots`` branches on ``fill`` to pick its brush."""

    def test_flat_fill_uses_set_source_rgba_with_color(self):
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dots(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=2,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 0.8),
            fill=IndicatorFill.FLAT,
        )
        assert cr.set_source_rgba.called
        cr.set_source.assert_not_called()
        last_call = cr.set_source_rgba.call_args
        assert last_call.args == (0.4, 0.5, 0.6, 0.8)

    def test_glow_fill_uses_radial_gradient_brush(self, monkeypatch):
        gradients = []

        class _RecordingRadial:
            def __init__(self, *args, **kwargs):
                gradients.append(self)
                self.stops = []

            def add_color_stop_rgba(self, offset, r, g, b, a):
                self.stops.append((offset, r, g, b, a))

        monkeypatch.setattr(renderer_mod.cairo, "RadialGradient", _RecordingRadial)
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dots(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=1,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 1.0),
            fill=IndicatorFill.GLOW,
        )
        assert len(gradients) == 1
        assert cr.set_source.called
        # Plank's six gradient stops.
        assert [stop[0] for stop in gradients[0].stops] == [
            0.0,
            0.1,
            0.2,
            0.25,
            0.5,
            1.0,
        ]
        # Stop alphas: 1.0, 1.0, 0.6, 0.25, 0.15, 0.0 (color.a==1)
        alphas = [stop[4] for stop in gradients[0].stops]
        assert alphas == pytest.approx([1.0, 1.0, 0.6, 0.25, 0.15, 0.0])

    def test_glow_arc_uses_expanded_paint_radius(self):
        """The arc footprint expands so the halo has room to fall off."""
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dots(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=1,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 1.0),
            fill=IndicatorFill.GLOW,
        )
        arc_radius = cr.arc.call_args.args[2]
        assert arc_radius == pytest.approx(
            2.5 * renderer_mod.INDICATOR_GLOW_RADIUS_MULT
        )

    def test_flat_arc_uses_solid_radius(self):
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dots(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=1,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 1.0),
            fill=IndicatorFill.FLAT,
        )
        arc_radius = cr.arc.call_args.args[2]
        assert arc_radius == pytest.approx(2.5)


class TestDashesFillBranch:
    """``_draw_indicator_dashes`` branches on ``fill`` to pick its brush."""

    def test_flat_fill_uses_set_source_rgba(self):
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dashes(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=1,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 0.7),
            fill=IndicatorFill.FLAT,
        )
        cr.set_source.assert_not_called()
        last_call = cr.set_source_rgba.call_args
        assert last_call.args == (0.4, 0.5, 0.6, 0.7)

    def test_glow_fill_uses_radial_gradient(self, monkeypatch):
        gradients = []

        class _RecordingRadial:
            def __init__(self, *args, **kwargs):
                gradients.append(self)
                self.stops = []

            def add_color_stop_rgba(self, offset, r, g, b, a):
                self.stops.append((offset, r, g, b, a))

        monkeypatch.setattr(renderer_mod.cairo, "RadialGradient", _RecordingRadial)
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dashes(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=2,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 1.0),
            fill=IndicatorFill.GLOW,
        )
        assert len(gradients) == 2  # one per dash
        for gradient in gradients:
            assert [stop[0] for stop in gradient.stops] == [
                0.0,
                0.1,
                0.2,
                0.25,
                0.5,
                1.0,
            ]


class TestGlowAlphaMultiplication:
    """``color.a`` multiplies every gradient stop (Docking convention)."""

    def test_color_alpha_scales_gradient_stops(self, monkeypatch):
        gradients = []

        class _RecordingRadial:
            def __init__(self, *args, **kwargs):
                gradients.append(self)
                self.stops = []

            def add_color_stop_rgba(self, offset, r, g, b, a):
                self.stops.append((offset, r, g, b, a))

        monkeypatch.setattr(renderer_mod.cairo, "RadialGradient", _RecordingRadial)
        cr = MagicMock(spec=cairo.Context)
        renderer_mod._draw_indicator_dots(
            cr=cr,
            cx=50.0,
            cy=70.0,
            radius=2.5,
            spacing=7.5,
            count=1,
            horizontal=True,
            color=(0.4, 0.5, 0.6, 0.5),  # half-alpha color
            fill=IndicatorFill.GLOW,
        )
        alphas = [stop[4] for stop in gradients[0].stops]
        # Plank's stops at full alpha: 1.0, 1.0, 0.6, 0.25, 0.15, 0.0
        # With color.a=0.5 each multiplies by 0.5:
        assert alphas == pytest.approx([0.5, 0.5, 0.3, 0.125, 0.075, 0.0])
