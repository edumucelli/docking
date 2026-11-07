"""Physical activation of one verified owned window caption."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes


def focus_caption(native, current, journal):
    """Expose only the owned caption, click once, and restore any temporary move."""
    from .input_editor import _point_facts, _topmost, _window_facts
    from .input_environment import bind

    window = current()
    hwnd, pid, bounds = window["hwnd"], window["pid"], list(window["rect"])
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    assert width > 0 and height > 0, "Owned window bounds are invalid"
    user = native.user
    send = bind(
        user,
        "SendMessageTimeoutW",
        ctypes.c_ssize_t,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_size_t),
    )
    dpi = bind(user, "GetDpiForWindow", ctypes.c_uint32, ctypes.c_void_p)(hwnd)
    metrics = bind(
        user, "GetSystemMetricsForDpi", ctypes.c_int, ctypes.c_int, ctypes.c_uint32
    )
    assert dpi, "Owned window DPI is unavailable"
    caption = metrics(4, dpi)  # SM_CYCAPTION.
    frame = metrics(33, dpi) + metrics(92, dpi)  # CYFRAME + CXPADDEDBORDER.
    assert caption > 0 and frame >= 0, "Owned window caption metrics are invalid"
    workarea = native.workarea()
    journal["caption_metrics"] = {"dpi": dpi, "caption": caption, "frame": frame}
    probes = journal["caption_probes"] = []
    started = time.monotonic()
    attempt = 0
    ready_deadline = None

    def inside(point):
        x, y = point
        return workarea[0] <= x < workarea[2] and workarea[1] <= y < workarea[3]

    def hit(point):
        before = current()
        initial = list(before["rect"])
        x, y = point
        assert -32768 <= x <= 32767 and -32768 <= y <= 32767, (
            "Owned caption coordinates exceed WM_NCHITTEST's signed shorts"
        )
        packed = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
        if packed & 0x80000000:
            packed -= 1 << 32
        row = {
            **_point_facts(native, x, y),
            "attempt": attempt,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
        row["physical_before"] = {key: row[key] for key in ("point", "root", "hit")}
        if len(probes) >= 96:
            del probes[6]  # Retain the first six and the bounded latest facts.
            journal["caption_probes_dropped"] = (
                journal.get("caption_probes_dropped", 0) + 1
            )
        probes.append(row)

        def query(target):
            current()
            facts = _window_facts(user, target)
            assert facts.get("pid") == pid, "Caption query recipient changed ownership"
            if target != hwnd:
                assert (
                    bind(
                        user,
                        "GetAncestor",
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_uint32,
                    )(target, 2)
                    == hwnd
                ), "Caption child left the owned root"
            result = ctypes.c_size_t()
            remaining = (
                500
                if ready_deadline is None
                else min(500, int((ready_deadline - time.monotonic()) * 1000))
            )
            assert remaining > 0, "Owned caption readiness exceeded its deadline"
            response = {"hwnd": target}
            row.setdefault("queries", []).append(response)
            ctypes.set_last_error(0)
            accepted = send(
                target, 0x84, 0, packed, 0x23, remaining, ctypes.byref(result)
            )
            response.update(
                accepted=bool(accepted),
                hit_test=ctypes.c_ssize_t(result.value).value if accepted else None,
            )
            if not accepted:
                response["winerror"] = ctypes.get_last_error()
            assert accepted, "Owned window caption query failed or timed out"
            return response

        root = row["root_query"] = query(hwnd)
        effective = root
        actual = row["hit"]
        if (
            row["root"]["hwnd"] == hwnd
            and actual.get("pid") == pid
            and actual["hwnd"] != hwnd
        ):
            child = row["actual_child_query"] = query(actual["hwnd"])
            # A child can supply Explorer's client title bar, but must never
            # override a real nonclient button, edge, or menu classification.
            if root["hit_test"] in (1, 2):
                effective = child
        elif root["hit_test"] == 1 and before["class"] == "CabinetWClass":
            # A covering owned window can hide Explorer's title-bar island.
            # Search only the pinned Cabinet subtree, never the covering HWND.
            parent, seen = hwnd, {hwnd}
            for _depth in range(8):
                current()
                point_in_parent = wintypes.POINT(x, y)
                assert bind(
                    user,
                    "ScreenToClient",
                    ctypes.c_int32,
                    ctypes.c_void_p,
                    ctypes.POINTER(wintypes.POINT),
                )(parent, ctypes.byref(point_in_parent)), (
                    "Owned caption coordinates unavailable"
                )
                child = int(
                    bind(
                        user,
                        "ChildWindowFromPointEx",
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        wintypes.POINT,
                        ctypes.c_uint32,
                    )(parent, point_in_parent, 7)
                    or 0
                )
                if not child or child == parent:
                    break
                assert child not in seen, "Owned caption child hierarchy cycled"
                seen.add(child)
                effective = query(child)  # Verifies PID and root before messaging.
                row.setdefault("owned_subtree_queries", []).append(effective)
                parent = child
                if effective["hit_test"] != 1:
                    break
            else:
                raise AssertionError("Owned caption child hierarchy exceeds the bound")
            if parent != hwnd:
                row["owned_subtree_child"] = parent
        row["accepted"] = True
        row["hit_test"] = effective["hit_test"]
        row["queried_hit_test"] = effective["hit_test"]
        row["caption_recipient"] = effective["hwnd"]
        after = row["physical_after"] = _point_facts(native, x, y)
        row.update(after)
        if after != row["physical_before"]:
            row.update(raced_physical=True, hit_test=None)
            before_hit = row["physical_before"]["hit"]
            row["raced_child_bounds"] = (
                after["root"] == row["physical_before"]["root"]
                and all(
                    after["root"].get(key) for key in ("hwnd", "pid", "thread", "class")
                )
                and before_hit["hwnd"] != hwnd
                and all(
                    before_hit.get(key) for key in ("hwnd", "pid", "thread", "class")
                )
                and all(
                    before_hit.get(key) == after["hit"].get(key)
                    for key in ("hwnd", "pid", "thread", "class")
                )
                and before_hit.get("bounds") != after["hit"].get("bounds")
            )
        if current()["rect"] != initial:
            row.update(raced_bounds=True, hit_test=None)
        return row

    def own_caption(row):
        return (
            row["hit_test"] == 2
            and row["root"]["hwnd"] == hwnd
            and row["root"].get("pid") == pid
            and row["hit"].get("pid") == pid
        )

    def click(point, expected_bounds):
        def before_press():
            nonlocal ready_deadline

            def stable():
                assert current()["rect"] == expected_bounds, (
                    "Owned window moved before caption input"
                )
                cursor = wintypes.POINT()
                assert bind(
                    user, "GetCursorPos", ctypes.c_int32, ctypes.POINTER(wintypes.POINT)
                )(ctypes.byref(cursor))
                assert (cursor.x, cursor.y) == point, "Owned caption cursor moved"
                observed = journal["caption_before_press"] = hit(point)
                journal["caption_guard_reads"] = (
                    journal.get("caption_guard_reads", 0) + 1
                )
                assert inside(point), "Owned caption point left the workarea"
                if own_caption(observed):
                    return True
                bounds = observed["hit"].get("bounds")
                if (
                    observed.get("raced_child_bounds")
                    and not observed.get("raced_bounds")
                    and observed["queried_hit_test"] == 2
                    and observed["root"]["hwnd"] == hwnd
                    and observed["root"].get("pid") == pid
                    and observed["hit"].get("pid") == pid
                    and observed["caption_recipient"] == observed["hit"]["hwnd"]
                    and bounds is not None
                    and bounds[0] <= point[0] < bounds[2]
                    and bounds[1] <= point[1] < bounds[3]
                ):
                    # Same owned child, still under this fixed point: wait for
                    # a stable genuine caption read before our first press.
                    return None
                raise AssertionError("Owned window caption is covered or changed")

            remaining = ready_deadline - time.monotonic()
            assert remaining > 0, "Owned caption readiness exceeded its deadline"
            native.wait_for(
                stable, "Owned caption is not ready before input", remaining
            )
            journal["caption_press_started"] = True
            ready_deadline = None

        try:
            native.click(*point, before_press=before_press)
        except BaseException as error:
            cleanup = error.__dict__.get("desktop_input_cleanup")
            if cleanup is not None:
                journal["caption_input_cleanup"] = cleanup
            raise
        journal["physical_caption_click"] = True

        def foreground():
            current()
            return user.GetForegroundWindow() == hwnd

        native.wait_for(
            foreground, "Physical window caption did not receive foreground"
        )
        journal["foreground_after_caption"] = user.GetForegroundWindow()

    def ready():
        nonlocal attempt, bounds, width, height
        attempt += 1
        bounds = list(current()["rect"])
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        assert width > 0 and height > 0, "Owned window bounds are invalid"
        first = None
        for fraction in (0.5, 0.65, 0.8):
            for vertical in (0.33, 0.66):
                offset = int(width * fraction), frame + int(caption * vertical)
                point = bounds[0] + offset[0], bounds[1] + offset[1]
                row = hit(point)
                if row.get("raced_bounds") or row.get("raced_physical"):
                    return None
                if row["hit_test"] == 2:
                    first = first or {"offset": offset, "point": None}
                    if inside(point) and own_caption(row):
                        return {"offset": offset, "point": point}
        return first

    ready_deadline = time.monotonic() + 5
    selected = native.wait_for(
        ready, "Owned window has no ready verified draggable caption", 5
    )
    caption_offset = selected["offset"]
    if selected["point"] is not None:
        click(selected["point"], bounds)
        return
    shell = native.explorer()
    assert shell is not None, "Owned caption placement has no known desktop"
    candidates = journal["caption_placements"] = []
    chosen = None
    for x in dict.fromkeys(
        (
            workarea[0],
            workarea[2] - width,
            (workarea[0] + workarea[2]) // 2 - caption_offset[0],
        )
    ):
        for y in dict.fromkeys((workarea[1], workarea[3] - frame - caption - 2)):
            point = x + caption_offset[0], y + caption_offset[1]
            if not inside(point):
                continue
            facts = _point_facts(native, *point)
            candidates.append(facts)
            root = facts["root"]
            desktop = (
                root.get("class") in {"Progman", "WorkerW"}
                and root.get("pid") == shell["pid"]
                and facts["hit"].get("pid") == shell["pid"]
            )
            owned = (
                root["hwnd"] == hwnd
                and root.get("pid") == pid
                and facts["hit"].get("pid") == pid
            )
            if owned or desktop:
                chosen = ([x, y, x + width, y + height], point)
                break
        if chosen is not None:
            break
    assert chosen is not None, "No observed exposed place for the owned window caption"
    requested, point = chosen
    assert current()["rect"] == bounds, "Owned window moved before caption placement"
    topmost, foreground_before = _topmost(user, hwnd), user.GetForegroundWindow()
    neighbor = bind(
        user, "GetWindow", ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
    )
    order_before = [int(neighbor(hwnd, index) or 0) for index in (3, 2)]
    position = bind(
        user,
        "SetWindowPos",
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    flags = 0x0215  # NOSIZE | NOZORDER | NOACTIVATE | NOOWNERZORDER.
    journal["caption_placement"] = {
        "before": bounds,
        "requested": requested,
        "flags": flags,
        "foreground_before": foreground_before,
        "topmost": topmost,
    }
    journal["geometry_restored"] = False
    try:
        current()
        assert position(hwnd, 0, requested[0], requested[1], 0, 0, flags), (
            "Owned caption placement failed"
        )
        assert current()["rect"] == requested, (
            "Owned caption placement readback differs"
        )
        assert (
            _topmost(user, hwnd) == topmost
            and user.GetForegroundWindow() == foreground_before
        ), "Nonactivating caption placement changed foreground or topmost state"
        assert [int(neighbor(hwnd, index) or 0) for index in (3, 2)] == order_before, (
            "Caption placement changed Z order"
        )
        placed_hit = journal["caption_after_placement"] = hit(point)
        assert inside(point) and own_caption(placed_hit), (
            "Placed window caption is covered or changed"
        )
        click(point, requested)
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            current()
            restore_foreground = user.GetForegroundWindow()
            restore_order = [int(neighbor(hwnd, index) or 0) for index in (3, 2)]
            assert position(hwnd, 0, bounds[0], bounds[1], 0, 0, flags), (
                "Owned caption geometry did not restore"
            )
            assert current()["rect"] == bounds, "Owned caption restored bounds differ"
            assert (
                _topmost(user, hwnd) == topmost
                and user.GetForegroundWindow() == restore_foreground
            ), "Caption geometry restoration changed foreground or topmost state"
            assert [
                int(neighbor(hwnd, index) or 0) for index in (3, 2)
            ] == restore_order, "Caption restoration changed Z order"
            journal["geometry_restored"] = True
        except (AssertionError, OSError) as error:
            journal["restore_error"] = {
                "class": type(error).__name__,
                "errno": error.errno if isinstance(error, OSError) else None,
            }
            if not active_error:
                raise
