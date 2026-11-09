"""Failure recovery and dependency ordering for session-owned services."""

from unittest.mock import Mock

import pytest

from docking.platform.backends.lifecycle import ServiceLifecycle


@pytest.mark.parametrize("failing_stage", range(4))
def test_partial_start_preserves_error_and_cleans_every_owned_service(
    failing_stage, caplog
):
    lifecycle = ServiceLifecycle()
    events = []
    original = OSError("startup failed")
    lifecycle.add("runtime", stop=lambda: events.append("runtime"))

    def start(index):
        events.append(("start", index))
        if index == failing_stage:
            raise original

    def stop(index):
        events.append(("stop", index))
        raise ValueError(f"cleanup failed {index}")

    for index in range(4):
        lifecycle.add(
            str(index),
            start=lambda index=index: start(index),
            stop=lambda index=index: stop(index),
        )
    with pytest.raises(OSError) as error:
        lifecycle.start()
    assert error.value is original
    assert events == (
        [("start", index) for index in range(failing_stage + 1)]
        + [("stop", index) for index in reversed(range(4))]
        + ["runtime"]
    )
    assert all(f"cleanup failed {index}" in caplog.text for index in range(4))
    lifecycle.stop()
    assert events.count("runtime") == 1


@pytest.mark.parametrize("start_first", [False, True])
def test_shutdown_attempts_every_cleanup_once_even_when_all_fail(start_first, caplog):
    lifecycle = ServiceLifecycle()
    services = [Mock() for _ in range(4)]
    for index, service in enumerate(services):
        service.stop.side_effect = OSError(f"stop failed {index}")
        lifecycle.add_service(str(index), lambda service=service: service)
    if start_first:
        lifecycle.start()
        lifecycle.start()
    lifecycle.stop()
    lifecycle.stop()
    for index, service in enumerate(services):
        assert service.start.call_count == int(start_first)
        service.stop.assert_called_once_with()
        assert f"stop failed {index}" in caplog.text


def test_service_replacement_before_start_is_resolved_by_owner():
    lifecycle = ServiceLifecycle()
    service = None
    lifecycle.add_service("optional service", lambda: service)
    service = Mock()
    lifecycle.start()
    lifecycle.stop()
    service.start.assert_called_once_with()
    service.stop.assert_called_once_with()


def test_restart_leaves_support_up_to_owned_services():
    lifecycle = ServiceLifecycle()
    service = Mock()
    lifecycle.add_service("service", lambda: service)
    for _ in range(2):
        lifecycle.start()
        lifecycle.stop()
    assert service.start.call_count == 2
    assert service.stop.call_count == 2


def test_extension_failure_unwinds_children_before_parent_even_if_cleanup_fails():
    events = []
    parent = ServiceLifecycle()
    parent.add("runtime", stop=lambda: events.append("runtime"))
    parent.add("windows", stop=lambda: events.append("windows"))
    extension = ServiceLifecycle()
    extension.add("base session", start=parent.start, stop=parent.stop)
    child = Mock()
    child.start.side_effect = OSError("extension failed")
    child.stop.side_effect = lambda: events.append("extension") or 1 / 0
    extension.add_service("extension", lambda: child)
    with pytest.raises(OSError, match="extension failed"):
        extension.start()
    assert events == ["extension", "windows", "runtime"]
    extension.stop()
    assert len(events) == 3
