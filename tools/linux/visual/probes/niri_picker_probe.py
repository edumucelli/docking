"""Pick an actual tiled fixture with native pointer input, then terminate it."""

import json
import os
import subprocess
import sys
import threading
import time

import native_services_probe as native


class PickerProbe(native.Probe):
    async def run(self):
        assert self.backend.name == "niri"
        assert self.backend.capabilities.supports_window_pick
        assert self.backend.capabilities.supports_process_kill
        picker = self.backend.window_picker
        assert picker is not None and picker.interactive
        self.child = subprocess.Popen([sys.executable, native.__file__, "--fixture"])
        await self.wait(
            lambda: any(row.title == "Native backend fixture"
                        for row in self.backend.windows.list_all_windows()),
            "Tiled picker fixture was not tracked",
        )
        await self.settle(0.5)
        row = next(row for row in self.backend.windows.list_all_windows()
                   if row.title == "Native backend fixture")
        assert row.pid == self.child.pid
        assert row.geometry is None, "Picker test must exercise a tiled window"
        await self.capture("before-pick")
        failures = []

        def click():
            try:
                from input_probe import connect_pointer
                time.sleep(0.5)
                display, pointer = connect_pointer()
                try:
                    pointer.motion_absolute(1, 120, 200, 1280, 720)
                    pointer.frame()
                    display.roundtrip()
                    pointer.button(2, 272, 1)
                    pointer.frame()
                    display.roundtrip()
                    pointer.button(3, 272, 0)
                    pointer.frame()
                    display.roundtrip()
                finally:
                    pointer.destroy()
                    display.disconnect()
            except Exception as error:
                failures.append(repr(error))
                picker.stop()

        worker = threading.Thread(target=click, daemon=True)
        worker.start()
        selected = picker.select_window()
        worker.join(timeout=2)
        assert not failures and not worker.is_alive(), failures
        assert selected is not None and selected.id == row.id
        assert selected.pid == self.child.pid and selected.pid != os.getpid()
        assert picker.kill(selected.id).succeeded
        await self.wait(lambda: self.child.poll() is not None,
                        "Selected fixture was not terminated")
        await self.wait(
            lambda: not any(item.id == row.id for item in self.backend.windows.list_all_windows()),
            "Killed fixture remained tracked",
        )
        result = {"backend":"niri", "native_pointer_selection":True,
                  "tiled_geometry_unknown":True, "pid_matches_fixture":True,
                  "owned_fixture_terminated":True}
        (self.evidence / "picker-services.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    native.Probe = PickerProbe
    native.main()
