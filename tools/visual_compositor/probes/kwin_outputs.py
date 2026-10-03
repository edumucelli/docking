"""Read and apply native KDE output configuration on a separate Wayland connection."""

import json
import os
import sys
from pathlib import Path

from pywayland.client import Display

root = Path(__file__).parent
bindings = Path(os.environ["LAB_DIR"]) / "kde_protocols"
if not bindings.exists():
    from pywayland.scanner.protocol import Protocol

    protocols = [
        Protocol.parse_file(str(root / f"protocols/{name}.xml"))
        for name in ("kde-output-device-v2", "kde-output-management-v2")
    ]
    imports = {
        interface.name: protocol.name
        for protocol in protocols
        for interface in protocol.interface
    }
    bindings.mkdir()
    (bindings / "__init__.py").touch()
    for protocol in protocols:
        protocol.output(str(bindings), imports)
sys.path.insert(0, str(bindings.parent))
from kde_protocols.kde_output_device_v2 import KdeOutputDeviceV2
from kde_protocols.kde_output_management_v2 import KdeOutputManagementV2


def snapshot(scene=None):
    display = Display()
    outputs, modes, managers, proxies = {}, {}, [], []

    def added(registry, name, interface, version):
        if interface == KdeOutputManagementV2.name:
            managers.append(registry.bind(name, KdeOutputManagementV2, 1))
        elif interface == KdeOutputDeviceV2.name:
            device = registry.bind(name, KdeOutputDeviceV2, min(version, 2))
            proxies.append(device)
            outputs[device] = {}
            device.dispatcher["name"] = lambda d, v: outputs[d].update(name=v)
            device.dispatcher["geometry"] = lambda d, x, y, *args: outputs[d].update(
                x=x, y=y
            )
            device.dispatcher["scale"] = lambda d, v: outputs[d].update(scale=v)
            device.dispatcher["enabled"] = lambda d, v: outputs[d].update(
                enabled=bool(v)
            )
            device.dispatcher["current_mode"] = lambda d, m: outputs[d].update(mode=m)

            def mode_added(d, m):
                modes[m] = {}
                m.dispatcher["size"] = lambda proxy, w, h: modes[proxy].update(
                    width=w, height=h
                )

            device.dispatcher["mode"] = mode_added

    try:
        display.connect()
        registry = display.get_registry()
        registry.dispatcher["global"] = added
        for _ in range(5):
            display.roundtrip()
        active = sorted(
            ((d, o) for d, o in outputs.items() if o.get("enabled")),
            key=lambda pair: pair[1]["name"],
        )
        if scene is not None:
            if not managers or not active:
                raise RuntimeError("native KDE output management unavailable")
            config = managers[0].create_configuration()
            applied = []
            config.dispatcher["applied"] = lambda _: applied.append(True)
            config.dispatcher["failed"] = lambda _: applied.append(False)
            x = 0
            for index, (device, output) in enumerate(active):
                scale = (
                    1.25
                    if scene == "fractional" or (scene == "mixed-dpi" and index == 0)
                    else 1
                )
                config.scale(device, scale)
                config.position(device, x, 0)
                x += round(modes[output["mode"]]["width"] / scale)
            config.apply()
            for _ in range(20):
                display.roundtrip()
                if applied:
                    break
            if applied != [True]:
                raise RuntimeError("KWin rejected native output configuration")
        return [
            {
                "name": o["name"],
                "x": o["x"],
                "y": o["y"],
                "width": round(modes[o["mode"]]["width"] / o["scale"]),
                "height": round(modes[o["mode"]]["height"] / o["scale"]),
                "scale": o["scale"],
            }
            for d, o in active
        ]
    finally:
        display.disconnect()


if __name__ == "__main__":
    scene = sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == "scene" else None
    print(json.dumps({"outputs": snapshot(scene), "dock_rect": None}))
