"""One scoped native handle observes related process facts without PID caching."""

import ctypes
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.native.windows import process as mod


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(
        path=r"C:\Program Files\Example\Recorder.EXE",
        aumid="Example.Application",
        image_ok=True,
        aumid_size=None,
        aumid_status=0,
    )

    def image(handle, flags, buffer, size):
        assert handle == 17 and flags == 0
        assert size._obj.value == 32768 and ctypes.sizeof(size._obj) == 4
        buffer.value = state.path
        return state.image_ok

    def aumid(handle, size, buffer):
        assert handle == 17
        if buffer is None:
            size._obj.value = (
                len(state.aumid) + 1 if state.aumid_size is None else state.aumid_size
            )
            return 122
        buffer.value = state.aumid
        return state.aumid_status

    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=17),
        CloseHandle=Mock(return_value=True),
        QueryFullProcessImageNameW=Mock(side_effect=image),
        GetApplicationUserModelId=Mock(side_effect=aumid),
    )
    load = Mock(return_value=kernel)
    monkeypatch.setattr(mod.windows_ctypes, "WinDLL", load, raising=False)
    mod._process_api.cache_clear()
    yield state, kernel, load
    mod._process_api.cache_clear()


def test_related_facts_share_one_query_handle_and_close_it(native):
    state, kernel, load = native
    assert mod.read_process_metadata(123) == mod.WindowsProcessMetadata(
        PureWindowsPath(state.path), state.aumid
    )
    kernel.OpenProcess.assert_called_once_with(0x1000, False, 123)
    kernel.CloseHandle.assert_called_once_with(17)
    assert kernel.QueryFullProcessImageNameW.call_count == 1
    assert kernel.GetApplicationUserModelId.call_count == 2
    load.assert_called_once_with("kernel32", use_last_error=True)


@pytest.mark.parametrize(
    "failure", [0, PermissionError("access denied"), OSError("gone")]
)
def test_vanished_or_inaccessible_process_has_no_metadata(native, failure):
    _state, kernel, _load = native
    if isinstance(failure, Exception):
        kernel.OpenProcess.side_effect = failure
    else:
        kernel.OpenProcess.return_value = failure
    assert mod.read_process_metadata(123) is None
    kernel.CloseHandle.assert_not_called()
    kernel.QueryFullProcessImageNameW.assert_not_called()
    kernel.GetApplicationUserModelId.assert_not_called()


@pytest.mark.parametrize("failure", [False, OSError("image unavailable")])
def test_missing_path_preserves_aumid_and_closes_handle(native, failure):
    state, kernel, _load = native
    if isinstance(failure, Exception):
        kernel.QueryFullProcessImageNameW.side_effect = failure
    else:
        state.image_ok = failure
    assert mod.read_process_metadata(123) == mod.WindowsProcessMetadata(
        None, state.aumid
    )
    kernel.CloseHandle.assert_called_once_with(17)


@pytest.mark.parametrize("size", [0, 4097])
def test_aumid_size_limit_preserves_path_without_allocating_large_buffer(native, size):
    state, kernel, _load = native
    state.aumid_size = size
    assert mod.read_process_metadata(123) == mod.WindowsProcessMetadata(
        PureWindowsPath(state.path), None
    )
    kernel.GetApplicationUserModelId.assert_called_once()
    kernel.CloseHandle.assert_called_once_with(17)


@pytest.mark.parametrize("failure", [5, OSError("application id unavailable")])
def test_missing_aumid_preserves_executable_path(native, failure):
    state, kernel, _load = native
    if isinstance(failure, Exception):
        kernel.GetApplicationUserModelId.side_effect = failure
    else:
        state.aumid_status = failure
    assert mod.read_process_metadata(123) == mod.WindowsProcessMetadata(
        PureWindowsPath(state.path), None
    )
    kernel.CloseHandle.assert_called_once_with(17)


def test_handle_is_closed_even_for_an_unexpected_query_failure(native):
    _state, kernel, _load = native
    kernel.QueryFullProcessImageNameW.side_effect = ValueError("broken native adapter")
    with pytest.raises(ValueError, match="broken native adapter"):
        mod.read_process_metadata(123)
    kernel.CloseHandle.assert_called_once_with(17)


def test_pid_reuse_observes_fresh_facts_while_only_caching_bindings(native):
    state, kernel, load = native
    first = mod.read_process_metadata(123)
    state.path, state.aumid = r"C:\Other\Another.exe", "Another.Application"
    second = mod.read_process_metadata(123)
    assert first != second
    assert second == mod.WindowsProcessMetadata(
        PureWindowsPath(state.path), state.aumid
    )
    assert kernel.OpenProcess.call_count == kernel.CloseHandle.call_count == 2
    assert load.call_count == 1


def test_executable_reader_reuses_path_primitive_without_querying_aumid(native):
    state, kernel, _load = native
    assert mod.NativeProcessReader().executable(123) == Path(state.path)
    kernel.GetApplicationUserModelId.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(17)


@pytest.mark.parametrize("pid", [0, -1, True, False, 1.0, "1", None])
def test_invalid_pid_does_not_load_native_functions(native, pid):
    _state, _kernel, load = native
    assert mod.read_process_metadata(pid) is None
    load.assert_not_called()


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        mod.WindowsProcessMetadata(
            PureWindowsPath(r"C:\Program Files\Example\Straße.EXE"),
            "Example.Application",
        ),
        mod.WindowsProcessMetadata(None, "Example.Application"),
        mod.WindowsProcessMetadata(PureWindowsPath(r"C:\Example.exe"), None),
        mod.WindowsProcessMetadata(None, None),
    ],
)
def test_consumers_preserve_path_case_and_unavailable_metadata(monkeypatch, metadata):
    from docking.applets.micshield.implementations.windows import audio as microphone
    from docking.applets.music.implementations.windows import audio as music
    from docking.platform.backends.windows import windows
    from docking.search.services.implementations.windows import focus

    observe = Mock(return_value=metadata)
    for module in (windows, music, microphone, focus):
        monkeypatch.setattr(module, "read_process_metadata", observe)
    path = metadata.executable_path if metadata is not None else None
    aumid = metadata.aumid if metadata is not None else None
    window_api = windows.Win32WindowAPI.__new__(windows.Win32WindowAPI)
    audio_api = music.WindowsPlayerAudio.__new__(music.WindowsPlayerAudio)
    shell_api = focus.WindowsShellFocus.__new__(focus.WindowsShellFocus)
    assert window_api._process_identity(123) == (
        aumid,
        path.stem.lower() if path is not None else "",
    )
    assert audio_api._process_identity(123) == (
        aumid or "",
        str(path) if path is not None else "",
    )
    assert microphone._process_name(123) == (path.stem if path is not None else "")
    assert shell_api._process(123) == (path.stem.casefold() if path is not None else "")
    assert observe.call_count == 4
    assert observe.call_args_list[:2] == [((123,),), ((123,),)]
    assert observe.call_args_list[2:] == [
        ((123,), {"include_aumid": False}),
        ((123,), {"include_aumid": False}),
    ]
