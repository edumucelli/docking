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
    workarea: tuple[int, int, int, int] | None = None

    def marker(self) -> dict[str, int] | None:
        """Find the cyan fixture icons in an independently captured root image."""
        points = set()
        for y in range(self.height):
            for x in range(self.width):
                offset = y * self.stride + x * self.channels
                r, g, b = self.pixels[offset : offset + 3]
                # Keep the fixture's cyan hue through hover brightening while
                # rejecting Xfwm's blue titlebar gradients.
                if (
                    r < 80
                    and 145 < g < 235
                    and b > 180
                    and b - g <= 40
                    and g - r > 110
                    and abs(190 * r - 30 * g) <= 760
                    and abs(b - min(255, round(220 * g / 190))) <= 5
                ):
                    points.add((x, y))
        left, top, right, bottom, count = self.width, self.height, -1, -1, 0
        while points:
            point = points.pop()
            pending = [point]
            component = {point}
            while pending:
                x, y = pending.pop()
                for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if neighbor in points:
                        points.remove(neighbor)
                        component.add(neighbor)
                        pending.append(neighbor)
            x0, x1 = min(x for x, y in component), max(x for x, y in component)
            y0, y1 = min(y for x, y in component), max(y for x, y in component)
            # Native panels and titlebars also show small copies of the fixture icon.
            if x1 - x0 + 1 < 32 or y1 - y0 + 1 < 24:
                continue
            left, top = min(left, x0), min(top, y0)
            right, bottom = max(right, x1), max(bottom, y1)
            count += len(component)
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
    x, y, width, height = frame.workarea or (0, 0, frame.width, frame.height)
    if edge in {"top", "bottom"}:
        center = (bounds["left"] + bounds["right"] + 1) / 2
        expected = x + width / 2
        distance = (
            bounds["top"] - y if edge == "top" else y + height - 1 - bounds["bottom"]
        )
    elif edge in {"left", "right"}:
        center = (bounds["top"] + bounds["bottom"] + 1) / 2
        expected = y + height / 2
        distance = (
            bounds["left"] - x if edge == "left" else x + width - 1 - bounds["right"]
        )
    else:
        raise ValueError(f"Unknown edge: {edge}")
    if abs(center - expected) > 12 or not gap <= distance <= gap + 48:
        raise AssertionError(
            f"Dock misplaced at {edge}: center={center}, distance={distance}, "
            f"requested gap={gap}, pixels={bounds}"
        )
    return {**bounds, "distance": distance}
