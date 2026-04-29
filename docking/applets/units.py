"""Shared unit-formatting helpers for applet icon labels."""

from __future__ import annotations


def format_compact_number(value: float) -> str:
    """Format a short numeric value without redundant trailing decimals."""
    if value >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")
