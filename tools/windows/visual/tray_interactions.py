"""Actual GTK/native tray interaction acceptance, before GUID-only mutations.

Setup may select a GTK focus target; every action is real desktop input and
every provider callback comes from its independent native message pump.
"""

from __future__ import annotations

import ctypes as c
import os
import time
import uuid
import zlib
from ctypes import wintypes

from tools.windows.visual.tray_provider import (
    NativeProvider,
    ProviderCase,
    build_provider,
)


def keyboard_focus_ready(snapshot, pid):
    """Logical widget selection alone cannot prove delivery of real keys."""
    return bool(
        snapshot["popup_window"]
        and snapshot["popup_visible"]
        and snapshot["native_query_ok"]
        and snapshot["target_focus"]
        and snapshot["logical_selection"]
        and snapshot["foreground_pid"] == snapshot["native_focus_pid"] == pid
        and snapshot["foreground"]
        == snapshot["native_focus_root"]
        == snapshot["popup_window"]
    )


def visible_control_point(window, bounds, root_at):
    """Pick an actually exposed caption/body point, never a guessed center."""
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        raise AssertionError("Owned foreground control has no usable rectangle")
    candidates = (
        (left + 48, top + 12),  # Caption text, away from the system-menu icon.
        (left + 24, top + 12),
        ((left + right) // 2, (top + bottom) // 2),
        (left + 12, (top + bottom) // 2),
        (right - 12, (top + bottom) // 2),
        ((left + right) // 2, bottom - 12),
    )
    for point in candidates:
        if (
            left < point[0] < right
            and top < point[1] < bottom
            and root_at(*point) == window
        ):
            return point
    raise AssertionError("Owned foreground control has no unobscured input point")


def verify_interactions(
    *,
    native,
    call,
    applet,
    backend,
    ids,
    fixture_state,
    command,
    popup,
    button_point,
    capture,
    report,
    output,
    notification_point=None,
):
    from gi.repository import Gdk, Gtk
    from PIL import ImageGrab

    from docking.platform.backends.windows.ffi import bind, window_handle
    from docking.platform.tray.windows_messages import decode_message

    user = native.user
    move = bind(user, "SetCursorPos", c.c_int32, c.c_int, c.c_int)
    foreground = user.GetForegroundWindow
    visible = user.IsWindowVisible
    evidence = report["interactions"] = {
        "physical_input": "running",
        "visual": "captured-not-reviewed",
        "hover": [],
        "keyboard": [],
        "focus_routes": [],
        "balloons": "not-run",
        "keyboard_setup": [],
        "rich_windows": [],
    }

    def owner(window):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, c.byref(pid))
        return pid.value

    class ThreadInfo(c.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("active", wintypes.HWND),
            ("focus", wintypes.HWND),
            ("capture", wintypes.HWND),
            ("menu_owner", wintypes.HWND),
            ("move_size", wintypes.HWND),
            ("caret", wintypes.HWND),
            ("caret_rect", wintypes.RECT),
        ]

    query_focus = bind(
        user, "GetGUIThreadInfo", c.c_int32, wintypes.DWORD, c.POINTER(ThreadInfo)
    )

    def focus_state(which, label=None):
        def gtk_state():
            window = applet._popup
            icon = applet._row_targets[ids[which]]
            target = (
                icon
                if label is None
                else next(
                    child
                    for child in icon.get_parent().get_children()
                    if isinstance(child, Gtk.Button) and child.get_label() == label
                )
            )
            return {
                "popup_window": window_handle(window.get_window()),
                "popup_visible": window.get_visible(),
                "popup_active": window.is_active(),
                "logical_selection": target.is_focus(),
                "target_focus": target.has_focus(),
                "row_focus": icon.has_focus(),
            }

        info = ThreadInfo(size=c.sizeof(ThreadInfo))
        queried = bool(query_focus(0, c.byref(info)))
        return {
            **call(gtk_state),
            "native_query_ok": queried,
            "foreground": foreground(),
            "foreground_pid": owner(foreground()),
            "native_focus": info.focus,
            "native_focus_pid": owner(info.focus),
            "native_focus_root": user.GetAncestor(info.focus, 2) if info.focus else 0,
        }

    def callbacks(which, after):
        return [
            row
            for row in fixture_state()["callbacks"]
            if row["sequence"] > after and row["which"] == which
        ]

    def sequence():
        return fixture_state()["callback_sequence"]

    def leave():
        assert move(10, 10)
        time.sleep(0.3)

    def rich_visible(which):
        row = fixture_state()["popups"].get(which)
        return bool(row and row["visible"] and visible(row["window"]))

    desktop = bind(user, "GetDesktopWindow", c.c_void_p)()
    hit_child = bind(
        user,
        "ChildWindowFromPointEx",
        c.c_void_p,
        c.c_void_p,
        wintypes.POINT,
        c.c_uint32,
    )
    to_client = bind(
        user, "ScreenToClient", c.c_int32, c.c_void_p, c.POINTER(wintypes.POINT)
    )

    def rich_painted(which, observation):
        row = fixture_state()["popups"].get(which)
        if not row or not row["visible"] or not visible(row["window"]):
            return False
        rect = wintypes.RECT()
        assert user.GetWindowRect(row["window"], c.byref(rect))
        bounds = (rect.left, rect.top, rect.right, rect.bottom)
        center = wintypes.POINT(
            (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        )
        assert to_client(desktop, c.byref(center))
        # WindowFromPoint intentionally skips STATIC text controls. This native
        # z-order query includes them while skipping hidden/transparent windows.
        hit = hit_child(desktop, center, 7)
        observation.update(
            window=row["window"],
            rect=list(bounds),
            visible=True,
            center_hit=hit,
            foreground_unchanged=row["foreground_unchanged"],
        )
        if hit != row["window"] or rect.right <= rect.left or rect.bottom <= rect.top:
            return False
        pixels = ImageGrab.grab(bbox=bounds, all_screens=True).convert("RGB")
        checksum = zlib.crc32(pixels.tobytes())
        stable = observation.get("screen_crc32") == checksum
        contrast = max(high - low for low, high in pixels.getextrema()) >= 32
        observation.update(
            screen_crc32=checksum, paint_stable=stable, contrast=contrast
        )
        return stable and contrast

    # Hover alone never creates a focus grant. A flag-only modification uses
    # the ORIGINAL nonzero HWND, so this phase cannot change callback identity.
    for which in ids:
        version = fixture_state()["icons"][which]["version"]
        for showtip in (False, True, False):
            popup()
            leave()
            command("showtip-on" if showtip else "showtip-off", which)
            standard = version != 4 or showtip
            native.wait_for(
                lambda which=which, standard=standard: call(
                    lambda: (
                        applet._row_targets[ids[which]].get_has_tooltip() == standard
                    )
                ),
                "Flag-only tooltip policy did not reach the native icon target",
            )
            before, start = foreground(), sequence()
            assert move(
                *map(int, call(lambda which=which: button_point(ids[which], None)))
            )
            native.wait_for(
                lambda which=which, start=start: any(
                    row["event"] == 0x200 for row in callbacks(which, start)
                ),
                "Real pointer motion did not reach the provider",
            )
            if standard:
                native.wait_for(
                    lambda: call(
                        lambda: any(
                            window.get_visible()
                            and window.get_type_hint() == Gdk.WindowTypeHint.TOOLTIP
                            for window in Gtk.Window.list_toplevels()
                        )
                    ),
                    "Standard GTK tray tooltip did not map",
                )
                assert not rich_visible(which)
            else:
                painted = {"which": which, "showtip": showtip}
                evidence["rich_windows"].append(painted)
                native.wait_for(
                    lambda which=which, painted=painted: rich_painted(which, painted),
                    "Rich native popup did not become unobstructed and painted",
                )
                assert any(row["event"] == 0x406 for row in callbacks(which, start))
            if version == 4:
                point = wintypes.POINT()
                assert bind(user, "GetCursorPos", c.c_int32, c.POINTER(wintypes.POINT))(
                    c.byref(point)
                )
                motion = next(
                    row for row in callbacks(which, start) if row["event"] == 0x200
                )
                assert (
                    c.c_int16(motion["first"] & 0xFFFF).value,
                    c.c_int16((motion["first"] >> 16) & 0xFFFF).value,
                ) == (point.x, point.y)
            assert foreground() == before, (
                "Passive tray hover changed native foreground"
            )
            capture(f"interaction-{which}-showtip-{int(showtip)}-{start}")
            leave()
            if not standard:
                position = wintypes.POINT()
                assert bind(user, "GetCursorPos", c.c_int32, c.POINTER(wintypes.POINT))(
                    c.byref(position)
                )
                painted["leave_pointer"] = [position.x, position.y]
                assert painted["leave_pointer"] == [10, 10]
                native.wait_for(
                    lambda which=which: not rich_visible(which),
                    "Rich popup did not close on leave",
                )
                assert any(row["event"] == 0x407 for row in callbacks(which, start))
            evidence["hover"].append(
                {
                    "which": which,
                    "version": version,
                    "showtip": showtip,
                    "foreground_unchanged": foreground() == before,
                    "callbacks": callbacks(which, start),
                }
            )

    # Observe real host dispatch/forwarding, without replacing any policy or
    # injecting protocol messages. Dispatch and forwarding run on one host thread.
    host = backend._api
    dispatch, forward = host._dispatch_notification, host._forward
    forwarded = 0
    owned_windows = {fixture_state()["window"] & 0xFFFFFFFF}

    def trace_forward(window, message, first, second, *, handled=False):
        nonlocal forwarded
        forwarded += 1
        return forward(window, message, first, second, handled=handled)

    def trace_dispatch(window, first, second, payload):
        decoded = decode_message(payload)
        before = forwarded
        result = dispatch(window, first, second, payload)
        if (
            decoded is not None
            and decoded.operation == 3
            and decoded.window in owned_windows
        ):
            evidence["focus_routes"].append(
                {
                    "window": decoded.window,
                    "uid": decoded.uid,
                    "forwarded": forwarded > before,
                    "result": result,
                }
            )
        return result

    host._forward, host._dispatch_notification = trace_forward, trace_dispatch

    def await_route(after, window, *, expected_forward):
        route = native.wait_for(
            lambda: next(
                (
                    row
                    for row in evidence["focus_routes"][after:]
                    if row["window"] == window
                ),
                None,
            ),
            "Real NIM_SETFOCUS did not reach the host",
        )
        assert route["forwarded"] is expected_forward, route
        return route

    def keyboard_target(which, label=None):
        popup()

        def label_point():
            labels = applet._row_targets[ids[which]].get_parent().get_children()[1]
            window = applet._popup
            x, y = window.get_window().get_origin()[-2:]
            offset = labels.translate_coordinates(
                window,
                labels.get_allocated_width() // 2,
                labels.get_allocated_height() // 2,
            )
            assert offset is not None
            return x + offset[0], y + offset[1]

        # Select a passive title with real input; production owns native popup
        # activation. Never force SetForegroundWindow to conceal a product bug.
        native.click(*call(label_point))

        def select():
            if label is None:
                target = applet._row_targets[ids[which]]
            else:
                row = applet._row_targets[ids[which]].get_parent()
                target = next(
                    child
                    for child in row.get_children()
                    if isinstance(child, Gtk.Button) and child.get_label() == label
                )
            target.grab_focus()

        call(select)
        record = {"which": which, "target": label or "icon"}
        evidence["keyboard_setup"].append(record)

        def focused():
            record["observed"] = focus_state(which, label)
            return keyboard_focus_ready(record["observed"], os.getpid())

        native.wait_for(
            focused, "Tray target did not acquire native and GTK keyboard focus"
        )

    def menu_escape(which, *, keyboard=False):
        popup()
        before = fixture_state()["menus"]
        if keyboard:
            keyboard_target(which)
            native.key(0x79, shift=True)
        else:
            native.click(*call(lambda: button_point(ids[which], "Menu")))
        native.wait_for(
            lambda: (
                fixture_state()["menus"] > before
                and any(
                    row["class"] == "#32768"
                    for row in native.windows(fixture_state()["pid"])
                )
            ),
            "Real provider menu did not map",
        )
        capture("interaction-" + which + "-menu")
        native.key(0x1B)
        native.wait_for(
            lambda: (
                not any(
                    row["class"] == "#32768"
                    for row in native.windows(fixture_state()["pid"])
                )
            ),
            "Provider menu did not close on Escape",
        )

    try:
        for which in ids:
            version = fixture_state()["icons"][which]["version"]
            for label, key in ((None, 0x0D), ("Open", 0x20)):
                keyboard_target(which, label)
                command("rects")
                bounds = fixture_state()["icon_rects"][which]
                assert bounds["status"] == 0
                before = sequence()
                return_after = len(evidence["focus_routes"])
                native.key(key)
                expected = 0x401 if version in (3, 4) else 0x205
                native.wait_for(
                    lambda which=which, before=before, expected=expected: any(
                        row["event"] == expected for row in callbacks(which, before)
                    ),
                    "Real keyboard activation did not preserve its origin",
                )
                activation = next(
                    row for row in callbacks(which, before) if row["event"] == expected
                )
                assert activation["uid"] == fixture_state()["icons"][which]["uid"]
                assert activation["window"] == fixture_state()["window"]
                if version == 4:
                    assert (
                        c.c_int16(activation["first"] & 0xFFFF).value,
                        c.c_int16((activation["first"] >> 16) & 0xFFFF).value,
                    ) == tuple(bounds["rect"][:2])
                native.wait_for(
                    lambda: foreground() == fixture_state()["window"],
                    "Keyboard activation did not focus the native provider",
                )
                if version == 0:
                    # Legacy keyboard activation is right-down/up, as defined
                    # by Shell_NotifyIcon; this provider opens its real menu.
                    native.wait_for(
                        lambda: any(
                            row["class"] == "#32768"
                            for row in native.windows(fixture_state()["pid"])
                        ),
                        "Legacy keyboard activation did not open its native menu",
                    )
                    native.key(0x1B)
                    native.wait_for(
                        lambda: (
                            not any(
                                row["class"] == "#32768"
                                for row in native.windows(fixture_state()["pid"])
                            )
                        ),
                        "Legacy keyboard menu did not close",
                    )
                    # TrackPopupMenu can disappear before the provider sends
                    # NIM_SETFOCUS and GTK handles the native return. Drain that
                    # actual return before another physical action can race it.
                    await_route(
                        return_after, fixture_state()["window"], expected_forward=False
                    )
                    native.wait_for(
                        lambda which=which: keyboard_focus_ready(
                            focus_state(which), os.getpid()
                        ),
                        "Legacy keyboard Escape did not return "
                        "native and GTK row focus",
                    )
                evidence["keyboard"].append(
                    {
                        "which": which,
                        "target": label or "icon",
                        "version": version,
                        "callbacks": callbacks(which, before),
                        "foreground": foreground(),
                        **(
                            {"menu_focus_return": focus_state(which)}
                            if version == 0
                            else {}
                        ),
                    }
                )
            after = len(evidence["focus_routes"])
            menu_escape(which, keyboard=True)
            await_route(after, fixture_state()["window"], expected_forward=False)
            native.wait_for(
                lambda which=which: keyboard_focus_ready(
                    focus_state(which), os.getpid()
                ),
                "Menu Escape did not return native and GTK focus to its tray row",
            )
            evidence.setdefault("focus_return", {})[which] = focus_state(which)
            capture("interaction-" + which + "-returned-focus")
            before, after = foreground(), len(evidence["focus_routes"])
            command("focus", which)
            await_route(after, fixture_state()["window"], expected_forward=False)
            time.sleep(0.3)
            assert foreground() == before
            observed = focus_state(which)
            assert keyboard_focus_ready(observed, os.getpid())
            evidence.setdefault("duplicate_focus", {})[which] = observed

        case = ProviderCase(identity="hwnd")
        with NativeProvider(
            build_provider(output / "interaction-build", 64),
            output / "focus-control",
            case.configuration("Control", str(uuid.uuid4())),
        ) as control:
            owned_windows.add(control.read()["window"] & 0xFFFFFFFF)
            command("focus-defer-on")
            menu_escape("modern")
            control.command("owner-window")
            window = control.read()["owner_window"]
            observed = native.wait_for(
                lambda: next(
                    (
                        row
                        for row in native.windows(control.read()["pid"])
                        if row["hwnd"] == window
                    ),
                    None,
                ),
                "Owned unrelated foreground control did not map",
            )
            control_report = evidence["foreground_control"] = {
                "window": window,
                "pid": control.read()["pid"],
                "rect": observed["rect"],
                "hit_tests": [],
            }

            def root_at(x, y):
                hit = native.window_at(x, y)
                control_report["hit_tests"].append({"point": [x, y], "window": hit})
                return hit

            point = visible_control_point(window, observed["rect"], root_at)
            assert move(*point)
            cursor = wintypes.POINT()
            assert bind(user, "GetCursorPos", c.c_int32, c.POINTER(wintypes.POINT))(
                c.byref(cursor)
            )
            control_report.update(
                point=list(point),
                cursor=[cursor.x, cursor.y],
                hit_window=native.window_at(cursor.x, cursor.y),
                foreground_before=foreground(),
            )
            assert control_report["cursor"] == list(point)
            assert control_report["hit_window"] == window
            capture("interaction-control-before-click")
            assert native.window_at(*point) == window
            native.click(*point)
            native.wait_for(
                lambda: foreground() == window,
                "Physical click did not focus the control PID",
            )
            control_report["foreground_after"] = foreground()
            after = len(evidence["focus_routes"])
            command("focus")
            await_route(after, fixture_state()["window"], expected_forward=False)
            time.sleep(0.5)
            assert foreground() == window
            assert not call(lambda: applet._popup.get_visible())
            evidence["ineligible_focus"] = {
                "foreground": foreground(),
                "control_pid": control.read()["pid"],
            }
            # This provider has never registered a local icon: no local history
            # or pending claim may swallow its real Shell request.
            after = len(evidence["focus_routes"])
            control.command("focus")
            await_route(after, control.read()["window"], expected_forward=True)
            evidence["never_local_focus"] = {
                "foreground": foreground(),
                "calls": control.read()["calls"],
            }
            command("focus-defer-off")
        if notification_point is not None:
            observed = evidence["balloons"] = {}
            try:
                for phase in ("hide", "click", "timeout"):
                    start = sequence()
                    command("balloon")
                    native.wait_for(
                        lambda start=start: any(
                            row["event"] == 0x402 for row in callbacks("modern", start)
                        ),
                        "Explorer did not show the owned balloon; "
                        "check session notification policy",
                    )
                    capture("interaction-balloon-" + phase)
                    if phase == "hide":
                        command("balloon-clear")
                    elif phase == "click":
                        native.click(*notification_point)
                    expected = {"hide": 0x403, "click": 0x405, "timeout": 0x404}[phase]
                    native.wait_for(
                        lambda start=start, expected=expected: any(
                            row["event"] == expected
                            for row in callbacks("modern", start)
                        ),
                        "Explorer balloon did not return its actual "
                        + phase
                        + " callback",
                        60,
                    )
                    observed[phase] = callbacks("modern", start)
            finally:
                command("balloon-clear")
        evidence["physical_input"] = "passed"
    finally:
        host._dispatch_notification, host._forward = dispatch, forward
        command("focus-defer-off")
