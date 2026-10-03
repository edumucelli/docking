"""Host-side assertions for the visual compositor lab.

Two layers:

1. **Geometry** -- locate the dock's rendered content in the screenshot and
   assert it hugs the edge its configuration asked for. This is the layer that
   catches the #347/#349 class, where the dock rendered somewhere else entirely.
2. **Pixels** -- crop the band at the *expected* edge and SSIM/PSNR it against a
   committed per-compositor baseline. Cropping the expected band (not the
   measured rect) is deliberate: a crop that follows the dock would follow the
   bug and hide it.

Results use three states -- pass / fail / explicitly-unsupported -- so a
compositor that genuinely cannot run a scenario does not silently skip it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.visual.support import (
    VisualThresholds,
    compute_metrics,
    diff_image,
)

# Live captures are not bit-identical the way in-process Cairo renders are:
# rasteriser anti-aliasing, scale rounding and the compositor's own compositing
# all vary. These start looser than support.py's 0.995/35.0 defaults, which are
# calibrated for deterministic surfaces, and should be tightened once the spread
# of repeated captures of the same case has been measured.
LIVE_THRESHOLDS = VisualThresholds(ssim_min=0.985, psnr_min=30.0)

# How far the dock's rendered content may sit from the exact screen edge. The
# dock surface is bottom-anchored but its *visible* shelf is inset by theme
# padding and animation headroom, so an exact match is not expected. This
# tolerance cleanly separates "hugging the edge" from the ~500px displacement a
# misplacement bug produces.
EDGE_TOLERANCE_PX = 60

# Channel difference from the background above which a pixel counts as content.
BACKGROUND_TOLERANCE = 24

BASELINE_DIR = Path(__file__).resolve().parent / "baselines"


# How closely a paired gap case must move relative to its zero-gap counterpart.
# This is what actually verifies a gap: the absolute distance from the screen
# edge includes the shelf's constant inset within its surface, so a gap of 40px
# looks identical to a gap of 0px under any tolerance wider than the gap. The
# difference between the two captures cancels that inset.
RELATIVE_GAP_TOLERANCE_PX = 8

# Guard against measuring a scaled output with unscaled assumptions. Non-1 scale
# is reported as unsupported rather than silently mis-measured.
SUPPORTED_SCALE = 1

# How far a maximized window may stop short of the dock's visible content while
# still counting as "reserved exactly the dock". The assertion is deliberately
# two-sided: a reservation *smaller* than the dock lets a window overlap it, and
# a reservation *larger* than the dock silently wastes desktop space. Checking
# only the overlap direction would accept a dock that reserved its whole
# animation surface (163px) instead of its resting shelf (53px) -- a difference
# of ~110px that a one-sided check reports as a comfortable pass.
RESERVATION_SLACK_PX = 12
FRAME_TOLERANCE_PX = 8

# A case needing a capability the adapter does not provide. Reported as
# explicitly-unsupported, never as a pass and never silently dropped.
UNSUPPORTED = "unsupported"


@dataclass
class PendingBaseline:
    """A captured crop that may become a baseline, held until the run is clean."""

    crop: Image.Image
    case: object
    output: dict
    scene: dict


@dataclass
class Result:
    case: str
    status: str  # pass | fail | unsupported
    detail: str = ""
    measured_edge: int | None = None
    # The measured anchor coordinate (the side of the content that touches the
    # edge). Recorded so paired cases can be compared against each other.
    measured_anchor: int | None = None
    # Geometry outcome, tracked separately from the overall status: a pixel
    # baseline recorded from an already-wrong placement would otherwise mask the
    # geometry finding, which is the one that matters.
    geometry_ok: bool = False
    # Deferred baseline write. Nothing is committed to disk until every
    # assertion in the run has passed, so a failed run cannot leave behind
    # baselines recorded from a bad state.
    pending: PendingBaseline | None = None


def _background_colour(image: Image.Image) -> tuple[int, int, int]:
    """Most common colour along the image border, i.e. the empty desktop."""
    width, height = image.size
    samples = []
    for x in range(0, width, max(1, width // 64)):
        samples.append(image.getpixel((x, 0))[:3])
        samples.append(image.getpixel((x, height - 1))[:3])
    for y in range(0, height, max(1, height // 64)):
        samples.append(image.getpixel((0, y))[:3])
        samples.append(image.getpixel((width - 1, y))[:3])
    return Counter(samples).most_common(1)[0][0]


def locate_content(
    *, image: Image.Image, band: dict, background: tuple[int, int, int]
) -> tuple[int, int, int, int] | None:
    """Bounding box of non-background pixels inside ``band``, in image coords.

    Sway exposes no compositor-side rectangle for a layer surface, so this is
    the dock's position *measurement* -- independent of anything Docking
    reports about itself.
    """
    crop = image.crop(
        (band["x"], band["y"], band["x"] + band["width"], band["y"] + band["height"])
    ).convert("RGB")
    pixels = crop.load()
    width, height = crop.size

    min_x, min_y = width, height
    max_x = max_y = -1
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels[x, y]
            if (
                abs(red - background[0]) > BACKGROUND_TOLERANCE
                or abs(green - background[1]) > BACKGROUND_TOLERANCE
                or abs(blue - background[2]) > BACKGROUND_TOLERANCE
            ):
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    # Back to absolute image coordinates.
    return (
        band["x"] + min_x,
        band["y"] + min_y,
        band["x"] + max_x,
        band["y"] + max_y,
    )


def parse_self_reported(raw: str | None) -> tuple[int, int] | None:
    """Pull (x, y) out of a gdbus GetHoverAnchor reply.

    The reply is a GVariant tuple string like ``(true, 640, 700, 'bottom')``.
    Returns None when the dock did not report a usable position, which is
    itself meaningful -- `false` means it could not answer.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("("):
        text = text[1:]
    if text.endswith(")"):
        text = text[:-1]
    parts = [part.strip().strip("'") for part in text.split(",")]
    if len(parts) < 3 or parts[0].lower() != "true":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def check_self_report(
    *, reported: tuple[int, int] | None, bbox: tuple[int, int, int, int], tolerance: int
) -> tuple[bool, str]:
    """Assert Docking's own position lands inside its rendered content.

    The anchor it reports is a point on one of its items, so if the dock is
    telling the truth that point is inside the box the dock actually occupies.
    A dock reporting a position it never reached -- the #347 shape -- puts the
    point somewhere else entirely, which is what this catches.
    """
    if reported is None:
        return True, "self-report: not available"
    left, top, right, bottom = bbox
    x, y = reported
    inside = (
        left - tolerance <= x <= right + tolerance
        and top - tolerance <= y <= bottom + tolerance
    )
    if inside:
        return True, f"self-report ({x},{y}) inside rendered content"
    return False, (
        f"self-report ({x},{y}) is outside the rendered content "
        f"{bbox} -- the dock reports a position it is not at"
    )


def _overflow_amount(bbox: tuple[int, int, int, int], output: dict) -> int:
    """How far the content extends past the output, in pixels (0 if inside)."""
    left, top, right, bottom = bbox
    x, y = output["x"], output["y"]
    width, height = output["width"], output["height"]
    return max(
        x - left,
        y - top,
        right - (x + width - 1),
        bottom - (y + height - 1),
        0,
    )


def check_native_frame(*, frame: dict | None, output: dict) -> tuple[bool, str]:
    """Check the compositor's complete frame, including pixels clipped by capture."""
    if not frame:
        return False, "no compositor-reported dock frame"
    try:
        x, y, width, height = (frame[key] for key in ("x", "y", "width", "height"))
        if width <= 0 or height <= 0:
            return False, "compositor reported an empty dock frame"
        overflow = _overflow_amount((x, y, x + width - 1, y + height - 1), output)
    except (KeyError, TypeError):
        return False, "invalid compositor-reported dock frame"
    if overflow > FRAME_TOLERANCE_PX:
        return False, f"compositor dock frame extends {overflow}px beyond the output"
    return True, "compositor dock frame fits the output"


def check_output_transition(*, evidence: Path, case, record: dict) -> str | None:
    from tools.visual_compositor.behaviour import footprint
    from tools.visual_compositor.scenarios import EDGE_BAND_PX, expected_edge_coordinate

    before = _load_json(evidence / f"{case.name}.before-change.json")
    if not before or before.get("outputs") == record.get("outputs"):
        return "output change was not observed"
    if case.display_change != "remove":
        return None
    removed = next(
        (o for o in before["outputs"] if o.get("name") == "HEADLESS-2"), None
    )
    if not removed or any(o.get("name") == "HEADLESS-2" for o in record["outputs"]):
        return "target output was not removed"
    band = expected_edge_coordinate(
        edge=case.edge, output=removed, gap=case.gap, band=EDGE_BAND_PX
    )["rect"]
    origin = {key: min(o[key] for o in before["outputs"]) for key in ("x", "y")}
    band = {**band, "x": band["x"] - origin["x"], "y": band["y"] - origin["y"]}
    path = evidence / f"{case.name}.before-change.png"
    if not path.exists():
        return "missing pre-removal capture"
    with Image.open(path) as initial:
        initial = initial.convert("RGB")
        bbox = locate_content(
            image=initial, band=band, background=_background_colour(initial)
        )
    if bbox is None or footprint(path, band) < 1000:
        return "dock was never on the removed output"
    return None


def check_edge(
    *,
    bbox: tuple[int, int, int, int],
    axis: str,
    end: str,
    limit: int,
    tolerance: int,
) -> tuple[bool, int, str]:
    """Assert the content is anchored at the configured screen edge.

    ``bbox`` is (min_x, min_y, max_x, max_y). A dock on the bottom/right edge is
    anchored by the max side of its box; one on the top/left edge by the min
    side. Measuring the wrong side makes every correct top/left placement look
    like a failure.
    """
    if axis == "y":
        near, far = bbox[1], bbox[3]
    else:
        near, far = bbox[0], bbox[2]

    # Positive always means "away from the edge": either the content stops short
    # of it, or it starts too far inside.
    distance = limit - far if end == "max" else near - limit

    if distance < -tolerance:
        return False, distance, f"content overshoots the edge by {-distance}px"
    if distance > tolerance:
        return False, distance, f"content is {distance}px short of the edge"
    return True, distance, f"content within {distance}px of the edge"


def compare_band(
    *,
    image: Image.Image,
    band: dict,
    baseline_path: Path,
    output_path: Path,
    update: bool,
) -> tuple[bool, str, Image.Image | None]:
    """Crop the expected band and compare it against the compositor baseline.

    When ``update`` is set the crop is *returned*, not written: the caller
    commits it only after the whole run's assertions have passed, so a run that
    fails a geometry check cannot leave a baseline recorded from the bad state.
    """
    crop = image.crop(
        (band["x"], band["y"], band["x"] + band["width"], band["y"] + band["height"])
    ).convert("RGBA")

    if update:
        return True, "baseline pending (committed only if the run passes)", crop

    if not baseline_path.exists():
        return (
            False,
            (
                f"missing baseline {baseline_path}; "
                "run with --update-baselines once the case is known good"
            ),
            None,
        )

    expected = Image.open(baseline_path).convert("RGBA")
    if expected.size != crop.size:
        return (
            False,
            (
                f"baseline size {expected.size} != capture size {crop.size}; "
                "the output geometry changed, so the baseline must be regenerated"
            ),
            None,
        )

    metrics = compute_metrics(expected=expected, actual=crop)
    if (
        metrics.ssim >= LIVE_THRESHOLDS.ssim_min
        and metrics.psnr >= LIVE_THRESHOLDS.psnr_min
    ):
        return True, f"SSIM {metrics.ssim:.5f} PSNR {metrics.psnr:.2f}", None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(output_path.with_suffix(".actual.png"))
    expected.save(output_path.with_suffix(".expected.png"))
    diff = diff_image(expected=expected, actual=crop)
    diff.save(output_path.with_suffix(".diff.png"))
    return (
        False,
        (
            f"SSIM {metrics.ssim:.5f} < {LIVE_THRESHOLDS.ssim_min} or "
            f"PSNR {metrics.psnr:.2f} < {LIVE_THRESHOLDS.psnr_min}; "
            f"see {output_path.with_suffix('.diff.png').name}"
        ),
        None,
    )


def _anchor_coordinate(bbox: tuple[int, int, int, int], axis: str, end: str) -> int:
    """The side of the content that is anchored to the screen edge."""
    if axis == "y":
        near, far = bbox[1], bbox[3]
    else:
        near, far = bbox[0], bbox[2]
    return far if end == "max" else near


def check_relative_gap(
    *, gapped: int, base: int, expected_gap: int
) -> tuple[bool, str]:
    """Verify a configured gap by comparing a case against its counterpart.

    Both captures share the same unknown inset between the visible shelf and its
    surface, so the difference between their anchor coordinates is the applied
    gap exactly. This is the only assertion that can distinguish a 40px gap from
    none, since an absolute distance from the screen edge cannot.

    The comparison is *signed*: a gap lifts the dock away from the edge, so a
    movement of the right magnitude in the wrong direction is a failure, not a
    near miss.
    """
    # Positive when the gapped case sits further from the edge than the base.
    delta = base - gapped
    if abs(delta - expected_gap) > RELATIVE_GAP_TOLERANCE_PX:
        return False, (
            f"paired gap: counterpart moved {delta}px from the edge, "
            f"expected {expected_gap}px"
        )
    return True, f"paired gap: counterpart moved {delta}px as configured"


def evaluate(
    *,
    evidence_dir: Path,
    cases: list,
    out_dir: Path,
    update: bool,
    compositor: str,
    capabilities: dict,
    run_meta: dict,
    geometry_only: bool = False,
) -> list[Result]:
    from tools.visual_compositor.scenarios import EDGE_BAND_PX, expected_edge_coordinate

    expected_backend = capabilities.get("expected_backend")

    # If the session was not actually Wayland, every result below would be about
    # the wrong display server -- the classic way a Wayland test quietly becomes
    # an X11 test. The adapter probes this with the same environment the dock
    # runs in, so a False here invalidates the whole run rather than one case.
    if capabilities.get("gtk_display_is_wayland") is False:
        return [
            Result(
                case.name,
                "fail",
                "session did not provide a Wayland display; the dock would have "
                "run on X11 and these results would not describe Wayland",
            )
            for case in cases
        ]

    # Which cases this run was actually asked to perform. A case that was never
    # selected (a `--behavior placement` run, say) has no evidence by design and
    # must read as unsupported; only a case that *was* selected and produced
    # nothing is a failure. Without this the runner reports a page of failures
    # for the behaviours it deliberately did not run, which trains the reader to
    # ignore the failure column.
    selected: set[str] | None = None
    cases_json = evidence_dir / "cases.json"
    if cases_json.exists():
        try:
            selected = {entry["name"] for entry in json.loads(cases_json.read_text())}
        except (OSError, ValueError, KeyError, TypeError):
            selected = None

    results: list[Result] = []
    for case in cases:
        png_path = evidence_dir / f"{case.name}.png"
        json_path = evidence_dir / f"{case.name}.json"

        if selected is not None and case.name not in selected:
            results.append(Result(case.name, UNSUPPORTED, "not selected for this run"))
            continue

        # Lifecycle is required even when placement is unsupported (the Cage
        # compatibility lane). Never turn a recorded crash into an UNSUP result.
        if not json_path.exists():
            results.append(Result(case.name, "fail", "no session evidence produced"))
            continue
        try:
            record = json.loads(json_path.read_text())
        except (OSError, ValueError):
            results.append(Result(case.name, "fail", "invalid session evidence"))
            continue
        if not isinstance(record, dict):
            results.append(Result(case.name, "fail", "invalid session evidence"))
            continue
        if record.get("status") == "fail":
            results.append(
                Result(case.name, "fail", record.get("reason", "session failed"))
            )
            continue
        if record.get("started") is not True or record.get("stopped") is not True:
            results.append(
                Result(case.name, "fail", "startup/shutdown evidence missing")
            )
            continue
        if not expected_backend or not _backend_selected(
            evidence_dir, case.name, expected_backend
        ):
            results.append(
                Result(
                    case.name,
                    "fail",
                    f"expected backend {expected_backend!r} not verified",
                )
            )
            continue

        # A capability the adapter says it does not have. Report it as
        # unsupported with the adapter's own explanation when it gives one,
        # never as a pass and never as a silent skip -- an absent capability is
        # a result about the compositor, and the reason is the useful part.
        # Absent must count as unsupported, not as satisfied. `is False` alone
        # let a case run on an adapter that never declares the capability at all
        # -- which is how a panel case produced a false failure on a compositor
        # configured with no panel. A capability the adapter does not report is
        # not evidence that it is available.
        if case.requires and capabilities.get(case.requires) is not True:
            reason = (capabilities.get("unsupported_reasons") or {}).get(case.requires)
            results.append(
                Result(
                    case.name,
                    UNSUPPORTED,
                    (reason or f"{compositor} does not provide {case.requires!r}")
                    + "; startup, backend and shutdown verified",
                )
            )
            continue

        if not png_path.exists():
            results.append(Result(case.name, "fail", "no capture produced"))
            continue

        outputs = record.get("outputs") or []
        if not outputs:
            results.append(Result(case.name, "fail", "no output geometry reported"))
            continue
        # Identify the target output by name when the case gives one: compositor
        # enumeration order is not guaranteed to match connector numbering, so
        # an index can point at the wrong output and make a correct placement
        # look like a bug.
        if case.output_name:
            named = [o for o in outputs if o.get("name") == case.output_name]
            if not named:
                results.append(
                    Result(
                        case.name,
                        "fail",
                        f"case targets output {case.output_name!r}, which the "
                        f"compositor did not report "
                        f"(have: {[o.get('name') for o in outputs]})",
                    )
                )
                continue
            output = named[0]
        else:
            if case.output_index >= len(outputs):
                results.append(
                    Result(
                        case.name,
                        "fail",
                        f"case targets output {case.output_index} but only "
                        f"{len(outputs)} reported",
                    )
                )
                continue
            output = outputs[case.output_index]

        if output.get("scale", 1) != SUPPORTED_SCALE and not capabilities.get(
            "logical_screenshot"
        ):
            results.append(
                Result(
                    case.name,
                    UNSUPPORTED,
                    f"output scale {output.get('scale')} is not modelled "
                    f"(only {SUPPORTED_SCALE} is); measuring would be wrong",
                )
            )
            continue

        if not record.get("settled", True):
            results.append(
                Result(case.name, "fail", "capture never stabilised within budget")
            )
            continue

        usable_output = output
        if capabilities.get("compositor") == "cinnamon":
            # Exclude real Cinnamon panels from pixel localization, and measure
            # placement relative to the workarea before the dock reserves it.
            external = next(
                (
                    o
                    for o in record.get("external_outputs", [])
                    if o.get("name") == output.get("name")
                ),
                {},
            )
            if external.get("workarea"):
                usable_output = external["workarea"]
        band = expected_edge_coordinate(
            edge=case.edge,
            output=usable_output,
            gap=case.gap,
            band=EDGE_BAND_PX,
            panel=getattr(case, "panel", 0),
        )
        panel = run_meta.get("panel")
        if not isinstance(panel, dict) or not {"height", "position"} <= panel.keys():
            results.append(
                Result(
                    case.name,
                    "fail",
                    "panel configuration missing from run metadata; rerun the session",
                )
            )
            continue
        scene = {
            "panel": run_meta["panel"],
            "case_panel": case.panel,
            "workarea": {
                key: usable_output[key] for key in ("x", "y", "width", "height")
            },
            "crop": band["rect"],
        }
        if capabilities.get("native_geometry") is True:
            frame_ok, frame_detail = check_native_frame(
                frame=record.get("dock_rect"), output=output
            )
            if not frame_ok:
                results.append(Result(case.name, "fail", frame_detail))
                continue

        # Screenshots use canvas coordinates; outputs and D-Bus anchors use
        # compositor coordinates, which can start left/above zero.
        origin = record.get("capture_origin", {"x": 0, "y": 0})
        canvas_band = {
            **band["rect"],
            "x": band["rect"]["x"] - origin["x"],
            "y": band["rect"]["y"] - origin["y"],
        }
        if origin != {"x": 0, "y": 0}:
            scene["capture_origin"] = origin
        band = {
            **band,
            "rect": canvas_band,
            "limit": band["limit"] - origin[band["axis"]],
        }
        if case.action:
            from tools.visual_compositor.behaviour import check_action

            action_ok, action_detail = check_action(
                evidence=evidence_dir, case=case, band=canvas_band
            )
            if not action_ok:
                results.append(Result(case.name, "fail", action_detail))
                continue
        if case.display_change:
            transition_error = check_output_transition(
                evidence=evidence_dir, case=case, record=record
            )
            if transition_error:
                results.append(Result(case.name, "fail", transition_error))
                continue

        with Image.open(png_path) as image:
            image = image.convert("RGB")
            background = _background_colour(image)
            bbox = locate_content(image=image, band=band["rect"], background=background)

            if bbox is None:
                # Keep the full capture: when the dock is missing from its band
                # it rendered somewhere else, and this frame is the only record
                # of where it actually went.
                out_dir.mkdir(parents=True, exist_ok=True)
                image.save(out_dir / f"{case.name}.capture.png")
                results.append(
                    Result(
                        case.name,
                        "fail",
                        f"no dock content found in the {case.edge} band "
                        f"{band['rect']} (background={background})",
                    )
                )
                continue

            reported = parse_self_reported(record.get("self_reported_anchor"))
            if reported:
                reported = (reported[0] - origin["x"], reported[1] - origin["y"])
            reported_ok, reported_detail = check_self_report(
                reported=reported,
                bbox=bbox,
                tolerance=EDGE_TOLERANCE_PX,
            )
            if not reported_ok:
                out_dir.mkdir(parents=True, exist_ok=True)
                image.save(out_dir / f"{case.name}.capture.png")
                results.append(Result(case.name, "fail", reported_detail))
                continue

            if case.name == "layout-many-icons":
                last = parse_self_reported(record.get("last_item_anchor"))
                if last is None:
                    results.append(
                        Result(case.name, "fail", "last pinned item has no anchor")
                    )
                    continue
                last = (last[0] - origin["x"], last[1] - origin["y"])
                fits, reason = check_self_report(reported=last, bbox=bbox, tolerance=8)
                if not fits:
                    results.append(
                        Result(case.name, "fail", f"last pinned item clipped: {reason}")
                    )
                    continue

            anchor = _anchor_coordinate(bbox, band["axis"], band["end"])
            if getattr(case, "maximize_window", False):
                reservation_ok, reservation_detail = check_reservation(
                    record=record, bbox=bbox, edge=case.edge, output=output
                )
                if not reservation_ok:
                    results.append(Result(case.name, "fail", reservation_detail))
                    continue
                reported_detail = f"{reported_detail}; {reservation_detail}"
            # A panel or gap case asserts a *displacement*, so a tolerance wider
            # than the displacement would accept the behaviour it exists to
            # reject: a 40px panel against a 60px tolerance passes whether or not
            # the dock respected the panel. Scale the tolerance to the effect.
            effect = max(getattr(case, "panel", 0), case.gap)
            tolerance = (
                EDGE_TOLERANCE_PX
                if effect == 0
                else max(6, min(EDGE_TOLERANCE_PX, effect // 2))
            )
            ok, distance, detail = check_edge(
                bbox=bbox,
                axis=band["axis"],
                end=band["end"],
                limit=band["limit"],
                tolerance=tolerance,
            )
            if not ok:
                out_dir.mkdir(parents=True, exist_ok=True)
                image.save(out_dir / f"{case.name}.capture.png")
                results.append(Result(case.name, "fail", detail, distance, anchor))
                continue

            baseline_path = BASELINE_DIR / compositor / f"{case.name}.png"

            # A committed baseline is only meaningful for the image, output
            # geometry and case definition that produced it. Verify before
            # trusting it, and never accept a baseline with no provenance.
            if not update and not geometry_only and baseline_path.exists():
                untrusted = check_case_provenance(
                    compositor=compositor,
                    case=case,
                    output=output,
                    run_meta=run_meta,
                    scene=scene,
                )
                if untrusted:
                    results.append(
                        Result(
                            case.name,
                            "fail",
                            f"baseline provenance: {untrusted}",
                            distance,
                            anchor,
                            geometry_ok=True,
                        )
                    )
                    continue

            if geometry_only:
                pixels_ok, pixel_detail, pending_crop = (
                    True,
                    "geometry only; pixel baseline not compared",
                    None,
                )
            else:
                pixels_ok, pixel_detail, pending_crop = compare_band(
                    image=image,
                    band=band["rect"],
                    baseline_path=baseline_path,
                    output_path=out_dir / f"{case.name}.png",
                    update=update,
                )

        results.append(
            Result(
                case.name,
                "pass" if pixels_ok else "fail",
                f"{detail}; {reported_detail}; {pixel_detail}",
                distance,
                anchor,
                geometry_ok=True,
                pending=(
                    PendingBaseline(
                        crop=pending_crop, case=case, output=output, scene=scene
                    )
                    if pending_crop is not None
                    else None
                ),
            )
        )

    _apply_paired_gap_checks(cases=cases, results=results)
    return results


def check_reservation(
    *, record: dict, bbox: tuple, edge: str, output: dict
) -> tuple[bool, str]:
    """Independent oracle: Muffin's maximized frame must avoid visible dock pixels."""
    evidence = record.get("reservation") or {}
    maximized = (evidence.get("maximized") or {}).get("window")
    released = (evidence.get("released") or {}).get("window")
    if not maximized or not maximized.get("maximized") or not released:
        return False, "no settled maximized-window evidence"
    x, y, w, h = (maximized[k] for k in ("x", "y", "width", "height"))
    clearance = {
        "bottom": bbox[1] - (y + h),
        "top": y - (bbox[3] + 1),
        "left": x - (bbox[2] + 1),
        "right": bbox[0] - (x + w),
    }[edge]
    # Shadows and antialiasing extend a few pixels beyond the reserved shelf.
    if clearance < -8:
        return False, f"maximized window overlaps visible {edge} dock by {-clearance}px"
    if clearance > RESERVATION_SLACK_PX:
        over = (
            f"reserved space exceeds the {edge} dock: maximized window stops "
            f"{clearance}px short of it (slack {RESERVATION_SLACK_PX}px)"
        )
        return False, over
    index = next((i for i, o in enumerate(record["outputs"]) if o == output), 0)
    before_outputs = (evidence.get("before") or {}).get("outputs") or []
    released_outputs = (evidence.get("released") or {}).get("outputs") or []
    if index >= len(before_outputs) or index >= len(released_outputs):
        return False, "missing workarea evidence for reservation release"
    before = before_outputs[index].get("workarea")
    after = released_outputs[index].get("workarea")
    if not before or after != before:
        return (
            False,
            f"dock reservation was not released: before={before}, after={after}",
        )
    if any(abs(released[k] - before[k]) > 1 for k in ("x", "y", "width", "height")):
        return False, "maximized window did not regain the released workarea"
    return True, f"maximized window clears dock by {clearance}px; reservation released"


def _apply_paired_gap_checks(*, cases: list, results: list[Result]) -> None:
    """Run the relative gap assertion for each case that declares a pair."""
    by_name = {result.case: result for result in results}
    for case in cases:
        if not case.pair_with or not case.gap:
            continue
        result = by_name.get(case.name)
        counterpart = by_name.get(case.pair_with)
        if result is None or counterpart is None:
            continue
        if not result.geometry_ok or not counterpart.geometry_ok:
            continue
        if result.measured_anchor is None or counterpart.measured_anchor is None:
            continue
        ok, detail = check_relative_gap(
            gapped=result.measured_anchor,
            base=counterpart.measured_anchor,
            expected_gap=case.gap,
        )
        result.detail = f"{result.detail}; {detail}"
        if not ok:
            result.status = "fail"


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _backend_selected(
    evidence_dir: Path, case_name: str, expected_backend: str
) -> bool:
    """Verify backend selection independently of geometry support."""
    path = evidence_dir / f"{case_name}.log"
    return (
        path.exists()
        and f"Selected session backend: {expected_backend} ("
        in path.read_text(errors="replace")
    )


def baseline_provenance_path(*, compositor: str, case_name: str) -> Path:
    """Per-case sidecar.

    Deliberately per case rather than one manifest for the directory: refreshing
    a single case would otherwise rewrite a shared manifest and relabel every
    untouched baseline as belonging to the new image.
    """
    return BASELINE_DIR / compositor / f"{case_name}.provenance.json"


def commit_baseline(
    *, pending: PendingBaseline, compositor: str, run_meta: dict
) -> Path:
    """Write a baseline PNG and its provenance sidecar."""
    case = pending.case
    path = BASELINE_DIR / compositor / f"{case.name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending.crop.save(path)
    output_keys = ("name", "x", "y", "width", "height", "scale")
    sidecar = {
        "compositor": compositor,
        "case": case.name,
        "image_id": run_meta.get("image_id"),
        "image_ref": run_meta.get("image_ref"),
        "output": {key: pending.output.get(key) for key in output_keys},
        "edge": case.edge,
        "gap": case.gap,
        "config": case.overrides,
        "scene": pending.scene,
    }
    baseline_provenance_path(compositor=compositor, case_name=case.name).write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def check_case_provenance(
    *, compositor: str, case: object, output: dict, run_meta: dict, scene: dict
) -> str | None:
    """Return a reason this case's baseline cannot be trusted, or None."""
    path = baseline_provenance_path(compositor=compositor, case_name=case.name)
    if not path.exists():
        return f"{path.name} is missing, so the baseline has no recorded provenance"

    data = _load_json(path)
    if not data:
        return f"{path.name} is empty or unreadable"
    if data.get("scene") != scene:
        return (
            "baseline scene differs or is missing (panel, workarea or crop); "
            "regenerate the baseline"
        )

    recorded_image = data.get("image_id")
    current_image = run_meta.get("image_id")
    if not recorded_image:
        return f"{path.name} records no image identity"
    if not current_image:
        return "this run recorded no image identity to check the baseline against"
    if recorded_image != current_image:
        return (
            f"baseline recorded against image {recorded_image[:12]}, "
            f"this run uses {current_image[:12]}"
        )

    # Output geometry decides the crop, so every field is required. A missing
    # field must fail rather than be skipped: absent metadata is not agreement.
    recorded_output = data.get("output")
    if not isinstance(recorded_output, dict):
        return f"{path.name} records no output geometry"
    for key in ("x", "y", "width", "height", "scale"):
        if key not in recorded_output:
            return f"{path.name} does not record output {key}"
        if recorded_output[key] != output.get(key):
            return (
                f"baseline recorded at output {key}={recorded_output[key]}, "
                f"observed {output.get(key)}"
            )

    if data.get("edge") != case.edge or data.get("gap") != case.gap:
        return (
            f"baseline recorded for edge={data.get('edge')!r} gap={data.get('gap')!r}, "
            f"case is edge={case.edge!r} gap={case.gap!r}"
        )

    # The configuration changes what is rendered, so a baseline recorded under
    # different settings -- a theme, icon size, zoom -- is not a valid reference
    # for this case even when the geometry is unchanged.
    recorded_config = data.get("config")
    if not isinstance(recorded_config, dict):
        return f"{path.name} records no case configuration"
    if recorded_config != case.overrides:
        changed = sorted(
            key
            for key in set(recorded_config) | set(case.overrides)
            if recorded_config.get(key) != case.overrides.get(key)
        )
        return (
            "case configuration changed since the baseline was recorded "
            f"({', '.join(changed)})"
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compositor", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--behavior")
    parser.add_argument("--case", action="append", dest="only")
    parser.add_argument("--update-baselines", action="store_true")
    parser.add_argument("--geometry-only", action="store_true")
    parser.add_argument("--require-supported", action="store_true")
    args = parser.parse_args()
    if args.geometry_only and args.update_baselines:
        parser.error("--geometry-only cannot update pixel baselines")

    from tools.visual_compositor.scenarios import select_cases

    cases = select_cases(behavior=args.behavior, only=args.only)
    args.out.mkdir(parents=True, exist_ok=True)

    capabilities = _load_json(args.evidence / "capabilities.json") or {}
    run_meta = _load_json(args.evidence / "run-meta.json") or {}

    results = evaluate(
        evidence_dir=args.evidence,
        cases=cases,
        out_dir=args.out,
        update=args.update_baselines,
        compositor=args.compositor,
        capabilities=capabilities,
        run_meta=run_meta,
        geometry_only=args.geometry_only,
    )

    if not results:
        # select_cases already rejects an empty matrix, so reaching here means
        # something upstream went wrong. Never report success for zero checks.
        print("no cases were evaluated; refusing to report success", file=sys.stderr)
        return 2

    failed = [result for result in results if result.status == "fail"]

    # Baselines are committed only after every assertion in the run has passed.
    # Writing them as each case is evaluated would let a run that later fails a
    # geometry check leave behind baselines recorded from the bad state.
    if args.update_baselines:
        if failed:
            print(
                f"{len(failed)} case(s) failed; no baselines written. "
                "Fix the failures, then re-run with --update-baselines.",
                file=sys.stderr,
            )
        else:
            for result in results:
                if result.pending is not None:
                    committed = commit_baseline(
                        pending=result.pending,
                        compositor=args.compositor,
                        run_meta=run_meta,
                    )
                    result.detail = f"{result.detail}; baseline -> {committed.name}"

    width = max((len(r.case) for r in results), default=4)
    failures = 0
    unsupported = 0
    for result in results:
        marker = {"pass": "PASS", "fail": "FAIL", "unsupported": "UNSUP"}[result.status]
        if result.status == "fail":
            failures += 1
        elif result.status == UNSUPPORTED:
            unsupported += 1
        print(f"{marker:5} {result.case:<{width}}  {result.detail}")

    summary = f"{len(results) - failures - unsupported}/{len(results)} passed"
    if unsupported:
        summary += f", {unsupported} explicitly unsupported"
    print(f"\n{summary} on {args.compositor}")
    return 1 if failures or (args.require_supported and unsupported) else 0


if __name__ == "__main__":
    raise SystemExit(main())
