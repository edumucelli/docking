"""Own two real Shell_NotifyIcon clients for Windows tray acceptance.

The legacy and GUID/version-4 icons contain generated pixels. A native window
and native context menu record activation independently of Docking. Write a
revisioned command.json with action modify/hide/show/remove/add/quit and which
legacy/modern. The lifetime is bounded and normal exit deletes both icons.
"""

from __future__ import annotations

import argparse
import ctypes as c
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path


def decode_callback(first: int, second: int, versions: dict[str, int]):
    """Decode only identities established by our actual provider registrations."""
    identities = {500: "legacy", 501: "modern"}
    packed_uid = (second >> 16) & 0xFFFF
    packed = identities.get(packed_uid)
    if packed is not None and versions[packed] == 4:
        return packed, packed_uid, second & 0xFFFF, 4
    uid = first & 0xFFFFFFFF
    which = identities.get(uid)
    if which is not None and versions[which] != 4 and 0 <= second <= 0xFFFF:
        return which, uid, second & 0xFFFFFFFF, versions[which]
    return None, uid, second & 0xFFFFFFFF, None


def rich_popup_position(icon, work, width=230, height=72):
    """Keep owned hover UI on its monitor without covering its trigger icon."""
    left, top, right, bottom = icon
    for x, y in (
        (left, top - height - 4),
        (left, bottom + 4),
        (right + 4, top),
        (left - width - 4, top),
    ):
        x = max(work[0], min(x, work[2] - width))
        y = max(work[1], min(y, work[3] - height))
        if (
            x + width <= work[2]
            and y + height <= work[3]
            and (x + width <= left or x >= right or y + height <= top or y >= bottom)
        ):
            return x, y
    raise ValueError("No unobstructed rich popup space")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--use-icon-rect", action="store_true")
    parser.add_argument("--legacy-version", type=int, choices=(0, 3, 4), default=0)
    parser.add_argument("--modern-version", type=int, choices=(0, 3, 4), default=4)
    args = parser.parse_args()
    if sys.platform != "win32" or not 0 < args.seconds <= 600:
        parser.error("Requires Windows and a lifetime of at most 600 seconds")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from ctypes import wintypes

    from PIL import Image, ImageDraw

    from docking.platform.backends.windows.com import GUID
    from docking.platform.backends.windows.ffi import bind

    p, u, w, result_type = c.c_void_p, c.c_uint32, c.c_size_t, c.c_ssize_t
    user = c.WinDLL("user32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    shell = c.WinDLL("shell32", use_last_error=True)
    callback_type = c.WINFUNCTYPE(result_type, p, u, w, result_type)

    class WindowClass(c.Structure):
        _fields_ = [
            ("size", u),
            ("style", u),
            ("callback", callback_type),
            ("class_extra", c.c_int),
            ("window_extra", c.c_int),
            ("instance", p),
            ("icon", p),
            ("cursor", p),
            ("background", p),
            ("menu", c.c_wchar_p),
            ("name", c.c_wchar_p),
            ("small", p),
        ]

    class IconData(c.Structure):
        _fields_ = [
            ("size", u),
            ("window", p),
            ("uid", u),
            ("flags", u),
            ("message", u),
            ("icon", p),
            ("tip", c.c_wchar * 128),
            ("state", u),
            ("mask", u),
            ("info", c.c_wchar * 256),
            ("version", u),
            ("title", c.c_wchar * 64),
            ("info_flags", u),
            ("guid", GUID),
            ("balloon", p),
        ]

    class IconIdentifier(c.Structure):
        _fields_ = [("size", u), ("window", p), ("uid", u), ("guid", GUID)]

    query_rect = bind(
        shell,
        "Shell_NotifyIconGetRect",
        c.c_int32,
        c.POINTER(IconIdentifier),
        c.POINTER(wintypes.RECT),
    )

    def bounds(which):
        data = icons[which]
        identifier = IconIdentifier(
            size=c.sizeof(IconIdentifier),
            window=data.window,
            uid=data.uid,
            guid=data.guid,
        )
        rect = wintypes.RECT()
        status = query_rect(c.byref(identifier), c.byref(rect))
        return {
            "status": status,
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
        }

    report = {
        "pid": os.getpid(),
        "stage": "creating",
        "icons": {},
        "callbacks": [],
        "callback_sequence": 0,
        "focus_returns": [],
        "popups": {},
        "activations": 0,
        "menu_actions": 0,
        "menus": 0,
        "command_revision": 0,
    }
    handles, icons, popups = [], {}, {}
    versions = {"legacy": args.legacy_version, "modern": args.modern_version}
    default = bind(user, "DefWindowProcW", result_type, p, u, w, result_type)
    notify = bind(shell, "Shell_NotifyIconW", c.c_int32, u, c.POINTER(IconData))
    taskbar = bind(user, "RegisterWindowMessageW", u, c.c_wchar_p)("TaskbarCreated")
    hwnd = None
    deadline = time.monotonic() + args.seconds

    def publish():
        temporary = output / "state.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(output / "state.json")
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)

    def load_icon(name, color):
        image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, 29, 29), radius=5, fill=(*color, 255))
        draw.rectangle((14, 5, 18, 26), fill=(*color, 128))
        target = output / (name + ".ico")
        image.save(target, sizes=[(32, 32)])
        handle = bind(user, "LoadImageW", p, p, c.c_wchar_p, u, c.c_int, c.c_int, u)(
            None, str(target), 1, 32, 32, 0x10
        )
        if not handle:
            raise c.WinError(c.get_last_error())
        handles.append(handle)
        return handle

    def add(which):
        data = icons[which]
        data.flags = 7 | (0x20 if which == "modern" else 0)
        accepted = bool(notify(0, c.byref(data)))
        report["icons"][which]["last_add_accepted"] = accepted
        # Existing Explorer icons can already exist after a host handoff.
        if not accepted:
            accepted = bool(notify(1, c.byref(data)))
        data.version = versions[which]
        version_accepted = bool(notify(4, c.byref(data)))
        report["icons"][which]["version_accepted"] = version_accepted
        # An unsupported explicit zero still leaves the SDK's default legacy
        # protocol. Versions 3/4 must be negotiated successfully.
        accepted = (version_accepted or versions[which] == 0) and accepted
        report["icons"][which]["registered"] = accepted
        report["icons"][which]["adds"] = report["icons"][which].get("adds", 0) + 1
        return accepted

    def return_focus(which):
        data = icons[which]
        identity = IconData(
            size=c.sizeof(IconData),
            window=data.window,
            uid=data.uid,
            flags=0x20 if which == "modern" else 0,
            guid=data.guid,
        )
        foreground = bind(user, "GetForegroundWindow", p)
        before = foreground()
        accepted = bool(notify(3, c.byref(identity)))
        report["focus_returns"].append(
            {
                "which": which,
                "accepted": accepted,
                "before": before,
                "after": foreground(),
                "window": data.window,
                "uid": data.uid,
            }
        )
        publish()

    def rich_popup(which, visible):
        window = popups.get(which)
        if visible:
            geometry = bounds(which)
            assert geometry["status"] == 0

            class MonitorInfo(c.Structure):
                _fields_ = [
                    ("size", u),
                    ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT),
                    ("flags", u),
                ]

            rect = wintypes.RECT(*geometry["rect"])
            monitor = bind(user, "MonitorFromRect", p, c.POINTER(wintypes.RECT), u)(
                c.byref(rect),
                2,
            )
            info = MonitorInfo(size=c.sizeof(MonitorInfo))
            assert bind(user, "GetMonitorInfoW", c.c_int32, p, c.POINTER(MonitorInfo))(
                monitor,
                c.byref(info),
            )
            x, y = rich_popup_position(
                geometry["rect"],
                (info.work.left, info.work.top, info.work.right, info.work.bottom),
            )
            if window is None:
                window = bind(
                    user,
                    "CreateWindowExW",
                    p,
                    u,
                    c.c_wchar_p,
                    c.c_wchar_p,
                    u,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                    p,
                    p,
                    p,
                    p,
                )(
                    0x08000088,  # NOACTIVATE | TOOLWINDOW | TOPMOST.
                    "STATIC",
                    "Owned rich tray popup " + which,
                    0x80800001,
                    x,
                    y,
                    230,
                    72,
                    hwnd,
                    None,
                    None,
                    None,
                )
                assert window
                popups[which] = window
            assert bind(
                user,
                "SetWindowPos",
                c.c_int32,
                p,
                p,
                c.c_int,
                c.c_int,
                c.c_int,
                c.c_int,
                u,
            )(
                window,
                c.c_void_p(-1),  # HWND_TOPMOST, above the host's popup surface.
                x,
                y,
                0,
                0,
                0x11,  # NOSIZE | NOACTIVATE; update z-order deliberately.
            )
            before = bind(user, "GetForegroundWindow", p)()
            bind(user, "ShowWindow", c.c_int32, p, c.c_int)(window, 4)
            assert bind(user, "UpdateWindow", c.c_int32, p)(window)
            report["popups"][which] = {
                "window": window,
                "visible": True,
                "foreground_unchanged": bind(user, "GetForegroundWindow", p)()
                == before,
            }
        elif window is not None:
            bind(user, "ShowWindow", c.c_int32, p, c.c_int)(window, 0)
            report["popups"][which]["visible"] = False

    def context(which, x, y):
        if args.use_icon_rect:
            geometry = bounds(which)
            report["menu_icon_bounds"] = geometry
            assert geometry["status"] == 0
            x, y = geometry["rect"][0], geometry["rect"][3]
            report["menu_requested_position"] = [x, y]
        menu = bind(user, "CreatePopupMenu", p)()
        if not menu:
            raise c.WinError(c.get_last_error())
        try:
            append = bind(user, "AppendMenuW", c.c_int32, p, u, w, c.c_wchar_p)
            assert append(menu, 0, 1001, "Record tray action")
            assert append(menu, 0, 1002, "Hide fixture window")
            report["menus"] += 1
            report["menu_for"] = which
            publish()
            report["menu_set_foreground"] = bool(
                bind(user, "SetForegroundWindow", c.c_int32, p)(hwnd)
            )
            report["menu_foreground"] = bind(user, "GetForegroundWindow", p)()
            publish()
            selected = bind(user, "TrackPopupMenuEx", u, p, u, c.c_int, c.c_int, p, p)(
                menu, 0x102, x, y, hwnd, None
            )
            report["menu_selected"] = selected
            if selected == 1001:
                report["menu_actions"] += 1
            elif selected == 1002:
                bind(user, "ShowWindow", c.c_int32, p, c.c_int)(hwnd, 0)
            bind(user, "PostMessageW", c.c_int32, p, u, w, result_type)(hwnd, 0, 0, 0)
        finally:
            bind(user, "DestroyMenu", c.c_int32, p)(menu)
        if report["icons"][which]["defer_focus"]:
            report["focus_deferred"] = which
        else:
            return_focus(which)
        publish()

    def command(value):
        revision = value.get("revision", 0)
        if type(revision) is not int or revision <= report["command_revision"]:
            return
        action, which = value.get("action"), value.get("which", "modern")
        if action == "quit":
            bind(user, "PostQuitMessage", None, c.c_int)(0)
        elif action == "rects":
            report["icon_rects"] = {key: bounds(key) for key in icons}
        elif which in icons:
            data = icons[which]
            row = report["icons"][which]
            guid_only = action.startswith("guid-")
            if guid_only:
                assert which == "modern"
                action = action.removeprefix("guid-")

            def request(operation):
                if not guid_only:
                    return bool(notify(operation, c.byref(data)))
                # A GUID identifies an existing icon independently of hWnd/uID.
                value = IconData(
                    size=c.sizeof(IconData),
                    flags=data.flags | 0x20,
                    icon=data.icon,
                    tip=data.tip,
                    state=data.state,
                    mask=data.mask,
                    guid=data.guid,
                )
                accepted = bool(notify(operation, c.byref(value)))
                report["guid_only_request"] = {
                    "operation": operation,
                    "window": 0,
                    "uid": 0,
                    "accepted": accepted,
                }
                return accepted

            if action == "modify":
                old = data.icon
                color = (32, 190, 96) if which == "modern" else (190, 48, 150)
                data.icon = load_icon(which + "-updated-" + str(revision), color)
                data.flags = (6 if guid_only else 7) | (
                    0x20 if which == "modern" else 0
                )
                suffix = f" GUID {revision}" if guid_only else ""
                data.tip = "Docking tray fixture " + which + suffix + " updated"
                assert request(1)
                bind(user, "DestroyIcon", c.c_int32, p)(old)
                handles.remove(old)
                row.update(title=data.tip, color=list(color))
            elif action in ("showtip-on", "showtip-off"):
                data.flags = (0x80 if action == "showtip-on" else 0) | (
                    0x20 if which == "modern" else 0
                )
                assert request(1)
                row["show_tip"] = action == "showtip-on"
            elif action in ("focus-defer-on", "focus-defer-off"):
                row["defer_focus"] = action == "focus-defer-on"
            elif action == "focus":
                return_focus(which)
            elif action in ("balloon", "balloon-clear"):
                data.flags = 0x10 | (0x20 if which == "modern" else 0)
                data.info = "Owned tray notification" if action == "balloon" else ""
                data.title, data.info_flags = "Owned tray fixture", 0x91
                assert request(1)
            elif action in ("hide", "show"):
                data.flags = 8 | (0x20 if which == "modern" else 0)
                data.state = 1 if action == "hide" else 0
                data.mask = 1
                assert request(1)
                row["hidden"] = action == "hide"
            elif action == "remove":
                data.flags = 0x20 if which == "modern" else 0
                assert request(2)
                row["registered"] = False
                rich_popup(which, False)
            elif action == "add":
                assert add(which)
            else:
                raise ValueError("Unknown fixture command")
        else:
            raise ValueError("Unknown fixture icon")
        report["command_revision"] = revision
        publish()

    def procedure(window, message, first, second):
        try:
            if message == 0x8001:
                which, uid, event, version = decode_callback(first, second, versions)
                report["callback_sequence"] += 1
                report["callbacks"] = (
                    report["callbacks"]
                    + [
                        {
                            "which": which,
                            "sequence": report["callback_sequence"],
                            "window": window,
                            "version": version,
                            "uid": uid,
                            "event": event,
                            "first": first,
                            "second": second,
                        }
                    ]
                )[-256:]
                if which is None:
                    publish()
                    return 0
                if event == 0x406:
                    rich_popup(which, True)
                elif event == 0x407:
                    rich_popup(which, False)
                elif event in (0x202, 0x203, 0x400, 0x401):
                    if event == 0x203:
                        report["double_clicks"] = report.get("double_clicks", 0) + 1
                    report["activations"] += 1
                    report["activated"] = which
                    bind(user, "SetWindowTextW", c.c_int32, p, c.c_wchar_p)(
                        window, "Docking Tray Fixture - " + which
                    )
                    bind(user, "ShowWindow", c.c_int32, p, c.c_int)(window, 5)
                    bind(user, "SetForegroundWindow", c.c_int32, p)(window)
                    report["foreground"] = (
                        bind(user, "GetForegroundWindow", p)() == window
                    )
                elif event in (0x205, 0x7B):
                    point = wintypes.POINT()
                    bind(user, "GetCursorPos", c.c_int32, c.POINTER(wintypes.POINT))(
                        c.byref(point)
                    )
                    x = c.c_int16(first & 0xFFFF).value if version == 4 else point.x
                    y = (
                        c.c_int16((first >> 16) & 0xFFFF).value
                        if version == 4
                        else point.y
                    )
                    context(which, x, y)
                publish()
                return 0
            if message == taskbar:
                for which, row in report["icons"].items():
                    if row["registered"]:
                        add(which)
                publish()
                return 0
            if message == 0x10:
                bind(user, "ShowWindow", c.c_int32, p, c.c_int)(window, 0)
                return 0
            if message == 0x113:
                if time.monotonic() >= deadline or (output / "stop").exists():
                    bind(user, "PostQuitMessage", None, c.c_int)(0)
                else:
                    target = (
                        output / f"command-{report['command_revision'] + 1:06d}.json"
                    )
                    if not target.exists():
                        target = output / "command.json"  # Earlier lab drivers.
                    if target.exists():
                        try:
                            value = json.loads(target.read_text())
                        except (
                            PermissionError,
                            FileNotFoundError,
                            json.JSONDecodeError,
                        ):
                            # A concurrent SMB rename can briefly deny a read.
                            # The revision remains pending for the next tick.
                            return 0
                        command(value)
                return 0
        except Exception as exc:
            report["error"] = repr(exc)
            report["error_traceback"] = traceback.format_exc()
            publish()
            bind(user, "PostQuitMessage", None, c.c_int)(1)
        return default(window, message, first, second)

    callback = callback_type(procedure)
    instance = bind(kernel, "GetModuleHandleW", p, c.c_wchar_p)(None)
    name = "Docking.TrayFixture." + str(os.getpid()) + "." + uuid.uuid4().hex
    descriptor = WindowClass(
        size=c.sizeof(WindowClass),
        style=8,
        callback=callback,
        instance=instance,
        background=6,
        name=name,
    )
    assert bind(user, "RegisterClassExW", c.c_uint16, c.POINTER(WindowClass))(
        c.byref(descriptor)
    )
    try:
        hwnd = bind(
            user,
            "CreateWindowExW",
            p,
            u,
            c.c_wchar_p,
            c.c_wchar_p,
            u,
            c.c_int,
            c.c_int,
            c.c_int,
            c.c_int,
            p,
            p,
            p,
            p,
        )(
            0,
            name,
            "Docking Tray Fixture",
            0x00CF0000,
            200,
            150,
            420,
            240,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:
            raise c.WinError(c.get_last_error())
        report["window"] = hwnd
        for which, uid, color in [
            ("legacy", 500, (48, 112, 220)),
            ("modern", 501, (240, 150, 32)),
        ]:
            guid = str(uuid.uuid4()) if which == "modern" else ""
            icon = load_icon(which, color)
            title = "Docking tray fixture " + which
            icons[which] = IconData(
                size=c.sizeof(IconData),
                window=hwnd,
                uid=uid,
                flags=7 | (0x20 if guid else 0),
                message=0x8001,
                icon=icon,
                tip=title,
                guid=GUID.parse(guid or "00000000-0000-0000-0000-000000000000"),
            )
            report["icons"][which] = {
                "uid": uid,
                "guid": guid,
                "title": title,
                "color": list(color),
                "registered": False,
                "hidden": False,
                "version": versions[which],
                "show_tip": False,
                "defer_focus": False,
            }
            assert add(which)
        assert bind(user, "SetTimer", w, p, w, u, p)(hwnd, 1, 100, None)
        report["stage"] = "ready"
        publish()
        message = wintypes.MSG()
        while (
            bind(user, "GetMessageW", c.c_int32, c.POINTER(wintypes.MSG), p, u, u)(
                c.byref(message), None, 0, 0
            )
            > 0
        ):
            bind(user, "TranslateMessage", c.c_int32, c.POINTER(wintypes.MSG))(
                c.byref(message)
            )
            bind(user, "DispatchMessageW", result_type, c.POINTER(wintypes.MSG))(
                c.byref(message)
            )
    finally:
        for window in popups.values():
            bind(user, "DestroyWindow", c.c_int32, p)(window)
        for which, data in icons.items():
            data.flags = 0x20 if which == "modern" else 0
            notify(2, c.byref(data))
            report["icons"][which]["registered"] = False
        for icon in handles:
            bind(user, "DestroyIcon", c.c_int32, p)(icon)
        if hwnd:
            bind(user, "DestroyWindow", c.c_int32, p)(hwnd)
        bind(user, "UnregisterClassW", c.c_int32, c.c_wchar_p, p)(name, instance)
        report["stage"] = "closed"
        publish()
    if report.get("error"):
        raise RuntimeError(report["error"])
    print(json.dumps(report))


if __name__ == "__main__":
    main()
