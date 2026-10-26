"""Independent panel/dock pixels and a real maximized client's usable size."""

import json
import os
import subprocess
import sys
from pathlib import Path

import native_services_probe as native


class PanelProbe(native.Probe):
    def __init__(self, *args):
        super().__init__(*args)
        self.panel = None
        self.marker_pixels = None

    async def scene(self, phase, *, panel_present, expected_shift, base_marker=None):
        image = await self.capture(phase)
        points = native.dock_marker_points(image, (0,0,image.get_width(),image.get_height()))
        if self.marker_pixels is None:
            # Cairo can anti-alias the outermost pixel of the 48px launcher.
            assert len(points) >= 47 * 47, ("Full marker missing", phase, len(points))
            self.marker_pixels = len(points)
        assert abs(len(points) - self.marker_pixels) <= 4, ("Dock marker is clipped or covered", phase, len(points))
        axis = 1 if self.position in {"top", "bottom"} else 0
        marker = min(point[axis] for point in points)
        if base_marker is not None:
            assert marker - base_marker == expected_shift, (phase, marker, base_marker, expected_shift)
        if panel_present:
            width, height = image.get_width(), image.get_height()
            bounds = {
                "bottom": (0,height-40,width,height), "top": (0,0,width,40),
                "left": (0,0,40,height), "right": (width-40,0,width,height),
            }[self.position]
            x0,y0,x1,y1 = bounds
            for y in range(y0,y1):
                for x in range(x0,x1):
                    assert all(abs(actual-expected) <= 1
                               for actual,expected in zip(self.pixel(image,(x,y)), (30,30,76))), (
                        "Panel was displaced or covered", phase, x,y,
                    )
        return marker

    def start_panel(self):
        self.panel = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name("panel_probe.py")),
            "40", self.position,
        ], env={**os.environ, "LAB_PANEL_LAYER":"top"})

    async def client_size(self, expected):
        path = self.evidence / f"{self.position}-client-size.json"
        await self.wait(
            lambda: path.exists() and json.loads(path.read_text()) == expected,
            f"Maximized client did not receive usable size {expected}",
        )

    async def run(self):
        assert self.backend.name == "wayland-layer-shell"
        assert self.window.config.hide_mode == "none"
        self.window.autohide.set_disabled(True, reason="panel placement probe")
        self.window.autohide.set_hovered(False)
        await self.settle(0.5)
        baseline = await self.scene("before-panel", panel_present=False, expected_shift=0)
        path = self.evidence / f"{self.position}-client-size.json"
        self.child = subprocess.Popen([sys.executable, native.__file__, "--fixture"],
                                     env={**os.environ, "LAB_FIXTURE_SIZE_FILE":str(path)})
        horizontal = self.position in {"top","bottom"}
        own = [1280,667] if horizontal else [1227,720]
        both = [1280,627] if horizontal else [1187,720]
        panel_only = [1280,680] if horizontal else [1240,720]
        await self.client_size(own)
        self.start_panel()
        await self.client_size(both)
        shift = 40 if self.position in {"top","left"} else -40
        await self.scene("late-panel", panel_present=True, expected_shift=shift, base_marker=baseline)
        self.panel.terminate()
        self.panel.wait(timeout=3)
        self.panel = None
        await self.client_size(own)
        await self.scene("panel-removed", panel_present=False, expected_shift=0, base_marker=baseline)
        self.start_panel()
        await self.client_size(both)
        await self.scene("panel-restarted", panel_present=True, expected_shift=shift, base_marker=baseline)
        self.window.config.hide_mode = "always-on-top"
        self.window.placement.update_struts()
        await self.client_size(panel_only)
        await self.scene("reservation-cleared", panel_present=True, expected_shift=shift, base_marker=baseline)
        self.window.config.hide_mode = "none"
        self.window.placement.update_struts()
        await self.client_size(both)
        await self.scene("reservation-restored", panel_present=True, expected_shift=shift, base_marker=baseline)
        result = {"backend":self.backend.name, "position":self.position,
                  "late_panel_pixels":True, "restarted_panel_pixels":True,
                  "full_dock_marker":True, "reservation_cleared_restored":True,
                  "client_size_with_panel_and_dock":both}
        (self.evidence / f"{self.position}-panel-services.json").write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)

    def close(self):
        if self.panel is not None:
            self.panel.terminate()
            self.panel.wait(timeout=3)
        super().close()


if __name__ == "__main__":
    native.Probe = PanelProbe
    native.main()
