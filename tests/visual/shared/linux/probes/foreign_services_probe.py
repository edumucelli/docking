"""Exercise generic Wayland actions and Phoc previews without inventing geometry.

The full native_services_probe requires overlap/workspace capabilities which
foreign-toplevel management does not supply. Reuse its real GTK application and
fixture lifecycle, but prove only the services this protocol actually offers.
Private Phoc thumbnails additionally require DOCKING_PHOC_PRIVATE_PREVIEWS=1
in a bare compositor without the Phosh shell; the normal lane leaves the shell
singleton untouched and records the missing preview capability explicitly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

if __package__:
    from . import native_services_probe as native
else:
    import native_services_probe as native


class ForeignServicesProbe(native.Probe):
    async def run(self):
        assert self.backend.name == os.environ["LAB_EXPECTED_BACKEND"]
        caps = self.backend.capabilities
        assert caps.tracks_windows
        assert caps.supports_activate
        assert not caps.supports_overlap_active
        assert not caps.supports_overlap_any
        self.child = subprocess.Popen([sys.executable, native.__file__, "--fixture"])
        await self.wait(
            lambda: any(
                row.title == "Native backend fixture"
                for row in self.backend.windows.list_all_windows()
            ),
            "Foreign toplevel fixture not tracked",
        )
        row = next(
            row
            for row in self.backend.windows.list_all_windows()
            if row.title == "Native backend fixture"
        )
        assert row.geometry is None
        assert self.backend.windows.activate(row.id).succeeded
        await self.wait(
            lambda: any(
                item.id == row.id and item.active and not item.minimized
                for item in self.backend.windows.list_all_windows()
            ),
            "Foreign toplevel activation not delivered",
        )
        await self.settle(0.5)
        preview = None
        private_previews = os.environ.get("DOCKING_PHOC_PRIVATE_PREVIEWS") == "1"
        if os.environ["LAB_COMPOSITOR"] in {"phoc", "phosh"} and not private_previews:
            assert not row.can_preview
        if os.environ["LAB_COMPOSITOR"] == "phoc" and private_previews:
            assert row.can_preview

            def captured():
                nonlocal preview
                preview = self.backend.previews.capture(row.id, width=240, height=160)
                return preview is not None

            await self.wait(captured, "Phoc private thumbnail not delivered")
            image = preview.image
            assert 0 < image.get_width() <= 240
            assert 0 < image.get_height() <= 160
            # The compositor's thumbnail must contain the red GTK fixture;
            # receiving a protocol callback or a black buffer is insufficient.
            red, green, blue = self.pixel(
                image, (image.get_width() // 2, image.get_height() // 2)
            )
            assert red > 240 and green < 15 and blue < 15
            image.savev(str(self.evidence / "foreign-preview.png"), "png", [], [])
        assert row.can_minimize
        assert self.backend.windows.minimize_all("lab-native.desktop").succeeded
        # The protocol permits a compositor to ignore minimize. In particular,
        # Phoc accepts the request without publishing a minimized state. Record
        # this limitation explicitly instead of calling the request success
        # proof of an effect, or failing unrelated tracking/thumbnail checks.
        minimized = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            minimized = any(
                item.id == row.id and item.minimized
                for item in self.backend.windows.list_all_windows()
            )
            if minimized:
                break
            await self.settle(0.05)
        assert self.backend.windows.activate(row.id).succeeded
        await self.wait(
            lambda: any(
                item.id == row.id and item.active and not item.minimized
                for item in self.backend.windows.list_all_windows()
            ),
            "Foreign toplevel restore not delivered",
        )
        assert self.backend.idle is not None
        await self.wait(
            lambda: self.backend.idle.idle_seconds() is not None,
            "Idle notification not delivered",
        )
        before = self.backend.idle.idle_seconds()
        await self.settle(0.3)
        after = self.backend.idle.idle_seconds()
        assert after > before
        assert self.backend.windows.close(row.id).succeeded
        await self.wait(
            lambda: (
                not any(
                    item.id == row.id
                    for item in self.backend.windows.list_all_windows()
                )
            ),
            "Foreign toplevel close not delivered",
        )
        result = {
            "backend": self.backend.name,
            "compositor": os.environ["LAB_COMPOSITOR"],
            "tracking": True,
            "activate": True,
            "minimize_restore": minimized,
            "minimize_request_accepted": True,
            "unsupported_reasons": (
                {}
                if minimized
                else {
                    "minimize_restore": (
                        "Compositor ignored minimize or did not publish "
                        "a minimized state"
                    )
                }
            ),
            "close": True,
            "window_geometry": None,
            "idle_seconds": after,
            "preview_pixels": preview is not None,
            "private_previews_opted_in": private_previews,
        }
        if not row.can_preview:
            result["unsupported_reasons"]["preview_pixels"] = (
                "Phosh private thumbnails use a singleton shell protocol; "
                "normal clients leave it unbound"
            )
        (self.evidence / "foreign-services.json").write_text(
            json.dumps(result, indent=2)
        )
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    native.Probe = ForeignServicesProbe
    native.main()
