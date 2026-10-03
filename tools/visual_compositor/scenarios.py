"""Case matrix for the visual compositor lab (host-side, single source of truth).

The container is deliberately dumb: it is handed a JSON list of cases and runs
them. Everything about *what* to test and *what the result means* lives here and
in compare.py, on the host.

Each case writes a subset of Docking's config (``$XDG_CONFIG_HOME/docking/dock.json``)
before the dock is launched. Keys are the flat ``Config`` dataclass fields
documented in docs/CONFIGURATION.md.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

# Thickness in pixels of the band cropped from the expected edge for the
# baseline comparison. Must comfortably contain the dock surface: the dock
# surface is taller than the visible strip because of zoom/animation headroom
# (docs/HEADLESS_WAYLAND_TESTING.local.md:768-782 measured 187px for a much
# shorter visible shelf), so this is deliberately generous.
EDGE_BAND_PX = 320


@dataclass(frozen=True)
class Case:
    """One runnable case: a dock configuration plus what it is meant to prove."""

    name: str
    behavior: str
    overrides: dict = field(default_factory=dict)
    # Edge to assert the dock hugs. "bottom" means the dock's rendered content
    # must reach the bottom of the output.
    edge: str = "bottom"
    # Expected distance from the screen edge, in pixels. Non-zero where the case
    # configures a gap.
    gap: int = 0
    # Which output the dock should land on, as an index into the compositor's
    # output list. Comparison must use this, not always outputs[0].
    output_index: int = 0
    # Preferred over output_index when set: identify the target output by its
    # name. Compositor enumeration order is not guaranteed to match connector
    # numbering -- sway listed HEADLESS-2 before HEADLESS-1 -- so an index-based
    # expectation can silently point at the wrong output and produce a result
    # that looks like an app bug when it is a harness bug.
    output_name: str | None = None
    # Name of the zero-gap counterpart case. When both run, their measured edge
    # positions are compared directly: a gap cannot be verified by an absolute
    # tolerance wider than the gap itself, but the *difference* between the two
    # cancels the unknown inset of the visible shelf within its surface.
    pair_with: str | None = None
    # Capability a compositor must provide for this case to mean anything. When
    # the adapter does not provide it the case reports explicitly-unsupported
    # rather than silently passing or silently disappearing.
    requires: str | None = None
    # Thickness of a panel reserving space at the same edge, in pixels. The dock
    # must sit *inside* the remaining usable area, so the expected edge moves
    # inward by this much. Zero means no panel.
    panel: int = 0
    # Maximize a real client and verify it avoids the dock, then regains the
    # workarea when Docking exits. Currently implemented by the Cinnamon probe.
    maximize_window: bool = False
    # Exercise the shell-owned reservation's cleanup even without app teardown.
    crash_dock: bool = False
    action: str | None = None
    display_scene: str | None = None
    display_change: str | None = None


# A fixed, static pinned set. Docking's own first-run default is applets --
# clock, calendar, weather, systemmonitor (docking/core/config.py:249-257) --
# which render live, time-varying data. Leaving defaults in place would mean the
# dock never settles and no pixel baseline can ever converge, so every case pins
# these synthetic launchers instead. run_session.sh creates the .desktop files.
STATIC_PINNED = ["lab-alpha.desktop", "lab-beta.desktop", "lab-gamma.desktop"]

# Held constant across every case so a difference between two captures is always
# attributable to the behavior under test.
BASE_CONFIG = {
    "hide_mode": "none",
    "icon_size": 48,
    "zoom_enabled": False,
    "theme": "default",
    "pinned": STATIC_PINNED,
    "recent_apps": [],
    "show_window_count_numbers": False,
    "previews_enabled": False,
    "active_display": False,
}


def _placement_cases() -> list[Case]:
    base = BASE_CONFIG
    # Every placement case requires the compositor to be able to position a
    # surface at all. Where it cannot -- Cinnamon without layer-shell (#347), or
    # a kiosk compositor like cage -- the case reports explicitly-unsupported
    # with the adapter's stated reason, rather than failing as a mystery or
    # quietly disappearing.
    requires = "placement"
    return [
        Case(
            "placement-bottom",
            "placement",
            {**base, "position": "bottom"},
            "bottom",
            requires=requires,
        ),
        Case(
            "placement-top",
            "placement",
            {**base, "position": "top"},
            "top",
            requires=requires,
        ),
        Case(
            "placement-left",
            "placement",
            {**base, "position": "left"},
            "left",
            requires=requires,
        ),
        Case(
            "placement-right",
            "placement",
            {**base, "position": "right"},
            "right",
            requires=requires,
        ),
        Case(
            "placement-bottom-gap",
            "placement",
            {**base, "position": "bottom", "additional_distance_from_edge": 40},
            "bottom",
            gap=40,
            pair_with="placement-bottom",
            requires=requires,
        ),
        Case(
            # The dock and a panel both claim the bottom edge. The panel is a
            # real swaybar with a genuine exclusive zone, so the compositor
            # shrinks the usable area; the dock must place itself inside what is
            # left. A dock that only knows its own geometry lands on top of the
            # panel instead.
            "placement-bottom-panel",
            "placement",
            {**base, "position": "bottom"},
            "bottom",
            panel=40,
            requires="panel",
        ),
    ]


def _monitor_cases() -> list[Case]:
    """Which output the dock lands on, with more than one configured.

    These need a run with LAB_OUTPUTS>=2; the adapter reports `multi_output`
    accordingly and the cases report explicitly-unsupported without it.
    """
    base = BASE_CONFIG
    return [
        Case(
            "monitor-primary",
            "monitors",
            {**base, "position": "bottom", "monitor_index": -1},
            "bottom",
            output_index=0,
            requires="multi_output",
        ),
        Case(
            "monitor-second-by-index",
            "monitors",
            {**base, "position": "bottom", "monitor_index": 1},
            "bottom",
            output_index=1,
            requires="multi_output",
        ),
        Case(
            # Deliberately names the NON-primary output. Naming the primary
            # would be non-discriminating: "connector honoured" and "fell back
            # to the primary" produce the same placement, so the case could not
            # tell the documented feature from a dead code path.
            "monitor-by-connector-nonprimary",
            "monitors",
            {
                **base,
                "position": "bottom",
                "monitor_connector": "HEADLESS-1",
                "monitor_index": -1,
            },
            "bottom",
            output_name="HEADLESS-1",
            requires="multi_output",
        ),
    ]


PLACEMENT_CASES = _placement_cases()
MONITOR_CASES = _monitor_cases()
VISIBILITY_CASES = [
    Case(
        f"autohide-{edge}",
        "visibility",
        {
            **BASE_CONFIG,
            "position": edge,
            "hide_mode": "autohide",
            "tooltips_enabled": False,
        },
        edge,
        requires="interactive",
        action="autohide",
    )
    for edge in ("bottom", "top", "left", "right")
] + [
    Case(
        "dodge-active",
        "visibility",
        {**BASE_CONFIG, "hide_mode": "dodge-active"},
        requires="dodge",
        action="dodge",
    ),
]
INTERACTION_CASES = [
    Case(
        "hover-zoom",
        "interaction",
        {**BASE_CONFIG, "zoom_enabled": True},
        requires="interactive",
        action="zoom",
    ),
    Case(
        "hover-tooltip",
        "interaction",
        BASE_CONFIG,
        requires="interactive",
        action="tooltip",
    ),
    Case(
        "context-menu",
        "interaction",
        BASE_CONFIG,
        requires="interactive",
        action="menu",
    ),
]
LAYOUT_CASES = [
    Case(
        "layout-narrow",
        "layouts",
        BASE_CONFIG,
        requires="output_changes",
        display_scene="narrow",
    ),
    Case(
        "layout-many-icons",
        "layouts",
        {**BASE_CONFIG, "pinned": [f"lab-item-{i:02}.desktop" for i in range(32)]},
        requires="output_changes",
        display_scene="narrow",
    ),
    Case(
        "layout-scale-2",
        "layouts",
        BASE_CONFIG,
        requires="output_changes",
        display_scene="scale2",
    ),
    Case(
        "layout-mixed-scale",
        "layouts",
        {**BASE_CONFIG, "monitor_index": 0},
        output_name="HEADLESS-1",
        requires="output_changes",
        display_scene="mixed",
    ),
    Case(
        "layout-rotated",
        "layouts",
        BASE_CONFIG,
        requires="output_changes",
        display_scene="rotated",
    ),
    Case(
        "layout-negative-origin",
        "layouts",
        {**BASE_CONFIG, "monitor_index": 1},
        output_name="HEADLESS-2",
        requires="output_changes",
        display_scene="negative",
    ),
]
DISPLAY_CASES = [
    Case(
        "output-resolution-change",
        "displays",
        BASE_CONFIG,
        requires="output_changes",
        display_change="resolution",
    ),
    Case(
        "output-scale-change",
        "displays",
        BASE_CONFIG,
        requires="output_changes",
        display_change="scale",
    ),
    Case(
        "output-removal",
        "displays",
        {**BASE_CONFIG, "monitor_index": 1},
        output_name="HEADLESS-1",
        requires="output_changes",
        display_scene="dual",
        display_change="remove",
    ),
]
RESERVATION_CASES = [
    Case(
        f"reservation-{edge}",
        "reservation",
        {**BASE_CONFIG, "position": edge},
        edge,
        requires="reservation_probe",
        maximize_window=True,
    )
    for edge in ("bottom", "top", "left", "right")
]
RESERVATION_CASES.append(
    Case(
        "reservation-bottom-gap",
        "reservation",
        {**BASE_CONFIG, "position": "bottom", "additional_distance_from_edge": 40},
        "bottom",
        gap=40,
        pair_with="reservation-bottom",
        requires="reservation_probe",
        maximize_window=True,
    )
)
RESERVATION_CASES.append(
    Case(
        "reservation-bottom-crash",
        "reservation",
        {**BASE_CONFIG, "position": "bottom"},
        "bottom",
        requires="reservation_probe",
        maximize_window=True,
        crash_dock=True,
    )
)
ALL_CASES = (
    PLACEMENT_CASES
    + MONITOR_CASES
    + VISIBILITY_CASES
    + RESERVATION_CASES
    + INTERACTION_CASES
    + LAYOUT_CASES
    + DISPLAY_CASES
)

BEHAVIORS = {
    "placement": PLACEMENT_CASES,
    "monitors": MONITOR_CASES,
    "visibility": VISIBILITY_CASES,
    "reservation": RESERVATION_CASES,
    "interaction": INTERACTION_CASES,
    "layouts": LAYOUT_CASES,
    "displays": DISPLAY_CASES,
}


def select_cases(*, behavior: str | None, only: list[str] | None) -> list[Case]:
    """Return the cases matching the requested behavior and/or names.

    An unknown behavior is an error, not a silent fallback to everything: a
    misspelling would otherwise run a different set than the one asked for and
    still report success. An empty selection is likewise an error -- reporting
    "0/0 passed" is a green light for having tested nothing.
    """
    if behavior is not None and behavior not in BEHAVIORS:
        known = ", ".join(sorted(BEHAVIORS))
        raise SystemExit(f"unknown behavior {behavior!r}; known behaviors: {known}")

    cases = BEHAVIORS[behavior] if behavior else ALL_CASES
    if only:
        wanted = set(only)
        unknown = wanted - {case.name for case in ALL_CASES}
        if unknown:
            raise SystemExit(f"unknown case(s): {sorted(unknown)}")

        # A paired case's assertion lives in the *comparison* against its
        # counterpart, so selecting it alone would silently skip the very check
        # it exists for -- a dock with no gap at all would pass. Pull the
        # counterpart in automatically rather than running a test that cannot
        # fail.
        by_name = {case.name: case for case in ALL_CASES}
        for name in list(wanted):
            counterpart = by_name[name].pair_with
            if counterpart and counterpart not in wanted:
                wanted.add(counterpart)
                print(
                    f"note: also running {counterpart!r} to evaluate the "
                    f"paired assertion for {name!r}"
                )

        cases = [case for case in cases if case.name in wanted]

    if not cases:
        detail = f"behavior={behavior!r}" if behavior else "all cases"
        raise SystemExit(
            f"no cases selected for {detail}; refusing to report success "
            "for an empty run"
        )
    return list(cases)


def emit_cases(path: Path, cases: list[Case]) -> None:
    """Write the container-facing case list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "name": case.name,
            "overrides": case.overrides,
            "edge": case.edge,
            "output_name": case.output_name,
            "maximize_window": case.maximize_window,
            "crash_dock": case.crash_dock,
            "action": case.action,
            "requires": case.requires,
            "display_scene": case.display_scene,
            "display_change": case.display_change,
        }
        for case in cases
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def expected_edge_coordinate(
    *, edge: str, output: dict, gap: int, band: int, panel: int = 0
) -> dict:
    """Derive the band of the output the dock is expected to occupy.

    Returns the crop rectangle (in screenshot pixels), the axis to measure, the
    coordinate that must be touched, and which end of the content's bounding box
    that coordinate corresponds to. The expected rect is *derived* from the
    output geometry and the case config, never from what Docking reports:
    cropping the region the dock actually moved to would move with the bug and
    hide it.

    ``end`` matters: a dock on the bottom or right edge is anchored by the far
    side of its bounding box (max), while one on the top or left edge is
    anchored by the near side (min).
    """
    x, y = output["x"], output["y"]
    width, height = output["width"], output["height"]
    band = min(band, height if edge in ("top", "bottom") else width)

    # A panel reserving the same edge moves the usable boundary inward.
    inset = panel + gap

    if edge == "bottom":
        rect = {"x": x, "y": y + height - band, "width": width, "height": band}
        return {"rect": rect, "axis": "y", "end": "max", "limit": y + height - inset}
    if edge == "top":
        rect = {"x": x, "y": y, "width": width, "height": band}
        return {"rect": rect, "axis": "y", "end": "min", "limit": y + inset}
    if edge == "left":
        rect = {"x": x, "y": y, "width": band, "height": height}
        return {"rect": rect, "axis": "x", "end": "min", "limit": x + inset}
    if edge == "right":
        rect = {"x": x + width - band, "y": y, "width": band, "height": height}
        return {"rect": rect, "axis": "x", "end": "max", "limit": x + width - inset}
    raise SystemExit(f"unknown edge: {edge}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit", type=Path, help="write cases.json for the container")
    parser.add_argument("--behavior", help="restrict to one behavior group")
    parser.add_argument(
        "--case", action="append", dest="only", help="restrict to named cases"
    )
    args = parser.parse_args()

    cases = select_cases(behavior=args.behavior, only=args.only)
    if args.emit:
        emit_cases(args.emit, cases)
        print(f"wrote {len(cases)} case(s) to {args.emit}")
    else:
        for case in cases:
            print(f"{case.behavior:12} {case.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
