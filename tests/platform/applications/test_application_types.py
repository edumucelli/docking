"""Invariants for canonical application values."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from docking.platform.applications.types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationMatch,
    ApplicationOrigin,
    MatchEvidence,
    MatchMethod,
)


def _application() -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id="org.example.App.desktop",
        name="Example App",
        declared_icon="org.example.App",
        wm_class="ExampleApp",
        exec_line="/opt/example/app %U",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=Path("/usr/share/applications/org.example.App.desktop"),
        executable_path=Path("/opt/example/app"),
        aliases=("exampleapp", "org.example.app", "app"),
        visible=True,
        has_gio_source=True,
    )


def test_enum_wire_values_match_the_v3_contract():
    assert [member.value for member in ApplicationOrigin] == [
        "installed",
        "generated",
        "runtime",
    ]
    assert [member.value for member in ApplicationLocation] == [
        "sandbox",
        "host",
    ]
    assert [member.value for member in ActionSource] == [
        "gio",
        "desktop-file",
    ]
    assert [member.value for member in MatchMethod] == [
        "launch-provenance",
        "wine-instance",
        "visible-alias",
        "instance-hint",
        "desktop-id",
        "wm-class",
        "runtime-path-split",
    ]


@pytest.mark.parametrize(
    "value_type",
    [ApplicationAction, ApplicationInfo, MatchEvidence, ApplicationMatch],
)
def test_canonical_dataclasses_are_keyword_only_and_slotted(value_type):
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in inspect.signature(value_type).parameters.values()
    )
    assert "__slots__" in value_type.__dict__


def test_canonical_values_are_frozen_and_have_immutable_defaults():
    action = ApplicationAction(
        action_id="new-window",
        name="New Window",
        sources=frozenset({ActionSource.GIO}),
    )
    application = _application()

    with pytest.raises(FrozenInstanceError):
        action.name = "Changed"  # ty: ignore[invalid-assignment]
    with pytest.raises(FrozenInstanceError):
        application.visible = False  # ty: ignore[invalid-assignment]

    assert application.categories == ()
    assert application.keywords == ()
    assert application.actions == ()
    assert not hasattr(application, "__dict__")


def test_application_match_always_retains_id_when_metadata_is_absent():
    evidence = MatchEvidence(
        method=MatchMethod.LAUNCH_PROVENANCE,
        raw_app_id="SharedTool",
        pid=42,
    )

    match = ApplicationMatch(
        desktop_id="removed.desktop",
        application=None,
        evidence=evidence,
    )

    assert match.desktop_id == "removed.desktop"
    assert match.application is None
    assert match.evidence is evidence
    assert match.runtime_app is None

    runtime = replace(_application(), origin=ApplicationOrigin.RUNTIME)
    runtime_match = replace(match, application=runtime)
    assert runtime_match.runtime_app is runtime
