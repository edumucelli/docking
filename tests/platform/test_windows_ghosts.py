"""Optional ghost exports and live, bidirectional native window associations."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.backends.windows.ghosts import WindowsGhostWindows


def native():
    return SimpleNamespace(
        IsWindow=Mock(return_value=True),
        GetClassNameW=Mock(
            side_effect=lambda window, output, size: setattr(output, "value", "Ghost")
        ),
        HungWindowFromGhostWindow=Mock(return_value=100),
        GhostWindowFromHungWindow=Mock(return_value=900),
    )


def test_native_association_preserves_wide_handles_and_checks_both_directions():
    user = native()
    api = WindowsGhostWindows(user)
    assert api.original(900) == 100
    assert api.presentation(100) == 900
    user.HungWindowFromGhostWindow.return_value = 200
    assert api.presentation(100) is None
    assert user.HungWindowFromGhostWindow.restype is ctypes.c_void_p
    assert user.GhostWindowFromHungWindow.argtypes == (ctypes.c_void_p,)


@pytest.mark.parametrize(
    "missing", ["HungWindowFromGhostWindow", "GhostWindowFromHungWindow"]
)
def test_missing_optional_export_disables_its_association(missing):
    user = native()
    delattr(user, missing)
    api = WindowsGhostWindows(user)
    assert api.presentation(100) is None
    if missing == "HungWindowFromGhostWindow":
        assert api.original(900) is None
    else:
        assert api.original(900) == 100


@pytest.mark.parametrize(
    "invalid", ["ghost-gone", "original-gone", "self", "class", "empty"]
)
def test_disappeared_or_unassociated_windows_are_never_treated_as_applications(invalid):
    user = native()
    if invalid == "ghost-gone":
        user.IsWindow.side_effect = lambda window: window != 900
    elif invalid == "original-gone":
        user.IsWindow.side_effect = lambda window: window != 100
    elif invalid == "self":
        user.HungWindowFromGhostWindow.return_value = 900
    elif invalid == "class":
        user.GetClassNameW.side_effect = lambda window, output, size: setattr(
            output, "value", "Ordinary.Window"
        )
    else:
        user.HungWindowFromGhostWindow.return_value = None
    api = WindowsGhostWindows(user)
    assert api.original(900) is None
    assert api.presentation(100) is None
