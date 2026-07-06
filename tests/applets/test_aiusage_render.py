"""Tests for AI usage render functions."""

from __future__ import annotations

import cairo

from docking.applets.aiusage.render import (
    _draw_claude_logo,
    _draw_codex_logo,
    _draw_opencode_logo,
    render_icon,
)
from docking.applets.aiusage.state import (
    AiUsageState,
    DayEntry,
    DisplayMode,
    ModelUsage,
    Provider,
)


def _make_state(
    *,
    provider: Provider = Provider.CLAUDE,
    tokens: int = 1000,
    cost: float = 0.05,
) -> AiUsageState:
    usage = ModelUsage(
        input_tokens=tokens,
        output_tokens=0,
        cache_write_tokens=0,
        cache_read_tokens=0,
        precalculated_cost=cost if provider == Provider.OPENCODE else 0.0,
    )
    entry = DayEntry(
        date="2025-01-01",
        sessions=1,
        by_model=((_provider_model(provider), usage),),
    )
    return AiUsageState(days=(entry,))


def _provider_model(provider: Provider) -> str:
    if provider == Provider.CODEX:
        return "gpt-4"
    if provider == Provider.OPENCODE:
        return "opencode:default"
    return "claude-sonnet-4-5-20250929"


class TestDrawLogos:
    def test_draw_claude_logo_does_not_raise(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)
        _draw_claude_logo(cr=cr, size=48)

    def test_draw_codex_logo_does_not_raise(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)
        _draw_codex_logo(cr=cr, size=48)

    def test_draw_opencode_logo_does_not_raise(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)
        _draw_opencode_logo(cr=cr, size=48)

    def test_draw_logos_with_small_sizes(self):
        for size in (16, 24, 32):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            cr = cairo.Context(surface)
            _draw_claude_logo(cr=cr, size=size)
            _draw_codex_logo(cr=cr, size=size)
            _draw_opencode_logo(cr=cr, size=size)


class TestRenderIcon:
    def test_render_icon_claude_default(self):
        state = _make_state(provider=Provider.CLAUDE)
        result = render_icon(size=48, state=state)
        assert result is not None

    def test_render_icon_codex_dominant(self):
        state = _make_state(provider=Provider.CODEX)
        result = render_icon(size=48, state=state)
        assert result is not None

    def test_render_icon_opencode_dominant(self):
        state = _make_state(provider=Provider.OPENCODE, cost=0.0)
        result = render_icon(size=48, state=state)
        assert result is not None

    def test_render_icon_selected_provider_overrides_dominant(self):
        state = _make_state(provider=Provider.CLAUDE)
        result = render_icon(size=48, state=state, selected_provider=Provider.CODEX)
        assert result is not None

    def test_render_icon_tokens_mode_with_tokens(self):
        state = _make_state(provider=Provider.CLAUDE, tokens=5000, cost=0.10)
        result = render_icon(size=48, state=state, display_mode=DisplayMode.TOKENS)
        assert result is not None

    def test_render_icon_tokens_mode_with_selected_provider(self):
        state = _make_state(provider=Provider.CLAUDE, tokens=1000, cost=0.05)
        result = render_icon(
            size=48,
            state=state,
            selected_provider=Provider.CLAUDE,
            display_mode=DisplayMode.TOKENS,
        )
        assert result is not None

    def test_render_icon_cost_mode_with_selected_provider(self):
        state = _make_state(provider=Provider.CLAUDE, tokens=1000, cost=0.05)
        result = render_icon(
            size=48,
            state=state,
            selected_provider=Provider.CLAUDE,
            display_mode=DisplayMode.COST,
        )
        assert result is not None

    def test_render_icon_zero_usage_no_label(self):
        state = _make_state(provider=Provider.CLAUDE, tokens=0, cost=0.0)
        result = render_icon(size=48, state=state)
        assert result is not None

    def test_render_icon_large_cost_uses_integer_format(self):
        state = _make_state(provider=Provider.CLAUDE, tokens=100000, cost=5.0)
        result = render_icon(size=48, state=state)
        assert result is not None

    def test_render_icon_no_entries(self):
        state = AiUsageState(days=())
        result = render_icon(size=48, state=state)
        assert result is not None
