"""Owned caption input preserves identity, physical focus, and temporary geometry."""

import ctypes
from types import SimpleNamespace

import pytest

from tools.windows.visual import window_focus as mod


@pytest.fixture
def caption(monkeypatch):
    from tools.windows.visual import input_editor, input_environment

    monkeypatch.setattr(ctypes, "set_last_error", lambda _code: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1460, raising=False)

    def make(*, exposed=False, mode="", bounds=None, workarea=None):
        original = list(bounds or [100, 100, 600, 500])
        state = {
            "bounds": original.copy(),
            "foreground": 999,
            "cursor": (0, 0),
            "moves": [],
            "presses": [],
            "events": [],
            "order": [999, 77],
            "round": 0,
            "recipients": [],
        }
        area = workarea or [0, 0, 1024, 720]

        def current():
            assert not state.get("replaced"), "Owned Cabinet identity changed"
            return {
                "hwnd": 91,
                "pid": 42,
                "class": "CabinetWClass",
                "title": "Owned",
                "rect": state["bounds"].copy(),
            }

        def facts(_native, x, y):
            a, b, c, d = state["bounds"]
            owned = (
                (exposed or len(state["moves"]) == 1)
                and a <= x < c
                and b <= y < d
                and not state.get("overlay")
            )
            if mode == "postmove-covered" and len(state["moves"]) == 1:
                owned = False
            if owned:
                root = {
                    "hwnd": 91,
                    "pid": 42,
                    "thread": 6616,
                    "class": "CabinetWClass",
                    "bounds": [a, b, c, d],
                }
            elif 0 <= y < 660 or mode == "no-desktop":
                root = {
                    "hwnd": 999,
                    "pid": 1000,
                    "class": "Terminal",
                    "bounds": [0, 0, 1024, 660],
                }
            else:
                root = {"hwnd": 7, "pid": 7, "class": "Progman", "bounds": area}
            hit = root.copy()
            if owned and mode == "foreign-child":
                hit.update(hwnd=92, pid=43)
            elif owned and mode in ("actual-child", "actual-close", "subtree-child"):
                hit.update(hwnd=92, pid=42)
            elif owned and mode.startswith("child-reflow"):
                hit.update(
                    hwnd=93
                    if mode == "child-reflow-identity" and state.get("child_reflowed")
                    else 92,
                    pid=42,
                    **{
                        "class": "TITLE_BAR_SCAFFOLDING_WINDOW_CLASS",
                        "bounds": [
                            (700 if mode == "child-reflow-outside" else 417)
                            if state.get("child_reflowed")
                            else 130,
                            70,
                            914,
                            105,
                        ],
                    },
                )
                if mode == "child-reflow-missing":
                    del hit["thread"]
            return {"point": [x, y], "root": root, "hit": hit}

        def send(hwnd, message, first, packed, flags, timeout, output):
            assert hwnd in {91, *range(92, 102)}
            assert (message, first, flags) == (0x84, 0, 0x23)
            assert 0 < timeout <= 500
            point = (
                ctypes.c_int16(packed & 0xFFFF).value,
                ctypes.c_int16((packed >> 16) & 0xFFFF).value,
            )
            state["events"].append(("hit", point))
            state["recipients"].append(hwnd)
            result = {"client": 1, "button": 20, "transparent": -1}.get(mode, 2)
            if mode.startswith(("subtree-", "child-reflow")) or mode in (
                "actual-child",
                "actual-close",
                "child-reflow",
            ):
                result = (
                    1 if hwnd == 91 or mode in ("subtree-cycle", "subtree-limit") else 2
                )
                if mode == "actual-close" and hwnd == 91:
                    result = 20
            if mode.startswith("child-reflow") and any(
                kind == "cursor" for kind, _point in state["events"]
            ):
                state["child_reflowed"] = True
            if mode == "overlay-during-hit" and any(
                kind == "cursor" for kind, _point in state["events"]
            ):
                state["overlay"] = True
            if mode in ("ready-later", "ready-never"):
                result = 2 if mode == "ready-later" and state["round"] > 1 else 1
            if mode == "ready-moved" and state["round"] == 1:
                state["bounds"] = [110, 110, 610, 510]
            output._obj.value = result
            return 0 if mode == "send-fail" else 1

        def cursor(output):
            output._obj.x, output._obj.y = state["cursor"]
            return 1

        def position(hwnd, after, x, y, cx, cy, flags):
            assert (hwnd, after, cx, cy, flags) == (91, 0, 0, 0, 0x215)
            state["moves"].append((x, y))
            state["events"].append(("move", (x, y)))
            if "restore-fail" in mode and len(state["moves"]) == 2:
                return 0
            width, height = original[2] - original[0], original[3] - original[1]
            state["bounds"] = [x, y, x + width, y + height]
            if mode == "move-activates" and len(state["moves"]) == 1:
                state["foreground"] = 91
            return 1

        def neighbor(hwnd, index):
            assert hwnd == 91 and index in (3, 2)
            return state["order"][index == 2]

        def to_client(hwnd, value):
            assert hwnd in {91, *range(92, 102)}
            value._obj.x -= state["bounds"][0]
            value._obj.y -= state["bounds"][1]
            return True

        def child(parent, _point, flags):
            assert flags == 7
            if mode == "subtree-cycle":
                return 92 if parent == 91 else 91
            if mode == "subtree-limit":
                return parent + 1
            if mode.startswith("subtree-"):
                return 92 if parent == 91 else parent
            return parent

        def bind(_user, name, result_type, *arguments):
            if name == "SendMessageTimeoutW":
                assert result_type is ctypes.c_ssize_t
                assert arguments[-1] == ctypes.POINTER(ctypes.c_size_t)
                return send
            return {
                "GetDpiForWindow": lambda hwnd: 96 if hwnd == 91 else 0,
                "GetSystemMetricsForDpi": lambda index, dpi: (
                    {4: 24, 33: 8, 92: 0}[index] if dpi == 96 else 0
                ),
                "GetCursorPos": cursor,
                "SetWindowPos": position,
                "GetWindow": neighbor,
                "GetAncestor": lambda _hwnd, _kind: (
                    88 if mode == "subtree-ancestor" else 91
                ),
                "ScreenToClient": to_client,
                "ChildWindowFromPointEx": child,
            }[name]

        def click(x, y, *, before_press):
            state["events"].append(("cursor", (x, y)))
            state["cursor"] = (x, y)
            if mode in ("cursor-race", "cursor-restore-fail"):
                state["cursor"] = (x + 1, y)
            if mode == "identity-race":
                state["replaced"] = True
            before_press()
            state["presses"].append((x, y))
            if mode == "partial-input":
                error = AssertionError("Caption input incomplete")
                error.__dict__["desktop_input_cleanup"] = {
                    "attempted": True,
                    "completed": False,
                }
                raise error
            state["foreground"] = 91
            state["order"] = [0, 999]  # Normal raising from a genuine physical click.

        def wait(predicate, message, seconds=None):
            assert seconds is None or 0 < seconds <= 5
            state["round"] += 1
            value = predicate()
            if "ready" in message:
                limit = 50 if mode == "ready-never" else state["round"] + 1
                while value is None and state["round"] < limit:
                    state["round"] += 1
                    value = predicate()
            assert value, message
            return value

        native = SimpleNamespace(
            user=SimpleNamespace(GetForegroundWindow=lambda: state["foreground"]),
            workarea=lambda: area,
            explorer=lambda: {"pid": 7},
            click=click,
            wait_for=wait,
        )
        monkeypatch.setattr(input_environment, "bind", bind)
        monkeypatch.setattr(input_editor, "_point_facts", facts)
        monkeypatch.setattr(
            input_editor,
            "_window_facts",
            lambda _user, hwnd: {
                "hwnd": hwnd,
                "pid": 43 if mode == "subtree-foreign" and hwnd != 91 else 42,
            },
        )
        monkeypatch.setattr(
            input_editor, "_topmost", lambda _user, hwnd: False if hwnd == 91 else None
        )
        return native, current, state

    return make


def test_exposed_owned_caption_uses_one_physical_click_without_geometry(caption):
    native, current, state = caption(exposed=True)
    journal = {}
    mod.focus_caption(native, current, journal)
    assert len(state["presses"]) == 1 and not state["moves"]
    assert (
        journal["physical_caption_click"] and journal["foreground_after_caption"] == 91
    )


@pytest.mark.parametrize(
    "mode,exposed", [("actual-child", True), ("subtree-child", False)]
)
def test_explorer_caption_queries_real_owned_child_without_accepting_root_client(
    caption, mode, exposed
):
    native, current, state = caption(mode=mode, exposed=exposed)
    journal = {}
    mod.focus_caption(native, current, journal)
    first = journal["caption_probes"][0]
    assert first["root_query"]["hit_test"] == 1 and first["hit_test"] == 2
    assert first["caption_recipient"] == 92 and state["recipients"][:2] == [91, 92]
    if exposed:
        assert first["actual_child_query"]["hwnd"] == 92
        assert "owned_subtree_child" not in first
    else:
        assert first["hit"]["pid"] == 1000  # Cover was never queried or clicked.
        assert first["owned_subtree_child"] == 92
        assert "actual_child_query" not in first
        assert journal["geometry_restored"]
    assert len(state["presses"]) == 1


@pytest.mark.parametrize(
    "mode", ["subtree-foreign", "subtree-ancestor", "subtree-cycle", "subtree-limit"]
)
def test_explorer_subtree_queries_never_escape_the_exact_owned_root(caption, mode):
    native, current, state = caption(mode=mode)
    with pytest.raises(AssertionError):
        mod.focus_caption(native, current, {})
    assert not state["presses"] and not state["moves"]
    if mode in ("subtree-foreign", "subtree-ancestor"):
        assert state["recipients"] == [91]
    if mode == "subtree-limit":
        assert len(state["recipients"]) == 9


@pytest.mark.parametrize("mode", ["ready-later", "ready-moved"])
def test_caption_readiness_retains_first_facts_then_clicks_once_on_current_geometry(
    caption, mode
):
    native, current, state = caption(mode=mode, exposed=True)
    journal = {}
    mod.focus_caption(native, current, journal)
    first = journal["caption_probes"][0]
    assert first["attempt"] == 1
    assert (
        first.get("raced_bounds") if mode == "ready-moved" else first["hit_test"] == 1
    )
    assert journal["caption_before_press"]["attempt"] == 2
    assert len(state["presses"]) == 1 and not state["moves"]


def test_unready_caption_probe_log_is_bounded_and_never_clicks(caption):
    native, current, state = caption(mode="ready-never", exposed=True)
    journal = {}
    with pytest.raises(AssertionError):
        mod.focus_caption(native, current, journal)
    probes = journal["caption_probes"]
    assert len(probes) == 96 and journal["caption_probes_dropped"] == 204
    assert all(row["attempt"] == 1 for row in probes[:6])
    assert probes[-1]["attempt"] == 50 and not state["presses"]


def test_overlay_arriving_during_caption_query_is_rechecked_before_dispatch(caption):
    native, current, state = caption(mode="overlay-during-hit", exposed=True)
    journal = {}
    with pytest.raises(AssertionError, match="covered or changed"):
        mod.focus_caption(native, current, journal)
    observed = journal["caption_before_press"]
    assert observed["physical_before"]["root"]["hwnd"] == 91
    assert observed["physical_after"]["root"]["hwnd"] == 999
    assert observed["raced_physical"] and observed["hit_test"] is None
    assert "caption_press_started" not in journal and not state["presses"]
    assert journal["caption_guard_reads"] == 1


def test_child_caption_cannot_override_root_close_button(caption):
    native, current, state = caption(mode="actual-close", exposed=True)
    journal = {}
    with pytest.raises(AssertionError):
        mod.focus_caption(native, current, journal)
    observed = journal["caption_probes"][0]
    assert observed["root_query"]["hit_test"] == 20
    assert observed["actual_child_query"]["hit_test"] == 2
    assert observed["hit_test"] == 20 and not state["presses"]


def test_explorer_same_child_130_to_417_reflow_waits_without_reaiming_or_input_retry(
    caption,
):
    native, current, state = caption(
        mode="child-reflow", exposed=True, bounds=[122, 70, 922, 670]
    )
    journal = {}
    mod.focus_caption(native, current, journal)
    raced = journal["caption_probes"][1]
    assert raced["physical_before"]["hit"]["bounds"] == [130, 70, 914, 105]
    assert raced["physical_after"]["hit"]["bounds"] == [417, 70, 914, 105]
    assert raced["raced_child_bounds"] and raced["hit_test"] is None
    assert journal["caption_guard_reads"] == 2
    assert journal["caption_before_press"]["hit_test"] == 2
    assert not journal["caption_before_press"].get("raced_physical")
    assert state["presses"] == [(522, 85)]
    assert sum(kind == "cursor" for kind, _point in state["events"]) == 1
    assert not state["moves"]


@pytest.mark.parametrize(
    "mode", ["child-reflow-outside", "child-reflow-identity", "child-reflow-missing"]
)
def test_reflow_cannot_retry_changed_identity_or_a_point_outside_fresh_child(
    caption, mode
):
    native, current, state = caption(
        mode=mode, exposed=True, bounds=[122, 70, 922, 670]
    )
    journal = {}
    with pytest.raises(AssertionError, match="covered or changed"):
        mod.focus_caption(native, current, journal)
    assert journal["caption_guard_reads"] == 1
    assert "caption_press_started" not in journal and not state["presses"]


def test_cursor_settle_uses_original_readiness_deadline_without_reset(
    caption, monkeypatch
):
    native, current, state = caption(exposed=True)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: clock.now))
    click = native.click

    def settled(*arguments, **keywords):
        clock.now = 5.01
        return click(*arguments, **keywords)

    native.click = settled
    journal = {}
    with pytest.raises(AssertionError, match="exceeded its deadline"):
        mod.focus_caption(native, current, journal)
    assert len(state["recipients"]) == 1
    assert not state["presses"] and "caption_press_started" not in journal


def test_caption_deadline_limits_each_message_and_does_not_retry_native_timeout(
    caption, monkeypatch
):
    from tools.windows.visual import input_environment

    native, current, state = caption(mode="ready-never", exposed=True)
    original_bind = input_environment.bind
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: clock.now))
    timeouts = []

    def bind(user, name, result, *arguments):
        function = original_bind(user, name, result, *arguments)
        if name != "SendMessageTimeoutW":
            return function

        def send(hwnd, message, first, point, flags, timeout, output):
            timeouts.append(timeout)
            clock.now += min(0.4, timeout / 1000)
            if timeout < 400:
                return 0
            return function(hwnd, message, first, point, flags, timeout, output)

        return send

    monkeypatch.setattr(input_environment, "bind", bind)
    journal = {}
    with pytest.raises(AssertionError, match="query failed or timed out"):
        mod.focus_caption(native, current, journal)
    assert 4.9 <= clock.now <= 5
    assert 0 < timeouts[-1] < 400 and max(timeouts) <= 500
    assert journal["caption_probes"][-1]["queries"][-1]["accepted"] is False
    assert journal["caption_probes"][-1]["queries"][-1]["winerror"] == 1460
    assert not state["presses"] and not state["moves"]


@pytest.mark.parametrize("width", [500, 1032, 2400])
def test_covered_caption_uses_only_observed_desktop_then_restores_before_return(
    caption, width
):
    native, current, state = caption(bounds=[100, 100, 100 + width, 500])
    original = state["bounds"].copy()
    journal = {}
    mod.focus_caption(native, current, journal)
    assert len(state["moves"]) == 2 and state["moves"][-1] == (100, 100)
    assert state["bounds"] == original and state["foreground"] == 91
    assert journal["caption_placement"]["requested"][3] > 720
    assert (
        journal["geometry_restored"]
        and journal["caption_after_placement"]["hit_test"] == 2
    )
    assert len(state["presses"]) == 1
    events = [kind for kind, _value in state["events"]]
    moved = events.index("move")
    assert events[moved + 1 : moved + 3] == ["hit", "cursor"]
    assert state["events"][-1][0] == "move"  # Restored before any caller UIA work.


@pytest.mark.parametrize(
    "mode",
    [
        "client",
        "button",
        "transparent",
        "send-fail",
        "no-desktop",
        "foreign-child",
        "postmove-covered",
        "move-activates",
        "cursor-race",
        "identity-race",
    ],
)
def test_unknown_or_changed_caption_never_receives_a_button_press(caption, mode):
    native, current, state = caption(exposed=mode == "foreign-child", mode=mode)
    journal = {}
    with pytest.raises(AssertionError):
        mod.focus_caption(native, current, journal)
    assert not state["presses"]
    if state["moves"] and mode != "identity-race":
        assert state["bounds"] == [100, 100, 600, 500]
        assert journal["geometry_restored"]
    if mode == "identity-race":
        assert journal["geometry_restored"] is False and "restore_error" in journal


@pytest.mark.parametrize(
    "bounds,workarea",
    [
        ([-500, 50, 0, 450], [-1024, 0, 0, 720]),
        ([-500, -500, 0, -100], [-1024, -768, 0, 0]),
    ],
)
def test_caption_message_preserves_signed_negative_screen_coordinates(
    caption, bounds, workarea
):
    native, current, state = caption(exposed=True, bounds=bounds, workarea=workarea)
    mod.focus_caption(native, current, {})
    hit = next(point for kind, point in state["events"] if kind == "hit")
    assert hit[0] < 0 and (hit[1] < 0) is (bounds[1] < 0)
    assert state["presses"] == [hit]


def test_caption_message_rejects_out_of_range_coordinates_without_wrapping(caption):
    native, current, state = caption(
        exposed=True, bounds=[-40000, 0, -39500, 400], workarea=[-50000, 0, 0, 720]
    )
    with pytest.raises(AssertionError, match="signed shorts"):
        mod.focus_caption(native, current, {})
    assert not state["events"] and not state["presses"]


def test_partial_exposed_caption_input_keeps_started_and_failed_release_evidence(
    caption,
):
    native, current, state = caption(exposed=True, mode="partial-input")
    journal = {}
    with pytest.raises(AssertionError, match="input incomplete"):
        mod.focus_caption(native, current, journal)
    assert journal["caption_press_started"] and "physical_caption_click" not in journal
    assert journal["caption_input_cleanup"] == {"attempted": True, "completed": False}
    assert not state["moves"]


def test_failed_geometry_restore_is_fatal_and_preserves_primary_if_also_failed(caption):
    for mode, expected in (
        ("restore-fail", "did not restore"),
        ("cursor-restore-fail", "cursor moved"),
    ):
        native, current, _state = caption(mode=mode)
        journal = {}
        with pytest.raises(AssertionError, match=expected):
            mod.focus_caption(native, current, journal)
        assert journal["geometry_restored"] is False
        assert journal["restore_error"]["class"] == "AssertionError"
