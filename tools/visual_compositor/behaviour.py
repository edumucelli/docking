"""Independent assertions on the intermediate screenshots of interactive cases."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image


def footprint(path: Path, rect: dict) -> int:
    with Image.open(path) as image:
        crop = image.convert("RGB").crop(
            (
                rect["x"],
                rect["y"],
                rect["x"] + rect["width"],
                rect["y"] + rect["height"],
            )
        )
        pixels = np.asarray(crop, dtype=np.int16)
        background = Counter(crop.getdata()).most_common(1)[0][0]
        return int(np.any(np.abs(pixels - background) > 24, axis=2).sum())


def check_action(*, evidence: Path, case, band: dict) -> tuple[bool, str]:
    def phase(name):
        path = evidence / f"{case.name}.{name}.png"
        if not path.exists():
            raise ValueError(f"missing {name} capture")
        return path

    try:
        if case.action and case.action.startswith("popup-"):
            from tools.visual_compositor.popup_assertions import check_popup

            return check_popup(evidence, case)
        if case.action in {"window-actions", "workspace-switch", "bridge-recovery"}:
            from tools.visual_compositor.backend_assertions import check_events

            return check_events(evidence, case)
        if case.action == "window":
            import ast

            states = []
            for name in ("before", "opened", "closed"):
                windows = json.loads(
                    (evidence / f"{case.name}.{name}.windows.json").read_text()
                )
                if not isinstance(windows, list) or not all(
                    isinstance(w, dict) for w in windows
                ):
                    return False, f"invalid {name} compositor window evidence"
                states.append(any(w.get("title") == "Lab probe" for w in windows))
            text = (evidence / f"{case.name}.opened.items.txt").read_text()
            items = ast.literal_eval(text.replace("@as ", ""))[0]
            if not isinstance(items, list) or not all(
                isinstance(item, str) for item in items
            ):
                return False, "invalid Docking window tracking evidence"
            if "lab-probe.desktop" not in items:
                return False, "Docking did not discover the native test window"
            return states == [
                False,
                True,
                False,
            ], f"native window item lifecycle: {states}"
        if case.action in {"autohide", "dodge"}:
            visible = footprint(phase("revealed"), band)
            hidden = footprint(phase("hidden"), band)
            restored = footprint(
                phase("hidden-again" if case.action == "autohide" else "restored"), band
            )
            if visible < 1000 or hidden > max(300, visible * 0.15):
                return (
                    False,
                    f"dock did not hide: visible={visible}, hidden={hidden} pixels",
                )
            if case.action == "autohide" and restored > max(300, visible * 0.15):
                return False, "dock did not hide again after the pointer left"
            if case.action == "dodge" and restored < visible * 0.8:
                return False, "dock did not return after the overlapping window closed"
            return (
                True,
                f"{case.action}: hidden and revealed states verified from pixels",
            )
        before, after = phase("resting"), phase("effect")
        if case.action == "zoom":
            # Measure the dock itself; a tooltip above it must not count as zoom.
            strip = {
                **band,
                "y": band["y"] + max(0, band["height"] - 110),
                "height": min(110, band["height"]),
            }
            old, new = footprint(before, strip), footprint(after, strip)
            return new > old * 1.1, f"hover footprint: {old} -> {new} pixels"
        with Image.open(before) as a, Image.open(after) as b:
            if a.size != b.size:
                return False, "interactive captures changed dimensions"
            delta = np.abs(
                np.asarray(a.convert("RGB"), dtype=np.int16)
                - np.asarray(b.convert("RGB"), dtype=np.int16)
            )
            # A moved cursor or an icon highlight is not evidence of a popup.
            # Require the effect above the resting dock's observed content.
            crop = a.convert("RGB").crop(
                (
                    band["x"],
                    band["y"],
                    band["x"] + band["width"],
                    band["y"] + band["height"],
                )
            )
            background = Counter(crop.getdata()).most_common(1)[0][0]
            rows = np.where(
                np.any(
                    np.abs(np.asarray(crop, dtype=np.int16) - background) > 24, axis=2
                )
            )[0]
            if not rows.size:
                return False, "no resting dock to locate the popup"
            cutoff = band["y"] + int(rows.min()) - 8
            changed = int(
                np.any(
                    delta[band["y"] : cutoff, band["x"] : band["x"] + band["width"]]
                    > 24,
                    axis=2,
                ).sum()
            )
        minimum = 500 if case.action == "menu" else 80
        return changed >= minimum, f"{case.action}: {changed} pixels changed"
    except (OSError, ValueError, SyntaxError, TypeError, IndexError, KeyError) as exc:
        return False, f"interactive evidence: {exc}"
