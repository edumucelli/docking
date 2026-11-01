"""Windows display setup must precede GTK and respect an existing host mode."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.native.windows import startup


@pytest.fixture
def dpi_api(monkeypatch):
    set_awareness = Mock(return_value=1)
    library = Mock(
        return_value=SimpleNamespace(SetProcessDpiAwarenessContext=set_awareness)
    )
    monkeypatch.setattr(startup.ctypes, "WinDLL", library, raising=False)
    error = Mock(return_value=0)
    monkeypatch.setattr(startup.ctypes, "get_last_error", error, raising=False)
    return set_awareness, library, error


def test_selects_per_monitor_v2_before_the_toolkit_initializes(dpi_api):
    set_awareness, library, error = dpi_api
    startup._prepare_dpi_awareness()
    library.assert_called_once_with("user32", use_last_error=True)
    assert set_awareness.call_args.args[0].value == ctypes.c_void_p(-4).value
    assert set_awareness.argtypes == (ctypes.c_void_p,)
    assert set_awareness.restype is ctypes.c_int32
    error.assert_not_called()


def test_existing_manifest_or_host_awareness_remains_in_force(dpi_api):
    set_awareness, _library, error = dpi_api
    set_awareness.return_value = 0
    error.return_value = 5
    startup._prepare_dpi_awareness()
    error.assert_called_once_with()


def test_other_dpi_initialization_errors_are_not_silenced(dpi_api):
    set_awareness, _library, error = dpi_api
    set_awareness.return_value = 0
    error.return_value = 87
    with pytest.raises(OSError) as result:
        startup._prepare_dpi_awareness()
    assert result.value.errno == 87
