"""Tests for generic Wayland idle notifications."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.wayland.runtime import IdleProtocolAdapter


class _Notifier:
    def __init__(self) -> None:
        self.get_idle_notification = MagicMock(
            return_value=SimpleNamespace(dispatcher={})
        )
        self.get_input_idle_notification = MagicMock(
            return_value=SimpleNamespace(dispatcher={})
        )


def _started_adapter(*, version: int) -> tuple[IdleProtocolAdapter, _Notifier, object]:
    notifier = _Notifier()
    seat = object()
    adapter = IdleProtocolAdapter()
    adapter._notifier = notifier
    adapter._notifier_version = version
    adapter._seat = seat
    adapter.start(MagicMock())
    return adapter, notifier, seat


def test_idle_adapter_uses_version_one_request_for_version_one_compositors():
    _adapter, notifier, seat = _started_adapter(version=1)

    notifier.get_idle_notification.assert_called_once_with(0, seat)
    notifier.get_input_idle_notification.assert_not_called()


def test_idle_adapter_uses_input_only_request_when_version_two_is_available():
    _adapter, notifier, seat = _started_adapter(version=2)

    notifier.get_input_idle_notification.assert_called_once_with(0, seat)
    notifier.get_idle_notification.assert_not_called()
