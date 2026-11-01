"""Run independent x64/x86 tray ABI cases against Explorer or Docking's host.

These are live API/ownership checks. Optional phase holds let the desktop lab
observe real pointer/keyboard behavior before later identity mutations. They do
not label a passive protocol run as input or visual acceptance.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import struct
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.tray_acceptance import runtime_origins, select_runtime
from tools.windows.visual.tray_provider import (
    NativeProvider,
    ProviderCase,
    build_provider,
    provider_cases,
    validate_protocol,
)


def _bind(library, name, result, *arguments):
    # Names/signatures cross the external Win32 ABI boundary only.
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


class OwnedWireTrace:
    """Observe only a currently owned case; never journal unrelated providers."""

    def __init__(self, host):
        from docking.platform.tray.implementations.windows.messages import (
            decode_message,
        )

        self._host = host
        self._dispatch = host._dispatch_notification
        self._forward = host._forward
        self._active = None
        self._forwarded = 0

        def forward(window, message, first, second, *, handled=False):
            self._forwarded += 1
            return self._forward(window, message, first, second, handled=handled)

        def dispatch(window, first, second, payload):
            decoded = decode_message(payload)
            active = self._active
            before = self._forwarded
            result = self._dispatch(window, first, second, payload)
            if active is not None and decoded is not None:
                native_size, guid, identities, rows, project = active
                if (guid and decoded.guid == guid) or (
                    decoded.window,
                    decoded.uid,
                ) in identities:
                    if len(rows) >= 512:
                        rows[-1]["trace_truncated"] = True
                        return result
                    rows.append(
                        {
                            "operation": decoded.operation,
                            "flags": decoded.flags,
                            "version": decoded.version,
                            "window": decoded.window,
                            "uid": decoded.uid,
                            "native_cb_size": native_size,
                            "wire_cb_size": struct.unpack_from("<I", payload, 8)[0],
                            "raw_flags": struct.unpack_from("<I", payload, 20)[0],
                            "raw_prefix_hex": payload[:32].hex(),
                            "payload_length": len(payload),
                            "forwarded": self._forwarded > before,
                            "accepted": bool(result),
                            "projection": project(decoded),
                        }
                    )
            return result

        # Install before backend.get_state starts the native receiver. Startup
        # re-registration remains unobserved until an owned case is established.
        host._forward, host._dispatch_notification = forward, dispatch

    def begin(self, case, guid, identities, rows, project):
        self._active = (
            case.native_size,
            guid if case.identity == "guid" else "",
            frozenset((window & 0xFFFFFFFF, uid) for window, uid in identities),
            rows,
            project,
        )

    def clear(self):
        self._active = None

    def close(self):
        self.clear()
        self._host._dispatch_notification = self._dispatch
        self._host._forward = self._forward


def run_case(case, executable, directory, backend, wire, hold_phase, hold_seconds):
    directory.mkdir()
    guid = str(uuid.uuid4())
    clients, observations = {}, {}
    report = {
        "case": case.name,
        "evidence": {
            "protocol": "running",
            "physical_input": "not-run",
            "visual": "not-run",
        },
        "scope": "independent native providers",
        "observations": observations,
        "native_wire": [] if wire is not None else None,
    }

    def projection():
        if backend is None:
            return None
        public = backend.get_state()
        assert public.available
        visible = {item.identifier: item for item in public.items}
        expected = (
            {"guid:" + guid}
            if case.identity == "guid"
            else {
                f"window:{client._state['window'] & 0xFFFFFFFF:08x}:"
                f"{client._state['uid']}"
                for client in clients.values()
            }
        )
        with backend._lock:
            return {
                key: {
                    "owner": item.owner,
                    "window": item.window,
                    "uid": item.uid,
                    "version": item.version,
                    "tip": item.tip,
                    "state": item.state,
                    "pixels_copied": item.pixels is not None,
                    "visible": key in visible,
                    "show_tooltip": visible[key].show_tooltip
                    if key in visible
                    else None,
                }
                for key, item in backend._items.items()
                if key in expected
            }

    def observe(name):
        observations[name] = {
            "providers": {label: client.read() for label, client in clients.items()},
            "projection": projection(),
            "native_wire_count": len(report["native_wire"])
            if wire is not None
            else None,
        }
        write_shared_json(directory / "result.json", report)

    def owned_projection(decoded):
        with backend._lock:
            item = backend._items.get(decoded.identifier)
            return (
                {
                    "owner": item.owner,
                    "window": item.window,
                    "uid": item.uid,
                    "version": item.version,
                    "show_tip": item.show_tip,
                }
                if item is not None
                else None
            )

    def hold(phase):
        if phase != hold_phase or not hold_seconds:
            return
        report["interactive_phase"] = phase
        write_shared_json(directory / "result.json", report)
        deadline = time.monotonic() + hold_seconds
        while time.monotonic() < deadline and not (directory / "continue").exists():
            for client in clients.values():
                client.read()
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        observe(phase + "-after-hold")

    try:
        with ExitStack() as cleanup:
            for label in ("A", "B"):
                clients[label] = cleanup.enter_context(
                    NativeProvider(
                        executable,
                        directory / label,
                        case.configuration(
                            label, guid, seconds=min(600, hold_seconds + 120)
                        ),
                    )
                )
            if wire is not None:
                wire.begin(
                    case,
                    guid,
                    (
                        (client._state["window"], client._state["uid"])
                        for client in clients.values()
                    ),
                    report["native_wire"],
                    owned_projection,
                )
            clients["A"].command("add")
            observe("first-add")
            # Holds here precede EVERY collision/GUID-only mutation, so missing
            # events cannot be attributed to a later changed callback identity.
            hold("fresh")
            clients["A"].command("showtip", enabled=True)
            observe("showtip-only-on")
            hold("showtip-on")
            clients["A"].command("showtip", enabled=False)
            observe("showtip-only-off")
            hold("showtip-off")
            clients["B"].command("add")
            observe("second-add")
            hold("collision")
            for client in clients.values():
                client.command("rect", guid_only=case.identity == "guid")
            observe("rects-after-collision")
            clients["B"].command(
                "tip",
                text="Owned second-provider modification",
                guid_only=case.identity == "guid",
            )
            observe("second-provider-modify")
            hold("guid-modify")
            clients["A"].command("focus")
            observe("setfocus")
            if case.size != "v1":
                clients["A"].command("hide")
                observe("hidden")
                clients["A"].command("show")
                observe("shown")
            clients["A"].command("delete")
            observe("deleted")
            clients["A"].command("add")
            observe("readded")
            hold("readded")
            validate_protocol(case, observations, native_wire=report["native_wire"])
            for label, client in clients.items():
                record = client.read()
                assert record["bits"] == case.bits, label
                assert record["native_size"] == case.native_size, label
            report["evidence"]["protocol"] = "passed"
    except Exception as error:
        report["evidence"]["protocol"] = "failed"
        report["error"] = type(error).__name__
        raise
    finally:
        if wire is not None:
            wire.clear()
        report["providers_closed"] = {
            label: client._state for label, client in clients.items()
        }
        report["cleanup_ok"] = bool(clients) and all(
            not client.forced_cleanup
            and client.process is not None
            and client.process.returncode == 0
            and client._state["stage"] == "closed"
            and not client._state["registered"]
            and "error" not in client._state
            for client in clients.values()
        )
        report["projection_after_close"] = projection()
        write_shared_json(directory / "result.json", report)
    assert report["cleanup_ok"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--mode", choices=("explorer", "backend"), default="backend")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--hold-phase",
        choices=(
            "fresh",
            "showtip-on",
            "showtip-off",
            "collision",
            "guid-modify",
            "readded",
        ),
        default="fresh",
    )
    parser.add_argument("--hold-seconds", type=int, default=0)
    args = parser.parse_args()
    choices = {case.name: case for case in provider_cases()}
    if args.list:
        print("\n".join(choices))
        return
    if (
        sys.platform != "win32"
        or args.output is None
        or not 0 <= args.hold_seconds <= 300
    ):
        parser.error(
            "Requires Windows, a fresh output directory and hold of 0..300 seconds"
        )
    if any(name not in choices for name in args.case):
        parser.error("Unknown ABI case; use --list")
    selected = (
        tuple(choices.values())
        if args.all
        else tuple(choices[name] for name in args.case)
        if args.case
        else (ProviderCase(),)
    )
    if args.hold_seconds and len(selected) != 1:
        parser.error("Interactive holds require exactly one ABI case")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = select_runtime(args.installed_root, args.wheel)
    from ctypes import wintypes

    native = WindowsDesktop()
    user = native.user
    find = _bind(
        user, "FindWindowW", ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p
    )
    shell = _bind(user, "GetShellWindow", ctypes.c_void_p)()

    def owner(window):
        value = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(value))
        return value.value

    assert shell and owner(find("Shell_TrayWnd", "")) == owner(shell), (
        "Stop the owned Docking host before the independent matrix"
    )
    current = _bind(user, "GetForegroundWindow", ctypes.c_void_p)()
    position = wintypes.POINT()
    assert _bind(user, "GetCursorPos", ctypes.c_int32, ctypes.POINTER(wintypes.POINT))(
        ctypes.byref(position)
    )
    backend = wire = None
    report = {
        "mode": args.mode,
        "runtime": "installed"
        if args.mode == "backend" and runtime.installed
        else "source",
        "evidence": "protocol-only",
        "cases": {},
        "provider_source_sha256": hashlib.sha256(
            Path(__file__).with_name("tray_provider.cs").read_bytes()
        ).hexdigest(),
    }
    binaries = {
        bits: build_provider(output / "build", bits)
        for bits in {case.bits for case in selected}
    }
    try:
        if args.mode == "backend":
            import docking
            from docking.platform.tray.implementations.windows.backend import (
                WindowsTrayBackend,
            )
            from docking.platform.tray.implementations.windows.host import (
                WindowsTrayHost,
            )

            report["module_origins"] = runtime_origins(
                runtime.root,
                {
                    "docking": docking.__file__,
                    WindowsTrayBackend.__module__: sys.modules[
                        WindowsTrayBackend.__module__
                    ].__file__,
                },
            )
            acceptance = runtime.acceptance("api-matrix")
            if acceptance is not None:
                report["acceptance"] = acceptance
            host = WindowsTrayHost()
            wire = OwnedWireTrace(host)
            backend = WindowsTrayBackend(api=host)
            assert backend.get_state().available
        for case in selected:
            try:
                result = run_case(
                    case,
                    binaries[case.bits],
                    output / case.name,
                    backend,
                    wire,
                    args.hold_phase,
                    args.hold_seconds,
                )
                report["cases"][case.name] = {
                    "protocol": result["evidence"]["protocol"],
                    "cleanup_ok": result["cleanup_ok"],
                }
            except Exception as error:
                report["cases"][case.name] = {
                    "protocol": "failed",
                    "error": type(error).__name__,
                }
            write_shared_json(output / "result.json", report)
    finally:
        try:
            if backend is not None:
                backend.close()
        finally:
            if wire is not None:
                wire.close()
            report["routing_restored"] = owner(find("Shell_TrayWnd", "")) == owner(
                shell
            )
            report["cursor_restored"] = bool(
                _bind(user, "SetCursorPos", ctypes.c_int32, ctypes.c_int, ctypes.c_int)(
                    position.x, position.y
                )
            )
            if current:
                report["foreground_restore_accepted"] = bool(
                    _bind(user, "SetForegroundWindow", ctypes.c_int32, ctypes.c_void_p)(
                        current
                    )
                )
            write_shared_json(output / "result.json", report)
    assert report["routing_restored"] and report["cursor_restored"]
    assert all(case["protocol"] == "passed" for case in report["cases"].values())
    assert len(report["cases"]) == len(selected)
    print(
        f"Verified {len(selected)} native tray ABI cases; "
        "physical/visual acceptance separate"
    )


if __name__ == "__main__":
    main()
