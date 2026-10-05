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
    if case.action == "window-switching":
        return check_window_switching(evidence / f"{case.name}.windows.json")

    def phase(name):
        path = evidence / f"{case.name}.{name}.png"
        if not path.exists():
            raise ValueError(f"missing {name} capture")
        return path

    try:
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
    except (OSError, ValueError) as exc:
        return False, f"interactive evidence: {exc}"


def check_window_switching(path: Path) -> tuple[bool, str]:
    """Assert compositor-observed focus and counts, not input acknowledgements."""
    try:
        report = json.loads(path.read_text())
        if report["gtk_display_is_wayland"] is not True:
            return False, "window switching did not use a native Wayland client"
        phases = report["phases"]
        original_alpha = {
            w["id"] for w in phases["launched"]["windows"] if w["app"] == "lab-alpha"
        }
        original_beta = {
            w["id"] for w in phases["other-app"]["windows"] if w["app"] == "lab-beta"
        }
        multiple_alpha = {
            w["id"] for w in phases["multiple"]["windows"] if w["app"] == "lab-alpha"
        }
        expected = {
            "launched": (1, 0, None),
            "other-app": (1, 1, "lab-beta"),
            "switched": (1, 1, "lab-alpha"),
            "minimized": (1, 1, None),
            "restored": (1, 1, "lab-alpha"),
            "workspace": (1, 1, "lab-alpha"),
            "multiple": (2, 1, None),
            "multiple-switched": (2, 1, "lab-alpha"),
            "closed": (0, 0, None),
        }
        for phase, (alpha_count, beta_count, focused) in expected.items():
            windows = phases[phase]["windows"]
            alpha = [w for w in windows if w["app"] == "lab-alpha"]
            beta = [w for w in windows if w["app"] == "lab-beta"]
            if len(alpha) != alpha_count or len(beta) != beta_count:
                return False, f"{phase}: unexpected application window count"
            alpha_ids = {w["id"] for w in alpha}
            beta_ids = {w["id"] for w in beta}
            if alpha_count == 1 and alpha_ids != original_alpha:
                return False, f"{phase}: original window was replaced"
            if beta_count == 1 and beta_ids != original_beta:
                return False, f"{phase}: original window was replaced"
            if alpha_count == 2 and (
                alpha_ids != multiple_alpha
                or len(alpha_ids) != 2
                or not original_alpha < alpha_ids
            ):
                return False, f"{phase}: original or additional window was replaced"
            if focused and not any(
                w["app"] == focused and w["focused"] and not w["minimized"]
                for w in windows
            ):
                return False, f"{phase}: existing window was not focused"
            if phase == "minimized" and not alpha[0]["minimized"]:
                return False, "active app was not minimized"
            if phase == "restored" and alpha[0]["minimized"]:
                return False, "minimized app was not restored"
            if phase == "workspace" and (
                phases[phase]["active_workspace"] != 1 or alpha[0]["workspace"] != 1
            ):
                return False, "activation did not switch workspace"
        return (
            True,
            "real clicks: counts, focus, minimize/restore and workspaces verified",
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        return False, f"window switching evidence: {exc}"
