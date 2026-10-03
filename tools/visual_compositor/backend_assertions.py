"""Require agreement between Docking observations and native compositor facts."""

import json


def check_events(evidence, case):
    events = json.loads((evidence / f"{case.name}.events.json").read_text())
    if not isinstance(events, list) or not events:
        return False, "missing backend event sequence"
    by_phase = {event["phase"]: event for event in events}
    if len(by_phase) != len(events):
        return False, "duplicate backend event phase"
    pids = {event["app"]["pid"] for event in events}
    if len(pids) != 1 or not all(isinstance(pid, int) and pid > 0 for pid in pids):
        return False, "Docking restarted or process evidence is missing"

    def window(phase):
        event = by_phase[phase]
        native = [w for w in event["native_windows"] if w.get("title") == "Lab probe"]
        app = [w for w in event["app"]["windows"] if w.get("title") == "Lab probe"]
        if len(native) != 1 or len(app) != 1:
            raise ValueError(f"{phase}: native test window not uniquely tracked")
        return native[0], app[0]

    window("opened")
    if (
        any(w.get("title") == "Lab probe" for w in by_phase["closed"]["native_windows"])
        or by_phase["closed"]["app"]["windows"]
    ):
        return False, "closed window remains in compositor or Docking"
    if case.action == "window-actions":
        for phase, minimized in [("minimized", True), ("restored", False)]:
            native, app = window(phase)
            if (
                native.get("minimized") is not minimized
                or app.get("minimized") is not minimized
            ):
                return False, f"{phase}: native and Docking minimized state disagree"
            if not minimized and (
                native.get("active") is not True or app.get("active") is not True
            ):
                return False, "restored window did not receive focus"
    elif case.action == "workspace-switch":
        active = []
        for phase in ("opened", "switched", "returned"):
            event = by_phase[phase]
            native = [w["id"] for w in event["native_workspaces"] if w["active"]]
            app = [w["id"] for w in event["app"]["workspaces"] if w["active"]]
            if len(native) != 1 or native != app:
                return False, f"{phase}: native and Docking active workspace disagree"
            active.append(native[0])
        if active[0] == active[1] or active[0] != active[2]:
            return False, "workspace did not switch away and return"
    elif case.action == "bridge-recovery":
        owners = [
            by_phase[p]["bridge_available"] for p in ("ready", "disabled", "recovered")
        ]
        if owners != [True, False, True]:
            return False, "bridge disappearance and return not verified"
        geometry = by_phase["recovered"]["geometry"]
        frame, output = geometry["dock_rect"], geometry["outputs"][0]
        if (
            frame["x"] != output["x"]
            or frame["y"] != output["y"]
            or frame["height"] != output["height"]
        ):
            return False, "native dock frame did not recover on the requested edge"
    else:
        return False, "unknown backend scenario"
    return (
        True,
        f"{case.action}: native state and Docking agree throughout; process unchanged",
    )
