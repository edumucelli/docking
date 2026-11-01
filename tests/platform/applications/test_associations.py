"""Explicit association availability and Unix lookup precedence."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.applications.associations import (
    AssociationRead,
    AssociationResolution,
    AssociationStatus,
)
from tests.platform.application_fakes import (
    ApplicationRegistryHarness,
    GioApplicationFake,
)


def registry():
    result = ApplicationRegistryHarness().registry
    result.refresh()
    return result


def test_unix_successful_default_never_queries_lower_priority_apis(monkeypatch):
    from docking.platform.applications.implementations.unix import (
        associations as unix_catalog,
    )

    app = GioApplicationFake("owned.desktop", name="Owned", commandline="owned")
    default = Mock(return_value=app)
    forbidden = Mock(side_effect=AssertionError("Lower priority query ran"))
    monkeypatch.setattr(unix_catalog.Gio.AppInfo, "get_default_for_type", default)
    monkeypatch.setattr(unix_catalog.Gio.AppInfo, "get_recommended_for_type", forbidden)
    monkeypatch.setattr(unix_catalog.Gio.AppInfo, "get_all_for_type", forbidden)
    result = registry().preferred_listing_for_content_types(
        ("audio/mpeg", "audio/flac")
    )
    assert result.status is AssociationStatus.READY and result.listing.name == "Owned"
    default.assert_called_once_with("audio/mpeg", False)
    forbidden.assert_not_called()


def test_unix_all_default_types_precede_recommended_and_all(monkeypatch):
    from docking.platform.applications.implementations.unix import (
        associations as unix_catalog,
    )

    calls = []
    app = GioApplicationFake("owned.desktop", name="Owned", commandline="owned")
    monkeypatch.setattr(
        unix_catalog.Gio.AppInfo,
        "get_default_for_type",
        lambda value, _uri: calls.append(("default", value)),
    )
    monkeypatch.setattr(
        unix_catalog.Gio.AppInfo,
        "get_recommended_for_type",
        lambda value: calls.append(("recommended", value)) or [],
    )
    monkeypatch.setattr(
        unix_catalog.Gio.AppInfo,
        "get_all_for_type",
        lambda value: calls.append(("all", value)) or [app],
    )
    result = registry().preferred_listing_for_content_types(
        ("one/type", "two/type", "one/type")
    )
    assert result.listing.name == "Owned"
    assert calls == [
        ("default", "one/type"),
        ("default", "two/type"),
        ("recommended", "one/type"),
        ("recommended", "two/type"),
        ("all", "one/type"),
    ]


@pytest.mark.parametrize("missing", [False, True])
def test_unix_missing_or_failed_lookup_is_unavailable_not_ready_absent(
    monkeypatch, missing
):
    from docking.platform.applications.implementations.unix import (
        associations as unix_catalog,
    )

    monkeypatch.setattr(
        unix_catalog.Gio.AppInfo,
        "get_default_for_type",
        Mock(side_effect=OSError(5, "Owned failure")),
    )
    if missing:
        monkeypatch.setattr(unix_catalog.Gio.AppInfo, "get_recommended_for_type", None)
    else:
        monkeypatch.setattr(
            unix_catalog.Gio.AppInfo,
            "get_recommended_for_type",
            Mock(side_effect=OSError(5, "Owned failure")),
        )
    monkeypatch.setattr(unix_catalog.Gio.AppInfo, "get_all_for_type", lambda _value: [])
    assert (
        registry().preferred_listing_for_content_types(("one/type",)).status
        is AssociationStatus.UNAVAILABLE
    )


def test_ready_empty_is_distinct_from_pending_and_unavailable():
    result = registry()
    app = GioApplicationFake("owned.desktop", name="Owned", commandline="owned")
    # Model the backend contract without replacing unrelated catalog behavior.
    result._associations.reads = lambda keys: (
        (
            key,
            AssociationRead(AssociationStatus.PENDING)
            if index == 0
            else AssociationRead(AssociationStatus.READY, (app,)),
        )
        for index, key in enumerate(keys)
    )
    assert (
        result.preferred_listing_for_content_types(("one/type",)).status
        is AssociationStatus.PENDING
    )
    result._associations.reads = lambda keys: (
        (key, AssociationRead(AssociationStatus.READY)) for key in keys
    )
    assert result.preferred_listing_for_content_types(
        ("one/type",)
    ) == AssociationResolution(AssociationStatus.READY)
    result._associations.reads = lambda keys: (
        (key, AssociationRead(AssociationStatus.UNAVAILABLE)) for key in keys
    )
    assert (
        result.preferred_listing_for_content_types(("one/type",)).status
        is AssociationStatus.UNAVAILABLE
    )


def test_partial_failure_can_only_fall_back_to_verified_requested_handler():
    result = registry()
    app = GioApplicationFake("owned.desktop", name="Owned", commandline="owned")

    def reads(keys):
        for key in keys:
            yield (
                key,
                AssociationRead(AssociationStatus.READY, (app,))
                if key.lookup == "recommended"
                else AssociationRead(AssociationStatus.UNAVAILABLE),
            )

    result._associations.reads = reads
    selected = result.preferred_listing_for_content_types(("one/type",))
    assert (
        selected.status is AssociationStatus.READY and selected.listing.name == "Owned"
    )


def test_native_directory_identity_never_falls_back_to_executable_or_display_name():
    result = registry()
    result._associations.reads = lambda keys: (
        (
            key,
            AssociationRead(AssociationStatus.READY, application_id="Missing.Exact.Id"),
        )
        for key in keys
    )
    assert (
        result.default_for_content_type("inode/directory").status
        is AssociationStatus.UNAVAILABLE
    )


def test_unix_directory_name_retains_direct_platform_behavior(monkeypatch):
    from docking.platform.applications.implementations.unix import (
        associations as unix_catalog,
    )

    app = SimpleNamespace(get_display_name=lambda: "Owned Files")
    getter = Mock(return_value=app)
    monkeypatch.setattr(unix_catalog.Gio.AppInfo, "get_default_for_type", getter)
    assert registry().directory_application_name() == AssociationResolution(
        AssociationStatus.READY, name="Owned Files"
    )
    getter.assert_called_once_with("inode/directory", False)


def test_worker_hidden_id_filters_an_association_absent_from_normal_catalog():
    result = registry()
    handler = GioApplicationFake(
        "Hidden.Association", name="Hidden", commandline="hidden"
    )
    from docking.platform.applications.implementations.windows.associations import (
        WindowsApplicationAssociationBackend,
    )

    lookup = SimpleNamespace(
        hidden_ids=frozenset(("Hidden.Association",)),
        applications=(),
        association_revision=0,
        association_reads=lambda keys: tuple(
            (key, AssociationRead(AssociationStatus.READY, (handler,))) for key in keys
        ),
    )
    result._associations = WindowsApplicationAssociationBackend(lookup)
    assert result.default_for_content_type("owned/type").listing is None
    assert result.preferred_listing_for_content_types(("owned/type",)).listing is None
    assert result._content_gio_handles == {}
