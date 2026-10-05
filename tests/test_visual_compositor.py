"""Regression tests for the compositor harness's independent assertions."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from tools.visual_compositor import behaviour, compare, scenarios


@pytest.fixture
def window_evidence(tmp_path):
    def state(alpha=1, beta=1, focused=None, minimized=False, workspace=0):
        return {
            "active_workspace": workspace,
            "windows": [
                {
                    "id": index + (1 if app == "lab-alpha" else 100),
                    "app": app,
                    "focused": app == focused and index == 0,
                    "minimized": minimized and app == "lab-alpha",
                    "workspace": workspace,
                }
                for app, count in [("lab-alpha", alpha), ("lab-beta", beta)]
                for index in range(count)
            ],
        }

    report = {
        "gtk_display_is_wayland": True,
        "phases": {
            "launched": state(beta=0),
            "other-app": state(focused="lab-beta"),
            "switched": state(focused="lab-alpha"),
            "minimized": state(minimized=True),
            "restored": state(focused="lab-alpha"),
            "workspace": state(focused="lab-alpha", workspace=1),
            "multiple": state(alpha=2),
            "multiple-switched": state(alpha=2, focused="lab-alpha"),
            "closed": state(alpha=0, beta=0),
        },
    }
    path = tmp_path / "window-switching.windows.json"

    def check():
        path.write_text(json.dumps(report))
        return behaviour.check_action(
            evidence=tmp_path, case=scenarios.WINDOW_CASES[0], band={}
        )

    return SimpleNamespace(report=report, check=check)


def test_real_window_switching_accepts_complete_compositor_evidence(window_evidence):
    assert window_evidence.check()[0]


@pytest.mark.parametrize(
    "failure",
    [
        "missing-phase",
        "duplicate",
        "focus",
        "minimize",
        "restore",
        "workspace",
        "replacement",
        "wrong-workspace",
        "x11",
        "close",
    ],
)
def test_real_window_switching_rejects_incomplete_or_wrong_effects(
    window_evidence, failure
):
    report = window_evidence.report
    phases = report["phases"]
    if failure == "missing-phase":
        phases.pop("switched")
    elif failure == "duplicate":
        phases["switched"]["windows"].append(dict(phases["switched"]["windows"][0]))
    elif failure == "focus":
        phases["switched"]["windows"][0]["focused"] = False
    elif failure == "minimize":
        phases["minimized"]["windows"][0]["minimized"] = False
    elif failure == "restore":
        phases["restored"]["windows"][0]["minimized"] = True
    elif failure == "workspace":
        phases["workspace"]["active_workspace"] = 0
    elif failure == "replacement":
        phases["switched"]["windows"][0]["id"] = 42
    elif failure == "wrong-workspace":
        phases["workspace"]["windows"][0]["workspace"] = 0
    elif failure == "x11":
        report["gtk_display_is_wayland"] = False
    else:
        phases["closed"]["windows"] = phases["launched"]["windows"]
    assert not window_evidence.check()[0]


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
def test_cosmic_probe_matches_the_production_backend(tmp_path):
    from docking.platform.backends.wayland.cosmic_session import CosmicSessionBackend

    scripts = compare.REPO_ROOT / "tools" / "visual_compositor" / "adapters"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            """
set -euo pipefail
export LAB_DIR="$1"
source "$2/common.sh"
source "$2/cosmic.sh"
session_probe_json() { echo '{"layer_shell_supported":true,"gtk_display_is_wayland":true}'; }
adapter_prepare
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
    caps = json.loads(completed.stdout)
    backend = CosmicSessionBackend.__new__(CosmicSessionBackend)
    assert caps["expected_backend"] == backend.name


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
