"""Small pixel checks without NumPy, Pillow or platform-specific baselines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    channels: int
    stride: int
    pixels: bytes

    def marker(self) -> dict[str, int] | None:
        """Find the cyan fixture icons in an independently captured root image."""
        left, top, right, bottom, count = self.width, self.height, -1, -1, 0
        for y in range(self.height):
            for x in range(self.width):
                offset = y * self.stride + x * self.channels
                r, g, b = self.pixels[offset : offset + 3]
                if r < 80 and 145 < g < 235 and b > 180:
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)
                    count += 1
        if not count:
            return None
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "count": count,
        }

    def changed_above(self, other: Frame, y_limit: int) -> int:
        """Count visible changes above the resting dock, excluding icon effects."""
        if (self.width, self.height) != (other.width, other.height):
            raise ValueError("Screenshots have different dimensions")
        count = 0
        for y in range(max(0, min(y_limit, self.height))):
            for x in range(self.width):
                a = y * self.stride + x * self.channels
                b = y * other.stride + x * other.channels
                if (
                    max(abs(self.pixels[a + c] - other.pixels[b + c]) for c in range(3))
                    > 20
                ):
                    count += 1
        return count


def placement(frame: Frame, edge: str, gap: int = 0) -> dict[str, int]:
    """Require substantial visible icons, centered along the requested edge."""
    bounds = frame.marker()
    if bounds is None or bounds["count"] < 900:
        raise AssertionError(f"Fixture icons absent or incomplete: {bounds}")
    if edge in {"top", "bottom"}:
        center = (bounds["left"] + bounds["right"] + 1) / 2
        expected = frame.width / 2
        distance = (
            bounds["top"] if edge == "top" else frame.height - 1 - bounds["bottom"]
        )
    elif edge in {"left", "right"}:
        center = (bounds["top"] + bounds["bottom"] + 1) / 2
        expected = frame.height / 2
        distance = (
            bounds["left"] if edge == "left" else frame.width - 1 - bounds["right"]
        )
    else:
        raise ValueError(f"Unknown edge: {edge}")
    if abs(center - expected) > 12 or not gap <= distance <= gap + 48:
        raise AssertionError(
            f"Dock misplaced at {edge}: center={center}, distance={distance}, "
            f"requested gap={gap}, pixels={bounds}"
        )
    return {**bounds, "distance": distance}
