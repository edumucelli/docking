"""Native Treeland actions and overlap pixels without invented window geometry."""

import json
import subprocess
import sys

from tests.visual.shared.linux.probes import native_services_probe as native


class TreelandProbe(native.Probe):
    async def run(self):
        assert self.backend.name == "treeland"
        assert self.backend.capabilities.supports_overlap_any
        self.window.autohide.set_disabled(True, reason="native visual probe")
        await self.wait(
            lambda: self.window.autohide.hide_offset < 0.05, "Dock not shown"
        )
        before = await self.capture("visible")
        points = native.dock_marker_points(
            before, (0, 0, before.get_width(), before.get_height())
        )
        assert len(points) >= 47 * 47, "Treeland dock marker missing"
        self.child = subprocess.Popen([sys.executable, native.__file__, "--fixture"])
        await self.wait(
            lambda: any(
                row.title == "Native backend fixture"
                for row in self.backend.windows.list_all_windows()
            ),
            "Fixture not tracked",
        )
        row = next(
            row
            for row in self.backend.windows.list_all_windows()
            if row.title == "Native backend fixture"
        )
        assert self.backend.windows.activate(row.id).succeeded
        await self.wait(
            lambda: any(
                item.id == row.id and item.active
                for item in self.backend.windows.list_all_windows()
            ),
            "Activation not delivered",
        )
        self.window.autohide.set_disabled(False, reason="native visual probe")
        await self.wait(
            lambda: self.window.autohide.hide_offset > 0.95, "Overlap did not hide dock"
        )
        changed, painted = await self.wait_for_marker_pixels(
            points, phase="hidden", present=False
        )
        assert changed >= painted * 0.98, "Treeland dock pixels did not disappear"
        if row.can_minimize:
            assert self.backend.windows.minimize_all("lab-native.desktop").succeeded
            await self.wait(
                lambda: any(
                    item.id == row.id and item.minimized
                    for item in self.backend.windows.list_all_windows()
                ),
                "Minimize not delivered",
            )
            assert self.backend.windows.activate(row.id).succeeded
            await self.wait(
                lambda: any(
                    item.id == row.id and not item.minimized and item.active
                    for item in self.backend.windows.list_all_windows()
                ),
                "Restore not delivered",
            )
        assert self.backend.windows.close(row.id).succeeded
        await self.wait(
            lambda: (
                not any(
                    item.id == row.id
                    for item in self.backend.windows.list_all_windows()
                )
            ),
            "Close not delivered",
        )
        await self.wait(
            lambda: self.window.autohide.hide_offset < 0.05,
            "Dock did not return after close",
        )
        changed, painted = await self.wait_for_marker_pixels(
            points, phase="restored", present=True
        )
        assert changed >= painted * 0.98, "Treeland dock pixels did not return"
        (self.evidence / "treeland-result.json").write_text(
            json.dumps(
                {
                    "backend": self.backend.name,
                    "activate": True,
                    "close": True,
                    "dodge_pixels": True,
                    "restore_pixels": True,
                }
            )
        )


if __name__ == "__main__":
    native.Probe = TreelandProbe
    native.main()
