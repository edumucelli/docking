"""Input-thread protocol, snapshot metadata, and owned local helper deployment."""

import ctypes
import threading
import uuid
from dataclasses import replace
from unittest.mock import Mock

import pytest

from docking.platform.native.windows import input_thread as mod
from docking.platform.native.windows.com import GUID
from docking.platform.native.windows.input_context import InputContext
from docking.platform.native.windows.input_profiles import (
    KEYBOARD_CATEGORY,
    InputProfile,
)


def test_wire_protocol_is_architecture_independent_and_never_contains_typed_text():
    assert ctypes.sizeof(mod.WireProfile) == 88
    assert ctypes.sizeof(mod.Request) == 496
    assert mod.Request.active.offset == 168
    assert mod.Request.language_tag.offset == 304
    assert all("text" not in name for name, _kind in mod.Request._fields_)
    request = mod.Request()
    request.active.kind, request.active.language = 1, 0x42A
    request.active.clsid = GUID.parse("c2cb2cf0-af47-413e-9780-8bc3a3c16068")
    request.active.profile = GUID.parse("5fb02ec5-0a77-4684-b4fa-def8a2195628")
    request.active.category = GUID.parse(KEYBOARD_CATEGORY)
    request.profile_known = 1
    request.observed = 0x042A042A
    tag = "vi-VN".encode("utf-16-le")
    ctypes.memmove(request.language_tag, tag, len(tag))
    request.ime_flags, request.composition_bytes = 3, 4
    snapshot = mod._snapshot(request)
    assert snapshot.profile.language == 0x42A
    assert snapshot.profile.identifier == "5fb02ec5-0a77-4684-b4fa-def8a2195628"
    assert snapshot.language_tag == "vi-VN" and snapshot.layout == 0x42A042A
    assert snapshot.ime_known and snapshot.ime_open and snapshot.composition_bytes == 4
    request.profile_known = 0
    assert mod._snapshot(request).profile is None


def test_cancelled_request_does_not_load_a_dll_or_query_a_process():
    client = mod.InputThread.__new__(mod.InputThread)
    client._machine = Mock(side_effect=AssertionError("queried a cancelled target"))
    cancelled = threading.Event()
    cancelled.set()
    assert client.request(InputContext(1, 1, 2, 3), cancelled) is None


@pytest.mark.parametrize("layout", [0, 1, 0x100000000])
def test_classic_request_requires_an_exact_layout_before_querying_native_resources(
    layout,
):
    client = mod.InputThread.__new__(mod.InputThread)
    with pytest.raises(ValueError, match="exact loaded keyboard layout"):
        client.request(InputContext(1, 1, 2, 3), threading.Event(), layout=layout)


def test_request_rejects_simultaneous_profile_and_classic_layout():
    client = mod.InputThread.__new__(mod.InputThread)
    profile = InputProfile(
        2,
        0x409,
        str(uuid.UUID(int=0)),
        str(uuid.UUID(int=0)),
        KEYBOARD_CATEGORY,
        0,
        0x04090409,
        2,
    )
    with pytest.raises(ValueError, match="profile or a keyboard layout"):
        client.request(
            InputContext(1, 1, 2, 3), threading.Event(), profile, layout=profile.layout
        )


def test_helper_cache_is_content_addressed_and_repairs_corrupt_contents(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "input_thread.py").touch()
    (source / "input-thread-64.dll").write_bytes(b"fixture native helper")
    monkeypatch.setattr(mod, "__file__", str(source / "input_thread.py"))
    monkeypatch.setattr(mod.tempfile, "gettempdir", lambda: str(tmp_path / "local"))
    mod._runtime.cache_clear()
    try:
        target = mod._runtime(64)
        assert target.read_bytes() == b"fixture native helper"
        assert source not in target.parents
        target.write_bytes(b"corrupt")
        mod._runtime.cache_clear()
        assert mod._runtime(64).read_bytes() == b"fixture native helper"
        assert list(target.parent.glob("*.tmp")) == []
    finally:
        mod._runtime.cache_clear()


def test_input_service_only_exposes_enabled_keyboard_profiles_and_revalidates(
    monkeypatch,
):
    from contextlib import nullcontext

    from docking.platform.native.windows import input_service as service

    profile = InputProfile(
        1,
        0x409,
        str(uuid.UUID(int=1)),
        str(uuid.UUID(int=2)),
        KEYBOARD_CATEGORY,
        0,
        0,
        2,
    )
    catalog = Mock(spec=["profiles", "is_enabled", "for_layout"])
    disabled = replace(profile, identifier=str(uuid.UUID(int=4)))
    catalog.profiles.return_value = (
        profile,
        disabled,
        replace(profile, category=str(uuid.UUID(int=3))),
    )
    enabled_codes = {profile.code}
    catalog.is_enabled.side_effect = lambda item: item.code in enabled_codes
    monkeypatch.setattr(service, "apartment", nullcontext)
    monkeypatch.setattr(service, "TextProfiles", lambda _scope: catalog)
    client = service.WindowsInputService.__new__(service.WindowsInputService)
    client._thread = Mock()
    assert client.profiles() == (profile,)
    context, cancelled = InputContext(1, 1, 2, 3), threading.Event()
    client.activate(context, profile, cancelled)
    client._thread.request.assert_called_once_with(context, cancelled, profile)
    client._thread.reset_mock()
    enabled_codes.clear()
    catalog.profiles.return_value = (profile,)
    assert client.activate(context, profile, cancelled) is None
    client._thread.request.assert_not_called()


@pytest.mark.parametrize(
    "flags,enabled", [(0, True), (9, True), (2, False), (15, False)]
)
def test_input_catalog_uses_dedicated_tip_state_when_enumeration_flags_are_stale(
    monkeypatch, flags, enabled
):
    from contextlib import nullcontext

    from docking.platform.native.windows import input_service as service

    raw = InputProfile(
        1,
        0x42A,
        str(uuid.UUID(int=1)),
        str(uuid.UUID(int=2)),
        KEYBOARD_CATEGORY,
        0,
        0,
        flags,
        "Fixture TIP",
    )
    keyboard = replace(raw, kind=2, layout=0x042A042A, flags=0)
    classic = replace(raw, kind=2, language=0x409, layout=0x04090409, flags=2)
    other_category = replace(raw, category=str(uuid.UUID(int=3)))
    catalog = Mock(spec=["profiles", "is_enabled", "for_layout"])
    catalog.profiles.return_value = (raw, keyboard, classic, other_category)
    catalog.is_enabled.return_value = enabled
    monkeypatch.setattr(service, "apartment", nullcontext)
    monkeypatch.setattr(service, "TextProfiles", lambda _scope: catalog)
    client = service.WindowsInputService.__new__(service.WindowsInputService)
    client._thread = Mock()

    result = client.catalog()
    catalog.is_enabled.assert_called_once_with(raw)
    assert result.profiles == ((replace(raw, flags=flags | 2),) if enabled else ())
    assert raw.flags == flags
    assert result.keyboards == (keyboard, classic)
    assert result.keyboards[0] is keyboard and not keyboard.enabled
    assert result.keyboards[1] is classic and classic.enabled
    assert result.synthetic_layouts((keyboard.layout, classic.layout)) == {
        keyboard.code
    }

    context, cancelled = InputContext(1, 1, 2, 3), threading.Event()
    client.activate(context, raw, cancelled)
    if enabled:
        client._thread.request.assert_called_once_with(context, cancelled, raw)
    else:
        client._thread.request.assert_not_called()


def test_cancellation_before_dispatch_does_not_initialize_native_resources(tmp_path):
    client = mod.InputThread.__new__(mod.InputThread)
    cancelled = threading.Event()
    cancelled.set()
    assert (
        client._dispatch(
            InputContext(1, 1, 2, 3),
            cancelled,
            None,
            0x8664,
            64,
            tmp_path / "unused.dll",
            "unused mapping",
            mod.Request(),
        )
        is None
    )


def _host_dispatch(monkeypatch):
    """Model only the Win32 ownership boundary; no subprocess or hook is started."""
    import subprocess
    from types import SimpleNamespace

    cancelled = threading.Event()
    trace = []
    child = Mock(_handle=11)
    child.returncode = None
    child.poll.side_effect = lambda: child.returncode

    def terminate():
        trace.append("terminate")
        child.returncode = -15

    def wait(*, timeout):
        trace.append("wait")
        if child.returncode is None:
            raise subprocess.TimeoutExpired("owned input host", timeout)
        return child.returncode

    child.terminate.side_effect = terminate
    child.wait.side_effect = wait
    job = Mock()
    job.close.side_effect = lambda: trace.append("job-close")
    functions = {
        "GetTickCount64": lambda: 1000,
        "GlobalAddAtomW": lambda name: 123,
        "GlobalDeleteAtom": Mock(side_effect=lambda atom: trace.append("atom-delete")),
    }
    monkeypatch.setattr(mod, "bind", lambda library, name, *_signature: functions[name])
    monkeypatch.setattr(mod, "ProcessJob", lambda: job)
    monkeypatch.setattr(
        mod,
        "subprocess",
        SimpleNamespace(
            Popen=Mock(return_value=child),
            CREATE_NO_WINDOW=0x8000000,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    client = mod.InputThread.__new__(mod.InputThread)
    client._kernel, client._user = object(), object()
    return client, cancelled, child, job, trace, functions


def _dispatch_host(client, cancelled, tmp_path, value, profile=None, *, layout=None):
    return client._dispatch(
        InputContext(1, 1, 2, 3),
        cancelled,
        profile,
        0x14C,
        32,
        tmp_path / "input-thread-32.dll",
        "owned mapping",
        value,
        layout=layout,
    )


@pytest.mark.parametrize("layout", [0x040C040C, 0xFFFFFFFFF0020409])
def test_classic_wire_request_needs_no_profile_and_keeps_a_verified_layout(
    monkeypatch, tmp_path, layout
):
    client, cancelled, child, job, trace, _functions = _host_dispatch(monkeypatch)
    value = mod.Request()

    def complete(_handle):
        assert value.operation == 3 and value.requested.kind == 2
        assert value.requested.layout == layout & 0xFFFFFFFF
        assert value.requested.language == layout & 0xFFFF
        assert not any(bytes(value.requested.clsid))
        assert not any(bytes(value.requested.profile))
        value.state, value.observed, value.composition_bytes = (
            2,
            layout & 0xFFFFFFFF,
            -1,
        )

    job.assign.side_effect = complete
    job.close.side_effect = lambda: setattr(child, "returncode", 0)
    snapshot = _dispatch_host(client, cancelled, tmp_path, value, layout=layout)
    assert snapshot is not None and snapshot.layout == layout & 0xFFFFFFFF
    assert snapshot.profile is None and not snapshot.ime_known
    assert snapshot.composition_bytes == -1
    assert trace[-1] == "atom-delete" and value.cancelled


def test_native_failure_reports_the_validation_stage_and_observed_profile_flags(
    monkeypatch, tmp_path
):
    import pytest

    client, cancelled, child, job, trace, _functions = _host_dispatch(monkeypatch)
    value = mod.Request()
    profile = InputProfile(
        1,
        0x42A,
        str(uuid.UUID(int=1)),
        str(uuid.UUID(int=2)),
        KEYBOARD_CATEGORY,
        0,
        0,
        2,
    )

    def complete(_handle):
        value.state, value.phase, value.result = 2, 46, -2147024891
        value.requested.flags = 0

    job.assign.side_effect = complete
    job.close.side_effect = lambda: setattr(child, "returncode", 0)
    with pytest.raises(OSError) as error:
        _dispatch_host(client, cancelled, tmp_path, value, profile)
    assert isinstance(error.value, mod.InputRequestError)
    assert error.value.diagnostic.phase == 46
    assert error.value.diagnostic.hresult == 0x80070005
    assert error.value.errno == 0x80070005
    assert error.value.strerror == (
        "Input-thread request failed: phase=46 kind=1 flags=0x00000000"
    )
    assert trace[-1] == "atom-delete" and value.cancelled


def test_failed_job_assignment_reaps_the_unauthorized_host_and_releases_atom(
    monkeypatch, tmp_path
):
    import pytest

    client, cancelled, child, job, trace, functions = _host_dispatch(monkeypatch)
    job.assign.side_effect = OSError("job assignment denied")
    value = mod.Request()
    with pytest.raises(OSError, match="job assignment denied"):
        _dispatch_host(client, cancelled, tmp_path, value)
    assert not value.authorized and value.cancelled
    assert trace == ["job-close", "terminate", "wait", "atom-delete"]
    child.terminate.assert_called_once()
    functions["GlobalDeleteAtom"].assert_called_once_with(123)


def test_atom_cleanup_survives_a_host_that_cannot_be_reaped(monkeypatch, tmp_path):
    import subprocess

    import pytest

    client, cancelled, child, job, trace, functions = _host_dispatch(monkeypatch)
    job.assign.side_effect = lambda _handle: cancelled.set()
    child.wait.side_effect = subprocess.TimeoutExpired("owned input host", 3)
    value = mod.Request()
    with pytest.raises(subprocess.TimeoutExpired):
        _dispatch_host(client, cancelled, tmp_path, value)
    assert value.cancelled and not value.authorized
    assert trace[-1] == "atom-delete"
    assert child.wait.call_count == 2
    child.terminate.assert_called_once()
    functions["GlobalDeleteAtom"].assert_called_once_with(123)


def test_host_startup_time_is_included_in_the_request_deadline(monkeypatch, tmp_path):
    client, cancelled, child, job, _trace, _functions = _host_dispatch(monkeypatch)
    moment = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: moment[0])

    def start(*_args, **_kwargs):
        moment[0] = 8.0
        return child

    mod.subprocess.Popen.side_effect = start
    job.close.side_effect = lambda: setattr(child, "returncode", 0)
    value = mod.Request()
    assert _dispatch_host(client, cancelled, tmp_path, value) is None
    assert value.authorized == value.dispatched == 0
    assert value.cancelled


def test_cancellation_during_hook_installation_never_posts_activation(
    monkeypatch, tmp_path
):
    client, cancelled, _child, _job, trace, functions = _host_dispatch(monkeypatch)
    callback = ctypes.CFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
    )(lambda *_args: 0)
    library = Mock(InputHook=callback, _handle=12)
    monkeypatch.setattr(mod, "_library", lambda: library)
    functions.update(
        ProtocolSize=lambda: ctypes.sizeof(mod.Request),
        SetWindowsHookExW=lambda *_args: cancelled.set() or 99,
        RegisterWindowMessageW=lambda name: 0xC001,
        PostMessageW=Mock(return_value=True),
        UnhookWindowsHookEx=Mock(side_effect=lambda hook: trace.append("unhook")),
    )
    value = mod.Request()
    assert (
        client._dispatch(
            InputContext(1, 1, 2, 3),
            cancelled,
            None,
            0x8664,
            64,
            tmp_path / "input-thread-64.dll",
            "owned mapping",
            value,
        )
        is None
    )
    functions["PostMessageW"].assert_not_called()
    functions["UnhookWindowsHookEx"].assert_called_once_with(99)
    assert trace == ["unhook", "atom-delete"]
    assert value.cancelled


def test_request_diagnostic_is_an_immutable_copy_of_native_protocol():
    from dataclasses import FrozenInstanceError

    request = mod.Request()
    request.phase, request.result, request.machine = 111, -2147023673, 0x8664
    request.state, request.dispatched = 2, 1
    diagnostic = mod.InputRequestDiagnostic.read(request)
    request.phase = 32
    assert diagnostic.phase == 111 and diagnostic.hresult == 0x800704C7
    assert diagnostic.machine == 0x8664 and diagnostic.dispatched
    with pytest.raises(FrozenInstanceError):
        diagnostic.phase = 0


def test_controller_error_preserves_winerror_rather_than_translated_errno(
    monkeypatch, tmp_path
):
    client, cancelled, child, job, _trace, _functions = _host_dispatch(monkeypatch)
    error = OSError(22, "Win32 invalid parameter")
    error.winerror = 87
    job.assign.side_effect = error
    with pytest.raises(mod.InputRequestError) as failed:
        _dispatch_host(client, cancelled, tmp_path, mod.Request())
    assert failed.value.errno == 87 and failed.value.diagnostic.phase == 200
    assert failed.value.__cause__ is error
    assert child.terminate.called


def test_controller_plain_oserror_with_no_winerror_preserves_errno(
    monkeypatch, tmp_path
):
    client, cancelled, _child, job, _trace, _functions = _host_dispatch(monkeypatch)
    error = OSError(5, "plain OS error")
    error.winerror = None
    job.assign.side_effect = error
    with pytest.raises(mod.InputRequestError) as failed:
        _dispatch_host(client, cancelled, tmp_path, mod.Request())
    assert failed.value.errno == 5
    assert failed.value.diagnostic.phase == 200
    assert failed.value.__cause__ is error
