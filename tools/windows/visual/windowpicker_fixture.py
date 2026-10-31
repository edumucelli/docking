"""Owned Win32 controls, hosted child windows and failure cases for picker tests."""

from __future__ import annotations

import argparse
import ctypes as c
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protected", action="store_true")
    parser.add_argument("--host", action="store_true")
    parser.add_argument("--parent", type=int)
    parser.add_argument("--app-ids", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned Windows test desktop")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.platform.backends.windows.ffi import bind

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    user, kernel = c.WinDLL("user32", use_last_error=True), c.WinDLL("kernel32")
    pointer, unsigned, signed = c.c_void_p, c.c_uint32, c.c_ssize_t
    callback_type = c.WINFUNCTYPE(signed, pointer, unsigned, c.c_size_t, signed)
    default = bind(
        user, "DefWindowProcW", signed, pointer, unsigned, c.c_size_t, signed
    )
    destroy = bind(user, "DestroyWindow", c.c_int32, pointer)
    quit_loop = bind(user, "PostQuitMessage", None, c.c_int32)
    state = {
        "pid": os.getpid(),
        "windows": {},
        "child_clicks": 0,
        "closed": [],
        "app_ids": {},
    }
    applied = set()

    def set_app_id(window, value):
        from docking.platform.backends.windows.com import apartment, method, release
        from docking.platform.backends.windows.properties import (
            APP_ID,
            PROPERTY_STORE,
            PropertyKey,
            PropertyVariant,
        )

        with apartment():
            store, variant = c.c_void_p(), PropertyVariant()
            try:
                get_store = bind(
                    c.WinDLL("shell32"),
                    "SHGetPropertyStoreForWindow",
                    c.c_int32,
                    pointer,
                    c.POINTER(type(PROPERTY_STORE)),
                    c.POINTER(pointer),
                )
                assert get_store(window, c.byref(PROPERTY_STORE), c.byref(store)) >= 0
                if value is not None:
                    variant.vt = 31
                    # Borrow Python's string storage for SetValue, which copies
                    # it. PropVariantClear must not free this borrowed storage.
                    variant.data.string = value
                assert (
                    method(
                        store,
                        6,
                        c.c_int32,
                        c.POINTER(PropertyKey),
                        c.POINTER(PropertyVariant),
                    )(store, c.byref(APP_ID), c.byref(variant))
                    >= 0
                )
            finally:
                release(store)

    def publish():
        temporary = output / "state.tmp"
        temporary.write_text(json.dumps(state, indent=2))
        # A concurrent Windows/SMB reader can briefly hold a handle which
        # denies replacement. Publish a complete file once that reader closes.
        for _ in range(20):
            try:
                temporary.replace(output / "state.json")
                return
            except PermissionError:
                time.sleep(0.01)
        temporary.replace(output / "state.json")

    def dispatch(window, message, first, second):
        if message == 0x111 and first & 0xFFFF == 42:
            state["child_clicks"] += 1
            publish()
            return 0
        if message == 0x10:
            if window in state["app_ids"]:
                set_app_id(window, None)
            state["closed"].append(window)
            publish()
            destroy(window)
            return 0
        if message == 0x113 and args.app_ids:
            for request in sorted(output.glob("identity-*.request.json")):
                if request.name in applied:
                    continue
                command = json.loads(request.read_text())
                target = state["windows"][command["window"]]
                set_app_id(target, command["app_id"])
                state["app_ids"][target] = command["app_id"]
                if command.get("label"):
                    bind(user, "SetWindowTextW", c.c_int32, pointer, c.c_wchar_p)(
                        state["button"], command["label"]
                    )
                applied.add(request.name)
                publish()
        if message == 2:
            state["windows"] = {
                key: value for key, value in state["windows"].items() if value != window
            }
            publish()
            if not state["windows"]:
                quit_loop(0)
            return 0
        if message == 0x113 and (output / "hang.request").exists():
            bind(user, "KillTimer", c.c_int32, pointer, c.c_size_t)(window, 1)
            state["hung"] = True
            publish()
            while not (output / "resume.request").exists():
                time.sleep(0.1)
            (output / "hang.request").unlink()
            (output / "resume.request").unlink()
            assert bind(
                user, "SetTimer", c.c_size_t, pointer, c.c_size_t, unsigned, pointer
            )(window, 1, 100, None)
            state.update(hung=False, recovered=True)
            publish()
        return default(window, message, first, second)

    callback = callback_type(dispatch)

    class WindowClass(c.Structure):
        _fields_ = [
            ("style", unsigned),
            ("callback", callback_type),
            ("class_extra", c.c_int32),
            ("window_extra", c.c_int32),
            ("instance", pointer),
            ("icon", pointer),
            ("cursor", pointer),
            ("brush", pointer),
            ("menu", c.c_wchar_p),
            ("name", c.c_wchar_p),
        ]

    instance = bind(kernel, "GetModuleHandleW", pointer, c.c_wchar_p)(None)
    name = (
        "Windows.UI.Core.CoreWindow"
        if args.parent
        else "ApplicationFrameWindow"
        if args.host
        else f"Docking.Picker.Fixture.{os.getpid()}"
    )
    cls = WindowClass(instance=instance, callback=callback, name=name, brush=6)
    assert bind(user, "RegisterClassW", c.c_uint16, c.POINTER(WindowClass))(
        c.byref(cls)
    )
    create = bind(
        user,
        "CreateWindowExW",
        pointer,
        unsigned,
        c.c_wchar_p,
        c.c_wchar_p,
        unsigned,
        c.c_int32,
        c.c_int32,
        c.c_int32,
        c.c_int32,
        pointer,
        pointer,
        pointer,
        pointer,
    )
    if args.parent:
        target = create(
            0,
            name,
            "Owned hosted application",
            0x50000000,
            10,
            10,
            540,
            240,
            args.parent,
            None,
            instance,
            None,
        )
        state["windows"]["CoreWindow"] = target
    else:
        for key, x, y, width, height in (
            ("Target", 100, 80, 600, 360),
            ("Sibling", 750, 100, 300, 220),
        ):
            window = create(
                0x40000,
                name,
                "Docking picker fixture " + key,
                0x10CF0000,
                x,
                y,
                width,
                height,
                None,
                None,
                instance,
                None,
            )
            assert window
            state["windows"][key] = window
        target = state["windows"]["Target"]
    assert target
    button = create(
        0,
        "BUTTON",
        "Owned child control",
        0x50000000,
        30,
        60,
        200,
        80,
        target,
        42,
        instance,
        None,
    )
    assert button
    state.update(button=button, parent=args.parent, protected=args.protected)
    if args.app_ids:
        for label, window in state["windows"].items():
            value = "Docking.Fixture." + label
            set_app_id(window, value)
            state["app_ids"][window] = value
    if args.protected:
        descriptor = pointer()
        advapi = c.WinDLL("advapi32", use_last_error=True)
        assert bind(
            advapi,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            c.c_int32,
            c.c_wchar_p,
            unsigned,
            c.POINTER(pointer),
            c.POINTER(unsigned),
        )("D:(D;;0x1;;;WD)(A;;0x1fffff;;;OW)", 1, c.byref(descriptor), None)
        try:
            process = bind(kernel, "GetCurrentProcess", pointer)()
            assert bind(
                advapi, "SetKernelObjectSecurity", c.c_int32, pointer, unsigned, pointer
            )(process, 4, descriptor)
        finally:
            bind(kernel, "LocalFree", pointer, pointer)(descriptor)
    assert bind(user, "SetTimer", c.c_size_t, pointer, c.c_size_t, unsigned, pointer)(
        target, 1, 100, None
    )
    publish()
    message = wintypes.MSG()
    get = bind(
        user,
        "GetMessageW",
        c.c_int32,
        c.POINTER(wintypes.MSG),
        pointer,
        unsigned,
        unsigned,
    )
    translate = bind(user, "TranslateMessage", c.c_int32, c.POINTER(wintypes.MSG))
    deliver = bind(user, "DispatchMessageW", signed, c.POINTER(wintypes.MSG))
    while (result := get(c.byref(message), None, 0, 0)) > 0:
        translate(c.byref(message))
        deliver(c.byref(message))
    assert result == 0
    state["normal_exit"] = True
    publish()
    print(json.dumps(state))


if __name__ == "__main__":
    main()
