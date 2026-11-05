"""Desktop tools compose the selected candidate and own catalog readiness."""

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import application_graph as graph


def test_import_does_not_select_a_docking_runtime_before_candidate_validation():
    code = (
        "import sys; from tools.windows.visual import application_graph; "
        "assert not any(n == 'docking' or n.startswith('docking.') "
        "or n == 'gi' or n.startswith('gi.') for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())


@pytest.mark.parametrize("explicit", [False, True])
def test_registry_uses_one_selected_application_graph(monkeypatch, explicit):
    from docking.platform.applications import registry
    from docking.platform.composition import selection

    sources = SimpleNamespace(
        catalog=object(), associations=object(), registrations=object()
    )
    provider = SimpleNamespace(application_sources=Mock(return_value=sources))
    select = Mock(return_value=provider)
    construct = Mock()
    monkeypatch.setattr(selection, "platform_provider", select)
    monkeypatch.setattr(registry, "ApplicationRegistry", construct)
    assert (
        graph.create_application_registry(**({"sources": sources} if explicit else {}))
        is construct.return_value
    )
    construct.assert_called_once_with(
        catalog_backend=sources.catalog,
        association_backend=sources.associations,
        registration_backend=sources.registrations,
    )
    assert select.call_count == int(not explicit)
    assert provider.application_sources.call_count == int(not explicit)


@pytest.mark.parametrize("failure", [None, OSError("catalog unavailable")])
def test_ready_registry_pumps_publication_and_cleans_up_on_failure(
    monkeypatch, failure
):
    from tools.windows.visual import applications_domain_smoke, desktop_driver

    registry = Mock(initial_discovery_pending=True)
    registry.initial_discovery_pending = True
    monkeypatch.setattr(graph, "create_application_registry", lambda: registry)
    monkeypatch.setattr(graph.ctypes, "WinDLL", Mock(), raising=False)
    cleanup = Mock()
    monkeypatch.setattr(graph.atexit, "register", cleanup)
    observed = []

    def refresh(candidate, context, alertable, facts):
        assert candidate is registry
        registry.start.assert_called_once_with()
        observed.append((context, alertable, facts))
        registry.initial_discovery_pending = len(observed) < 3

    monkeypatch.setattr(
        applications_domain_smoke, "refresh_association_registry", refresh
    )

    def wait(predicate, _message, *, seconds):
        assert seconds == 60
        assert not predicate()
        if failure is not None:
            raise failure
        assert not predicate()
        assert predicate()

    monkeypatch.setattr(desktop_driver.WindowsDesktop, "wait_for", wait)
    if failure is not None:
        with pytest.raises(OSError) as caught:
            graph.ready_application_registry()
        assert caught.value is failure
        registry.stop.assert_called_once_with()
        cleanup.assert_not_called()
    else:
        assert graph.ready_application_registry() is registry
        assert len(observed) == 3
        assert all(row == observed[0] for row in observed)
        registry.stop.assert_not_called()
        cleanup.assert_called_once_with(registry.stop)
