"""Theme loading and color accessors."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bundled themes directory (relative to package)
_BUILTIN_THEMES_DIR = Path(__file__).resolve().parent.parent / "themes"


def _rgba(values: list[int]) -> tuple[float, float, float, float]:
    """Convert [R, G, B, A] (0-255) to Cairo-compatible (0.0-1.0) tuple."""
    return (values[0] / 255, values[1] / 255, values[2] / 255, values[3] / 255)


@dataclass(frozen=True)
class Theme:
    """Visual theme for the dock."""

    fill_start: tuple[float, float, float, float] = (41/255, 41/255, 41/255, 1.0)
    fill_end: tuple[float, float, float, float] = (80/255, 80/255, 80/255, 1.0)
    stroke: tuple[float, float, float, float] = (41/255, 41/255, 41/255, 1.0)
    stroke_width: float = 1.0
    inner_stroke: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    roundness: float = 4.0
    indicator_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 200/255)
    active_indicator_color: tuple[float, float, float, float] = (100/255, 180/255, 1.0, 1.0)
    indicator_radius: float = 2.5
    h_padding: float = 12.0
    top_padding: float = 4.0
    bottom_padding: float = 8.0
    item_padding: float = 6.0

    @classmethod
    def load(cls, name: str = "default") -> Theme:
        """Load theme by name, searching built-in themes dir."""
        path = _BUILTIN_THEMES_DIR / f"{name}.json"
        if not path.exists():
            return cls()

        with open(path) as f:
            data: dict[str, Any] = json.load(f)

        return cls(
            fill_start=_rgba(data.get("fill_start", [40, 40, 40, 220])),
            fill_end=_rgba(data.get("fill_end", [30, 30, 30, 220])),
            stroke=_rgba(data.get("stroke", [41, 41, 41, 255])),
            stroke_width=float(data.get("stroke_width", 1.0)),
            inner_stroke=_rgba(data.get("inner_stroke", [255, 255, 255, 255])),
            roundness=float(data.get("roundness", 4.0)),
            indicator_color=_rgba(data.get("indicator_color", [255, 255, 255, 200])),
            active_indicator_color=_rgba(data.get("active_indicator_color", [100, 180, 255, 255])),
            indicator_radius=float(data.get("indicator_radius", 2.5)),
            h_padding=float(data.get("h_padding", 12.0)),
            top_padding=float(data.get("top_padding", 4.0)),
            bottom_padding=float(data.get("bottom_padding", 8.0)),
            item_padding=float(data.get("item_padding", 6.0)),
        )
