"""Independent input references collect text failures only after safe restoration."""

from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_profile_smoke as mod


@pytest.mark.parametrize("text", ["â ", "aa ", "a\u0309 ", ""])
def test_reference_records_exact_text_after_restore_and_classic_reset(text):
    events = []
    result = {"ime_mode": {}}

    @contextmanager
    def mode():
        events.append("enter")
        try:
            yield
        finally:
            events.append("restore")
            result["ime_mode"]["restored"] = True

    def exercise():
        events.append("type")
        return text

    def validate():
        events.append("validate")
        return {"profile": "expected", "caller_unchanged": True}

    def reset():
        events.append("reset-US")
        assert "passed" not in result
        return {"layout": 0x04090409}

    mod._collect_reference(
        result=result, mode=mode(), exercise=exercise, validate=validate, reset=reset
    )
    assert events == ["enter", "type", "validate", "restore", "validate", "reset-US"]
    assert result["text"] == text
    assert result["passed"] is (text.strip() == "â")
    assert result["classic_reset"]["layout"] == 0x04090409


@pytest.mark.parametrize(
    "phase", ["setup", "input", "readback", "restore", "after-restore", "reset"]
)
@pytest.mark.parametrize("failure", [RuntimeError, AssertionError, KeyboardInterrupt])
def test_unexpected_failure_is_never_collected_as_a_composition_mismatch(
    phase, failure
):
    result = {"ime_mode": {}}
    events = []

    def check(current):
        events.append(current)
        if current == phase:
            raise failure(current)

    @contextmanager
    def mode():
        check("setup")
        try:
            yield
        finally:
            check("restore")
            result["ime_mode"]["restored"] = True

    def exercise():
        check("input")
        return "aa"

    def validate():
        check("after-restore" if result["ime_mode"].get("restored") else "readback")
        return {}

    def reset():
        check("reset")
        return {}

    with pytest.raises(failure, match=phase):
        mod._collect_reference(
            result=result,
            mode=mode(),
            exercise=exercise,
            validate=validate,
            reset=reset,
        )
    assert "passed" not in result
    if phase not in ("after-restore", "reset"):
        assert "reset" not in events


def test_scope_without_verified_mode_restoration_cannot_collect_a_mismatch():
    result = {"ime_mode": {"restored": False}}

    @contextmanager
    def mode():
        yield

    reset = Mock()
    with pytest.raises(RuntimeError, match="restoration was not verified"):
        mod._collect_reference(
            result=result,
            mode=mode(),
            exercise=lambda: "aa",
            validate=dict,
            reset=reset,
        )
    assert "passed" not in result
    reset.assert_not_called()


def matrix():
    return [
        {
            "architecture": architecture,
            "profiles": [
                {
                    "requested": profile,
                    "variants": [
                        {"routing": routing, "passed": True, "text": "â "}
                        for routing in ("scan-code", "mapped-vk")
                    ],
                }
                for profile in ("telex", "vni")
            ],
        }
        for architecture in (64, 32)
    ]


def test_aggregation_keeps_all_eight_cells_and_each_strict_mismatch():
    stages = matrix()
    assert mod._composition_failures(stages) == []
    validate, reset = Mock(return_value={}), Mock(return_value={"layout": 0x04090409})
    for stage in stages:
        for profile in stage["profiles"]:
            for row in profile["variants"]:
                row.pop("passed")
                row.pop("text")
                row["ime_mode"] = {}

                @contextmanager
                def mode(row=row):
                    yield
                    row["ime_mode"]["restored"] = True

                cell = (stage["architecture"], profile["requested"], row["routing"])
                text = {
                    (64, "telex", "scan-code"): "aa ",
                    (32, "vni", "mapped-vk"): "a\u0309 ",
                }.get(cell, "â ")
                mod._collect_reference(
                    result=row,
                    mode=mode(),
                    exercise=Mock(return_value=text),
                    validate=validate,
                    reset=reset,
                )
    assert reset.call_count == 8 and validate.call_count == 16
    original = deepcopy(stages)
    assert mod._composition_failures(stages) == [
        {
            "architecture": 64,
            "profile": "telex",
            "routing": "scan-code",
            "expected": "â",
            "text": "aa ",
        },
        {
            "architecture": 32,
            "profile": "vni",
            "routing": "mapped-vk",
            "expected": "â",
            "text": "a\u0309 ",
        },
    ]
    assert stages == original


@pytest.mark.parametrize(
    "missing", ["architecture", "profile", "duplicate-profile", "route"]
)
def test_incomplete_matrix_fails_instead_of_reporting_unreached_controls_as_passed(
    missing,
):
    stages = matrix()
    if missing == "architecture":
        stages.pop()
    elif missing == "profile":
        stages[0]["profiles"].pop()
    elif missing == "duplicate-profile":
        stages[0]["profiles"][1]["requested"] = "telex"
    else:
        stages[0]["profiles"][0]["variants"].pop()
    with pytest.raises(AssertionError, match="incomplete"):
        mod._composition_failures(stages)


def test_composition_observation_waits_for_strict_expected_text(tmp_path, monkeypatch):
    document = tmp_path / "input.txt"
    document.write_text("a", encoding="utf-8")
    monkeypatch.setattr(
        mod.time, "sleep", lambda _: document.write_text("â ", encoding="utf-8")
    )
    assert mod._observe_composition(document) == "â "


def test_composition_timeout_retains_literal_text_without_normalizing_it(tmp_path):
    document = tmp_path / "input.txt"
    document.write_text("a\u0309 ", encoding="utf-8-sig")
    assert mod._observe_composition(document, seconds=0) == "a\u0309 "


def test_document_read_failure_is_not_a_composition_mismatch(tmp_path):
    with pytest.raises(FileNotFoundError):
        mod._observe_composition(tmp_path / "missing.txt", seconds=0)
