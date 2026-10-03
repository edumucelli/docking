"""Regression tests for the compositor harness's independent assertions."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from tools.visual_compositor import compare, scenarios


@pytest.mark.parametrize(
    ("states", "tracked", "expected"),
    [
        ([False, True, False], True, True),
        ([False, True, False], False, False),
        ([False, False, False], True, False),
        ([False, True, True], True, False),
        ([True, True, False], True, False),
    ],
)
def test_native_window_lifecycle_requires_compositor_and_app_evidence(
    tmp_path, states, tracked, expected
):
    from tools.visual_compositor.behaviour import check_action

    case = scenarios.WINDOW_CASES[0]
    for phase, present in zip(("before", "opened", "closed"), states, strict=True):
        windows = [{"title": "Lab probe"}] if present else []
        (tmp_path / f"{case.name}.{phase}.windows.json").write_text(json.dumps(windows))
    (tmp_path / f"{case.name}.opened.items.txt").write_text(
        "(['lab-probe.desktop'],)" if tracked else "(@as [],)"
    )
    assert check_action(evidence=tmp_path, case=case, band={})[0] is expected


@pytest.mark.parametrize("corrupt", ["missing", "[]", "(3,)", "not a variant"])
def test_native_window_lifecycle_rejects_missing_or_invalid_evidence(tmp_path, corrupt):
    from tools.visual_compositor.behaviour import check_action

    case = scenarios.WINDOW_CASES[0]
    for phase, windows in (
        ("before", []),
        ("opened", [{"title": "Lab probe"}]),
        ("closed", []),
    ):
        (tmp_path / f"{case.name}.{phase}.windows.json").write_text(json.dumps(windows))
    if corrupt != "missing":
        (tmp_path / f"{case.name}.opened.items.txt").write_text(corrupt)
    assert not check_action(evidence=tmp_path, case=case, band={})[0]


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    case = scenarios.PLACEMENT_CASES[0]
    output = {"name": "OUT-1", "x": 0, "y": 0, "width": 1280, "height": 720, "scale": 1}
    record = {
        "started": True,
        "stopped": True,
        "settled": True,
        "outputs": [output],
        "self_reported_anchor": "(true, 640, 700, 'bottom')",
        "dock_rect": {"x": 0, "y": 517, "width": 1280, "height": 163},
    }
    caps = {
        "compositor": "cinnamon",
        "expected_backend": "cinnamon-shell",
        "gtk_display_is_wayland": True,
        "placement": True,
        "native_geometry": True,
    }
    meta = {"image_id": "review-image", "panel": {"height": 0, "position": "bottom"}}
    image = Image.new("RGB", (1280, 720), "black")
    ImageDraw.Draw(image).rectangle((400, 660, 879, 719), fill="white")
    image.save(tmp_path / f"{case.name}.png")
    (tmp_path / f"{case.name}.log").write_text(
        "Selected session backend: cinnamon-shell (test)\n"
    )
    monkeypatch.setattr(compare, "BASELINE_DIR", tmp_path / "baselines")

    def evaluate(*, update=True, geometry_only=False):
        (tmp_path / f"{case.name}.json").write_text(json.dumps(record))
        return compare.evaluate(
            evidence_dir=tmp_path,
            cases=[case],
            out_dir=tmp_path / "diffs",
            update=update,
            compositor="cinnamon",
            capabilities=caps,
            run_meta=meta,
            geometry_only=geometry_only,
        )[0]

    return SimpleNamespace(
        case=case,
        record=record,
        caps=caps,
        meta=meta,
        path=tmp_path,
        evaluate=evaluate,
    )


@pytest.mark.parametrize(
    "failure",
    [
        "startup",
        "shutdown",
        "missing-start",
        "missing-stop",
        "backend",
        "backend-prefix",
        "missing-log",
    ],
)
def test_unsupported_placement_does_not_hide_session_failures(evidence, failure):
    evidence.caps["placement"] = False
    if failure in {"startup", "shutdown"}:
        evidence.record.update(status="fail", reason=f"{failure} failed")
    elif failure == "missing-start":
        evidence.record.pop("started")
    elif failure == "missing-stop":
        evidence.record.pop("stopped")
    elif failure == "backend":
        evidence.caps["expected_backend"] = "reduced"
    elif failure == "backend-prefix":
        evidence.caps["expected_backend"] = "cinnamon"
    else:
        (evidence.path / f"{evidence.case.name}.log").unlink()
    assert evidence.evaluate().status == "fail"


def test_negative_compatibility_checks_lifecycle_without_requiring_capture(evidence):
    evidence.caps["placement"] = False
    (evidence.path / f"{evidence.case.name}.png").unlink()
    result = evidence.evaluate()
    assert result.status == "unsupported"
    assert "startup, backend and shutdown verified" in result.detail


def test_cli_returns_failure_for_crash_on_unsupported_lane(evidence, monkeypatch):
    evidence.caps["placement"] = False
    evidence.record.update(status="fail", reason="docking did not start")
    evidence.evaluate()
    (evidence.path / "capabilities.json").write_text(json.dumps(evidence.caps))
    (evidence.path / "run-meta.json").write_text(json.dumps(evidence.meta))
    monkeypatch.setattr(scenarios, "select_cases", lambda **kwargs: [evidence.case])
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare",
            "--compositor",
            "cinnamon",
            "--evidence",
            str(evidence.path),
            "--out",
            str(evidence.path / "diffs"),
        ],
    )
    assert compare.main() == 1


@pytest.mark.parametrize(
    "frame",
    [
        None,
        {"x": 0, "y": 517, "width": 5000, "height": 163},
        {"x": -100, "y": 517, "width": 1280, "height": 163},
        {"x": 0, "y": 700, "width": 1280, "height": 163},
        {"x": 0, "y": 517, "width": 0, "height": 163},
    ],
)
def test_native_frame_rejects_clipping_even_when_visible_pixels_match(evidence, frame):
    evidence.record["dock_rect"] = frame
    result = evidence.evaluate()
    assert result.status == "fail"
    assert "frame" in result.detail
    assert result.pending is None


def test_native_frame_accepts_animation_headroom_and_valid_pixel_baseline(evidence):
    result = evidence.evaluate()
    assert result.status == "pass"
    assert result.pending is not None
    compare.commit_baseline(
        pending=result.pending, compositor="cinnamon", run_meta=evidence.meta
    )
    assert evidence.evaluate(update=False).status == "pass"


@pytest.mark.parametrize(
    "changed",
    ["panel-height", "panel-position", "workarea", "crop", "case-panel", "legacy"],
)
def test_baseline_requires_matching_scene_even_when_pixels_match(evidence, changed):
    result = evidence.evaluate()
    compare.commit_baseline(
        pending=result.pending, compositor="cinnamon", run_meta=evidence.meta
    )
    sidecar = compare.baseline_provenance_path(
        compositor="cinnamon", case_name=evidence.case.name
    )
    data = json.loads(sidecar.read_text())
    if changed == "legacy":
        data.pop("scene")
    elif changed == "panel-height":
        evidence.meta["panel"]["height"] = 40
    elif changed == "panel-position":
        evidence.meta["panel"]["position"] = "top"
    elif changed == "workarea":
        data["scene"]["workarea"]["height"] -= 40
    elif changed == "crop":
        data["scene"]["crop"]["y"] -= 40
    else:
        data["scene"]["case_panel"] = 40
    sidecar.write_text(json.dumps(data))
    result = evidence.evaluate(update=False)
    assert result.status == "fail"
    assert "baseline scene" in result.detail


@pytest.mark.parametrize("panel", [None, {}, {"height": 40}])
def test_missing_panel_metadata_cannot_produce_a_trusted_baseline(evidence, panel):
    evidence.meta["panel"] = panel
    result = evidence.evaluate()
    assert result.status == "fail"
    assert result.pending is None


def test_sway_selects_one_panel_implementation(tmp_path):
    scripts = compare.REPO_ROOT / "tools" / "visual_compositor" / "adapters"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            """
set -euo pipefail
export XDG_RUNTIME_DIR="$1" XDG_CONFIG_HOME="$1/config" LAB_DIR="$1"
export LAB_PANEL_HEIGHT=40 LAB_PANEL_POSITION=bottom
source "$2/common.sh"
source "$2/sway.sh"
adapter_prepare
start_lab_panel
test -z "$ADAPTER_PANEL_PID"
grep -q 'height 40' "$SWAY_CONFIG"
""",
            "review",
            str(tmp_path),
            str(scripts),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="adapter probe requires jq")
@pytest.mark.parametrize("window_tracking", [False, True])
def test_cosmic_probe_matches_the_production_backend(tmp_path, window_tracking):
    from docking.platform.backends.wayland.cosmic_session import CosmicSessionBackend

    scripts = compare.REPO_ROOT / "tools" / "visual_compositor" / "adapters"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            """
set -euo pipefail
export LAB_DIR="$1" XDG_CONFIG_HOME="$1/config" LAB_WINDOW_PROBE="$3"
source "$2/common.sh"
source "$2/cosmic.sh"
session_probe_json() { echo '{"layer_shell_supported":true,"gtk_display_is_wayland":true}'; }
adapter_windows() { "$LAB_WINDOW_PROBE"; }
adapter_prepare
adapter_capabilities
""",
            "review",
            str(tmp_path),
            str(scripts),
            "true" if window_tracking else "false",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    caps = json.loads(completed.stdout)
    backend = CosmicSessionBackend.__new__(CosmicSessionBackend)
    assert caps["expected_backend"] == backend.name
    assert caps["window_tracking"] is window_tracking
    assert caps["window_actions"] is window_tracking
    assert caps["workspace_switch"] is window_tracking


@pytest.mark.skipif(shutil.which("jq") is None, reason="adapter probe requires jq")
def test_niri_does_not_claim_a_native_dock_frame(tmp_path):
    scripts = compare.REPO_ROOT / "tools" / "visual_compositor" / "adapters"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            """
set -euo pipefail
export LAB_DIR="$1"
source "$2/common.sh"
source "$2/niri.sh"
session_probe_json() { echo '{"layer_shell_supported":true,"gtk_display_is_wayland":true}'; }
adapter_capabilities
""",
            "review",
            str(tmp_path),
            str(scripts),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["native_geometry"] is False


def test_geometry_mode_checks_assertions_without_recording_pixels(evidence):
    result = evidence.evaluate(update=False, geometry_only=True)
    assert result.status == "pass"
    assert result.pending is None
    assert "pixel baseline not compared" in result.detail
    evidence.record["dock_rect"]["width"] = 5000
    assert evidence.evaluate(geometry_only=True).status == "fail"


@pytest.mark.parametrize("action", ["menu", "tooltip", "zoom"])
def test_interaction_requires_a_visible_effect(tmp_path, action):
    from tools.visual_compositor.behaviour import check_action

    case = SimpleNamespace(name="interaction", action=action)
    for phase in ("resting", "effect"):
        Image.new("RGB", (1280, 720), "black").save(
            tmp_path / f"interaction.{phase}.png"
        )
    ok, _ = check_action(
        evidence=tmp_path,
        case=case,
        band={"x": 0, "y": 400, "width": 1280, "height": 320},
    )
    assert not ok


def test_tooltip_cannot_masquerade_as_zoom(tmp_path):
    from tools.visual_compositor.behaviour import check_action

    before = Image.new("RGB", (1280, 720), "black")
    ImageDraw.Draw(before).rectangle((400, 680, 879, 719), fill="white")
    before.save(tmp_path / "zoom.resting.png")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((550, 550, 700, 580), fill="white")
    after.save(tmp_path / "zoom.effect.png")
    ok, _ = check_action(
        evidence=tmp_path,
        case=SimpleNamespace(name="zoom", action="zoom"),
        band={"x": 0, "y": 400, "width": 1280, "height": 320},
    )
    assert not ok


@pytest.mark.parametrize("popup", [True, False])
def test_tooltip_requires_popup_above_dock_not_cursor(tmp_path, popup):
    from tools.visual_compositor.behaviour import check_action

    before = Image.new("RGB", (1280, 720), "black")
    ImageDraw.Draw(before).rectangle((400, 680, 879, 719), fill="white")
    before.save(tmp_path / "tooltip.resting.png")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((450, 685, 465, 705), fill="red")
    if popup:
        ImageDraw.Draw(after).rectangle((430, 630, 530, 650), fill="white")
    after.save(tmp_path / "tooltip.effect.png")
    ok, _ = check_action(
        evidence=tmp_path,
        case=SimpleNamespace(name="tooltip", action="tooltip"),
        band={"x": 0, "y": 400, "width": 1280, "height": 320},
    )
    assert ok is popup


@pytest.mark.parametrize("hidden", [True, False])
def test_autohide_requires_both_hide_transitions(tmp_path, hidden):
    from tools.visual_compositor.behaviour import check_action

    visible = Image.new("RGB", (1280, 720), "black")
    ImageDraw.Draw(visible).rectangle((400, 680, 879, 719), fill="white")
    visible.save(tmp_path / "hide.revealed.png")
    blank = Image.new("RGB", (1280, 720), "black")
    blank.save(tmp_path / "hide.hidden.png")
    (blank if hidden else visible).save(tmp_path / "hide.hidden-again.png")
    ok, _ = check_action(
        evidence=tmp_path,
        case=SimpleNamespace(name="hide", action="autohide"),
        band={"x": 0, "y": 400, "width": 1280, "height": 320},
    )
    assert ok is hidden


@pytest.mark.parametrize("on_removed", [True, False, "cursor"])
def test_output_removal_requires_dock_on_that_output(tmp_path, on_removed):
    case = SimpleNamespace(name="remove", display_change="remove", edge="bottom", gap=0)
    first = {"name": "HEADLESS-1", "x": 0, "y": 0, "width": 1280, "height": 720}
    second = {**first, "name": "HEADLESS-2", "x": 1280}
    (tmp_path / "remove.before-change.json").write_text(
        json.dumps({"outputs": [first, second]})
    )
    image = Image.new("RGB", (2560, 720), "black")
    offset = 1280 if on_removed else 0
    if on_removed == "cursor":
        ImageDraw.Draw(image).rectangle((1700, 685, 1710, 700), fill="white")
    else:
        ImageDraw.Draw(image).rectangle(
            (offset + 400, 680, offset + 879, 719), fill="white"
        )
    image.save(tmp_path / "remove.before-change.png")
    error = compare.check_output_transition(
        evidence=tmp_path, case=case, record={"outputs": [first]}
    )
    assert (error is None) is (on_removed is True)


def test_scaled_screenshot_requires_logical_pixel_contract(evidence):
    evidence.record["outputs"][0]["scale"] = 2
    assert evidence.evaluate(geometry_only=True).status == "unsupported"
    evidence.caps["logical_screenshot"] = True
    assert evidence.evaluate(geometry_only=True).status == "pass"


def test_negative_origin_transforms_self_report_into_canvas(evidence):
    evidence.record["outputs"][0]["x"] = -1280
    evidence.record["dock_rect"]["x"] = -1280
    evidence.record["capture_origin"] = {"x": -1280, "y": 0}
    evidence.record["self_reported_anchor"] = "(true, -640, 700, 'bottom')"
    result = evidence.evaluate(geometry_only=True)
    assert result.status == "pass"


def test_container_receives_actions_and_output_changes(tmp_path):
    cases = scenarios.select_cases(behavior=None, only=["output-removal", "hover-zoom"])
    path = tmp_path / "cases.json"
    scenarios.emit_cases(path, cases)
    records = {item["name"]: item for item in json.loads(path.read_text())}
    assert records["output-removal"]["display_change"] == "remove"
    assert records["output-removal"]["display_scene"] == "dual"
    assert records["hover-zoom"]["action"] == "zoom"
    assert records["hover-zoom"]["requires"] == "interactive"


@pytest.mark.parametrize("offset", [(10, 10), (-15, 10), (90, 10), (10, -15), (10, 70)])
def test_popup_oracle_rejects_clipping_on_every_edge(tmp_path, offset):
    from tools.visual_compositor.popup_assertions import check_popup

    case = scenarios.POPUP_CASES[0]
    canvas = Image.new("RGB", (120, 90), (20, 46, 71))
    template = Image.new("RGBA", (50, 35), (0, 0, 0, 255))
    draw = ImageDraw.Draw(template)
    draw.rectangle((4, 4, 20, 20), fill="white")
    draw.rectangle((30, 9, 44, 29), fill="red")
    canvas.save(tmp_path / f"{case.name}.resting.png")
    canvas.paste(template, offset, template)
    canvas.save(tmp_path / f"{case.name}.effect.png")
    template.save(tmp_path / f"{case.name}.popup-template.png")
    (tmp_path / f"{case.name}.popup.json").write_text(
        json.dumps(
            {
                "popups": [
                    {"class": "Window", "kind": "tooltip", "width": 50, "height": 35}
                ]
            }
        )
    )
    assert check_popup(tmp_path, case)[0] is (offset == (10, 10))


def test_popup_oracle_rejects_preexisting_pixels(tmp_path):
    from tools.visual_compositor.popup_assertions import check_popup

    case = scenarios.POPUP_CASES[0]
    template = Image.new("RGBA", (50, 35), "black")
    ImageDraw.Draw(template).rectangle((5, 5, 30, 20), fill="white")
    canvas = Image.new("RGB", (120, 90), (20, 46, 71))
    canvas.paste(template, (10, 10), template)
    for phase in ("effect", "resting"):
        canvas.save(tmp_path / f"{case.name}.{phase}.png")
    template.save(tmp_path / f"{case.name}.popup-template.png")
    (tmp_path / f"{case.name}.popup.json").write_text(
        json.dumps(
            {
                "popups": [
                    {"class": "Window", "kind": "tooltip", "width": 50, "height": 35}
                ]
            }
        )
    )
    assert not check_popup(tmp_path, case)[0]


@pytest.mark.parametrize(
    "mutation", ["none", "native-minimized", "native-focus", "pid", "still-open"]
)
def test_backend_actions_require_native_confirmation(tmp_path, mutation):
    from tools.visual_compositor.backend_assertions import check_events

    case = next(c for c in scenarios.WINDOW_CASES if c.action == "window-actions")
    events = []
    for phase in ("opened", "minimized", "restored", "closed"):
        window = {
            "title": "Lab probe",
            "minimized": phase == "minimized",
            "active": phase == "restored",
        }
        windows = [] if phase == "closed" else [window]
        events.append(
            {
                "phase": phase,
                "app": {"pid": 123, "windows": windows},
                "native_windows": [dict(w) for w in windows],
            }
        )
    if mutation == "native-minimized":
        events[1]["native_windows"][0]["minimized"] = False
    elif mutation == "native-focus":
        events[2]["native_windows"][0]["active"] = False
    elif mutation == "pid":
        events[2]["app"]["pid"] = 456
    elif mutation == "still-open":
        events[3]["native_windows"] = [{"title": "Lab probe"}]
    (tmp_path / f"{case.name}.events.json").write_text(json.dumps(events))
    assert check_events(tmp_path, case)[0] is (mutation == "none")


@pytest.mark.parametrize(
    "scene,scales,valid",
    [
        ("fractional", [1, 1], False),
        ("fractional", [1.25, 1.25], True),
        ("mixed-dpi", [1.25], False),
        ("mixed-dpi", [1, 1], False),
        ("mixed-dpi", [1.25, 1], True),
    ],
)
def test_scaling_requires_actual_native_scale(evidence, scene, scales, valid):
    from dataclasses import replace

    case = replace(evidence.case, display_scene=scene)
    evidence.record["outputs"] = [
        dict(evidence.record["outputs"][0], scale=scale) for scale in scales
    ]
    (evidence.path / f"{case.name}.json").write_text(json.dumps(evidence.record))
    result = compare.evaluate(
        evidence_dir=evidence.path,
        cases=[case],
        out_dir=evidence.path / "diffs",
        update=False,
        compositor="cinnamon",
        capabilities=evidence.caps,
        run_meta=evidence.meta,
        geometry_only=True,
    )[0]
    # Further geometric checks may reject synthetic scaled pixels; this check
    # specifically establishes that unchanged/default scaling cannot pass.
    assert ("native " in result.detail and "observed" in result.detail) is (not valid)


@pytest.mark.parametrize("missing", [None, 0, 1, 2, 3])
def test_output_calibration_requires_all_native_corners(tmp_path, missing):
    from tools.visual_compositor.probes.output_calibration import measure_output

    image = Image.new("RGB", (300, 200), "black")
    colors = [(255, 0, 80), (0, 255, 80), (80, 0, 255), (255, 255, 0)]
    corners = [(20, 30), (115, 30), (20, 105), (115, 105)]
    draw = ImageDraw.Draw(image)
    for index, (point, color) in enumerate(zip(corners, colors, strict=True)):
        if index != missing:
            x, y = point
            draw.rectangle((x, y, x + 4, y + 4), fill=color)
    args = (image.tobytes(), 900, 3, {"x": 0, "y": 0, "width": 200, "height": 150})
    if missing is not None:
        with pytest.raises(RuntimeError, match="marker"):
            measure_output(*args)
    else:
        assert measure_output(*args) == {"x": 20, "y": 30, "width": 100, "height": 80}


def test_calibration_markers_cannot_count_as_dock_pixels():
    from tools.visual_compositor.probes.output_calibration import remove_markers

    image = Image.new("RGB", (100, 80), (20, 46, 71))
    colors = [(255, 0, 80), (0, 255, 80), (80, 0, 255), (255, 255, 0)]
    draw = ImageDraw.Draw(image)
    for (x, y), color in zip([(0, 0), (88, 0), (0, 68), (88, 68)], colors, strict=True):
        draw.rectangle((x, y, x + 11, y + 11), fill=color)
    rect = {"x": 0, "y": 0, "width": 100, "height": 80}
    cleaned = remove_markers(image.tobytes(), 300, 3, rect)
    assert cleaned == Image.new("RGB", image.size, (20, 46, 71)).tobytes()
    draw.rectangle((25, 60, 75, 79), fill="white")
    cleaned = Image.frombytes(
        "RGB", image.size, remove_markers(image.tobytes(), 300, 3, rect)
    )
    assert cleaned.getpixel((50, 70)) == (255, 255, 255)
