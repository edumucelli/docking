"""Require real pixels from GameScope's display-only native layer-shell overlay."""

from __future__ import annotations

import json

from tests.visual.shared.linux.probes import native_services_probe as native


class OverlayProbe(native.Probe):
    async def run(self):
        assert self.backend.name == "wayland-layer-shell"
        results = []
        bottom = None
        # Keep one instance while exercising preferences: GameScope 3.16.31 can
        # crash when repeatedly destroying/recreating native overlay clients.
        for edge, gap in (
            ("bottom", 0),
            ("top", 0),
            ("left", 0),
            ("right", 0),
            ("bottom", 40),
        ):
            self.window.config.position = edge
            self.window.config.additional_distance_from_edge = gap
            self.window.placement.reposition()
            await self.settle(0.5)
            image = await self.capture(f"overlay-{edge}-{gap}")
            width, height = image.get_width(), image.get_height()
            points = native.dock_marker_points(image, (0, 0, width, height))
            assert len(points) >= 47 * 47, (
                "Visible launcher marker absent or incomplete"
            )
            x0, x1 = min(x for x, _ in points), max(x for x, _ in points) + 1
            y0, y1 = min(y for _, y in points), max(y for _, y in points) + 1
            horizontal = edge in {"bottom", "top"}
            center = (x0 + x1) / 2 if horizontal else (y0 + y1) / 2
            assert abs(center - (width if horizontal else height) / 2) <= 2
            distance = {
                "bottom": height - y1,
                "top": y0,
                "left": x0,
                "right": width - x1,
            }[edge]
            assert gap <= distance <= gap + 32, (
                f"{edge}: launcher is {distance}px from edge"
            )
            if edge == "bottom":
                if bottom is None:
                    bottom = distance
                else:
                    assert abs(distance - bottom - gap) <= 2, (
                        "Configured gap was not applied"
                    )
            results.append(
                {
                    "edge": edge,
                    "gap": gap,
                    "distance": distance,
                    "visible_pixels": len(points),
                }
            )
        (self.evidence / "overlay-result.json").write_text(
            json.dumps(
                {
                    "backend": self.backend.name,
                    "placements": results,
                    "input_supported": False,
                    "reason": "GameScope excludes external overlays from input focus",
                }
            )
        )


if __name__ == "__main__":
    native.Probe = OverlayProbe
    native.main()
