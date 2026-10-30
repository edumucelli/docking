"""Exercise classic input and TSF composition in owned x64 and x86 text windows.

Only this acceptance fixture temporarily enables the two built-in Vietnamese
profiles. It journals their original state and restores it on every exit path.
An owned French layout exercises direct classic switching on both architectures.
The application backend never enables profiles or changes language defaults.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict
from functools import partial
from pathlib import Path

from tools.windows.visual.desktop_driver import write_shared_json
from tools.windows.visual.input_editor import OwnedInputEditor, focus_editor


def _observe_composition(document: Path, *, seconds: float = 10) -> str:
    """Keep the strict expected text while retaining a settled mismatch as evidence."""
    deadline = time.monotonic() + seconds
    while True:
        text = document.read_text(encoding="utf-8-sig")
        if text.strip() == "â" or time.monotonic() >= deadline:
            return text
        time.sleep(0.1)


def _collect_reference(*, result, mode, exercise, validate, reset) -> None:
    """Collect only text mismatches after every ownership/restoration check passes.

    No exception is recoverable here: setup, input, readback, identity and mode
    restoration failures stop the entire fixture. A known text mismatch becomes
    a completed failed row only after its context and classic reset are proven.
    """
    with mode:
        result["text"] = exercise()
        result["readback"] = validate()
    if result["ime_mode"].get("restored") is not True:
        raise RuntimeError("Owned IME mode restoration was not verified")
    result["after_restore"] = validate()
    result["classic_reset"] = reset()
    result["passed"] = result["text"].strip() == "â"


def _composition_failures(stages) -> list[dict[str, object]]:
    """Require the complete independent matrix and retain each strict failure."""
    if [stage["architecture"] for stage in stages] != [64, 32]:
        raise AssertionError("Independent x64/x86 input matrix is incomplete")
    failures: list[dict[str, object]] = []
    for stage in stages:
        profiles = stage["profiles"]
        if len(profiles) != 2 or len({p["requested"] for p in profiles}) != 2:
            raise AssertionError("Independent Telex/VNI input matrix is incomplete")
        for profile in profiles:
            variants = profile["variants"]
            if [row["routing"] for row in variants] != ["scan-code", "mapped-vk"]:
                raise AssertionError("Independent key-routing matrix is incomplete")
            for row in variants:
                if not row["passed"]:
                    failures.append(
                        {
                            "architecture": stage["architecture"],
                            "profile": profile["requested"],
                            "routing": row["routing"],
                            "expected": "â",
                            "text": row["text"],
                        }
                    )
    return failures


def _cancel_queued(native, context, profiles, gate, marker, *, layout=None):
    """Cancel a posted activation while an owned editor's input thread is paused."""
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.input_thread import InputThread

    class TracedInput(InputThread):
        def __init__(self):
            super().__init__()
            self.lock = threading.Lock()
            self.pending = None

        def _dispatch(
            self,
            context,
            cancelled,
            profile,
            machine,
            bits,
            path,
            name,
            value,
            *,
            layout=None,
        ):
            with self.lock:
                self.pending = value
            try:
                return super()._dispatch(
                    context,
                    cancelled,
                    profile,
                    machine,
                    bits,
                    path,
                    name,
                    value,
                    layout=layout,
                )
            finally:
                with self.lock:
                    self.pending = None

        def posted(self):
            with self.lock:
                return bool(
                    self.pending is not None
                    and self.pending.dispatched
                    and self.pending.state == 0
                )

    client = TracedInput()
    before = client.request(context, threading.Event())
    assert before is not None
    wanted = (
        next(
            (
                p
                for p in profiles
                if before.profile is None or p.code != before.profile.code
            ),
            None,
        )
        if layout is None
        else None
    )
    assert wanted is not None or layout is not None
    if layout is not None:
        assert layout & 0xFFFFFFFF != before.layout & 0xFFFFFFFF
    cancelled = threading.Event()
    values, errors = [], []
    worker = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signal = bind(kernel, "SetEvent", ctypes.c_int32, ctypes.c_void_p)
    reset = bind(kernel, "ResetEvent", ctypes.c_int32, ctypes.c_void_p)
    message = bind(
        native.user, "RegisterWindowMessageW", ctypes.c_uint32, ctypes.c_wchar_p
    )("Docking.OwnedInputFixture.Pause")

    def request():
        try:
            values.append(client.request(context, cancelled, wanted, layout=layout))
        except Exception as error:
            errors.append(error)

    marker.unlink(missing_ok=True)
    assert reset(gate)
    try:
        assert native.user.PostMessageW(context.root, message, 0, 0)
        native.wait_for(marker.is_file, "Owned editor did not pause its input thread")
        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        native.wait_for(
            client.posted, "Input request was not posted before cancellation", 5
        )
        cancelled.set()
        worker.join(5)
        assert not worker.is_alive(), "Cancellation waited for the foreign editor"
        if errors:
            raise errors[0]
        assert values == [None]
    finally:
        cancelled.set()
        assert signal(gate)
        if worker is not None:
            worker.join(10)
    after = client.request(context, threading.Event())
    assert after is not None and after.profile == before.profile
    assert after.layout & 0xFFFFFFFF == before.layout & 0xFFFFFFFF
    return {
        "posted": True,
        "cancelled": True,
        "unchanged_after_resume": True,
        "profile_activation": wanted is not None,
        "classic_layout": f"{layout & 0xFFFFFFFF:08x}" if layout is not None else None,
    }


def main(monitor) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument(
        "--owned-amd64-editor",
        action="store_true",
        help="Use an explicitly AMD64 Forms editor instead of stock Notepad",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test desktop")
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True)
    monitor["output_directory"] = str(output)
    from tools.windows.visual.input_requests import (
        record_input_origins,
        select_input_runtime,
    )

    runtime = {}
    try:
        runtime_root = select_input_runtime(args.installed_root, runtime)
    except Exception as error:
        write_shared_json(
            output / "result.json",
            {
                "ok": False,
                "runtime": runtime,
                "error": type(error).__name__,
            },
        )
        raise
    from docking.applets.keyboardlayout.windows import (
        WindowsKeyboardAPI,
        WindowsKeyboardBackend,
    )
    from docking.platform.backends.windows.com import apartment
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.input_profiles import TextProfiles
    from docking.platform.backends.windows.input_service import WindowsInputService
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.input_environment import (
        runtime_snapshot,
        target_modules,
        thread_snapshot,
    )
    from tools.windows.visual.input_fixture import IMEMode, ime_conversion
    from tools.windows.visual.input_oracle import oracle
    from tools.windows.visual.input_profile_fixture import (
        IDENTIFIERS,
        TELEX_IDENTIFIER,
        VIETNAMESE_CONVERSION_MODE,
        is_fixture_profile,
        profile_enabled,
        set_profile_enabled,
    )
    from tools.windows.visual.input_requests import (
        CleanupJournal,
        release_fixture_layouts,
        trace_requests,
    )

    record_input_origins(
        runtime_root,
        runtime,
        (
            "docking",
            "docking.applets.keyboardlayout.windows",
            "docking.platform.backends.windows.input_service",
            "docking.platform.backends.windows.input_thread",
        ),
    )
    report: dict = {
        "runtime": runtime,
        "ok": False,
        "stages": [],
        "scope": "owned input fixtures only",
        "text_services": monitor,
        "text_services_report": "text-services.json",
        "editor_mode": "owned-amd64" if args.owned_amd64_editor else "stock-notepad",
        "owned_editors": [],
    }

    def save() -> None:
        write_shared_json(output / "result.json", report)

    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="input-environment"
    ) as pool:
        report["input_runtime"] = pool.submit(runtime_snapshot).result(timeout=60)
    save()

    def target_facts(context, input_service):
        facts = {
            "context": asdict(context),
            "foreground_is_fixture": bool(
                editor is not None and context.root == editor["hwnd"]
            ),
        }
        if not facts["foreground_is_fixture"]:
            return facts
        facts["modules"] = target_modules(context.pid)
        try:
            snapshot = input_service.read(context, threading.Event())
            facts["target_snapshot"] = asdict(snapshot) if snapshot else None
        except OSError as error:
            facts["target_snapshot_error"] = repr(error)
        return facts

    native = WindowsDesktop()
    api = WindowsKeyboardAPI()
    backend = WindowsKeyboardBackend()
    baseline = oracle(preferences_only=True)
    report["baseline"] = baseline
    loaded_before = {handle & 0xFFFFFFFF for handle in api.layouts()}
    caller_layout = api.active(0)
    user = ctypes.WinDLL("user32", use_last_error=True)
    load = bind(
        user, "LoadKeyboardLayoutW", ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32
    )
    unload = bind(user, "UnloadKeyboardLayout", ctypes.c_int32, ctypes.c_void_p)
    activate = bind(
        user,
        "ActivateKeyboardLayout",
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    report["loaded_before"] = [f"{handle:08x}" for handle in sorted(loaded_before)]
    report["caller_layout_before"] = f"{caller_layout & 0xFFFFFFFF:08x}"
    save()
    errors: list[Exception] = []
    with (
        trace_requests(report.setdefault("request_trace", {}), save),
        apartment(),
        ExitStack() as scope,
    ):
        report["controller_thread"] = thread_snapshot(0)
        save()
        service = TextProfiles(scope)
        caller = service.active()
        profiles = tuple(p for p in service.profiles() if is_fixture_profile(p))
        report["available_profile_fixture"] = len(profiles) == 2
        report["caller_before"] = asdict(caller) if caller else None

        enabled = partial(profile_enabled, service)
        enable = partial(set_profile_enabled, service)
        report["original_enabled"] = {p.code: enabled(p) for p in profiles}

        owned: list = []
        owned_fixture_layouts: set[int] = set()
        owned_classic = None
        editor = None
        process = None
        owned_editor = None

        def record_fixture_layouts():
            # These two built-in profiles use the exact Vietnamese compatibility
            # HKL. Attribute it only when all profiles of that language were
            # initially disabled and this fixture enabled them itself.
            languages = {
                p.language
                for p in owned
                if all(
                    not report["original_enabled"][other.code]
                    for other in profiles
                    if other.language == p.language
                )
            }
            expected = {(language << 16) | language for language in languages}
            owned_fixture_layouts.update(
                handle & 0xFFFFFFFF
                for handle in api.layouts()
                if handle & 0xFFFFFFFF in expected - loaded_before
            )
            report["owned_fixture_layouts"] = [
                f"{handle:08x}" for handle in sorted(owned_fixture_layouts)
            ]

        try:
            assert {profile.identifier for profile in profiles} == IDENTIFIERS, (
                "Both built-in Vietnamese fixture profiles are required"
            )
            classic = load("0000040C", 0)
            assert classic, "Owned French layout did not load"
            if classic & 0xFFFFFFFF not in loaded_before:
                owned_classic = classic
            report["classic_fixture"] = {
                "layout": f"{classic & 0xFFFFFFFF:08x}",
                "owned": owned_classic is not None,
                "load_flags": 0,
            }
            save()
            assert classic & 0xFFFFFFFF == 0x040C040C, "French layout was substituted"
            if api.active(0) & 0xFFFFFFFF != caller_layout & 0xFFFFFFFF:
                assert activate(caller_layout, 0), "Caller layout did not restore"
            assert api.active(0) & 0xFFFFFFFF == caller_layout & 0xFFFFFFFF
            assert service.active() == caller, "Classic setup changed caller profile"
            save()
            for profile in profiles:
                if not report["original_enabled"][profile.code]:
                    owned.append(profile)
                    report["owned_profiles"] = [p.code for p in owned]
                    save()
                    enable(profile, True)
                    record_fixture_layouts()
                    save()
            for architecture in (64, 32):
                document = output / f"input-{architecture}.txt"
                document.write_text("", encoding="utf-8")
                if architecture == 64 and not args.owned_amd64_editor:
                    assert not any("Notepad" in w["title"] for w in native.windows()), (
                        "An unrelated Notepad is open"
                    )
                    os.startfile(str(document))
                    editor = native.wait_for(
                        lambda document=document: next(
                            (
                                w
                                for w in native.windows()
                                if document.name in w["title"]
                                and "Notepad" in w["title"]
                            ),
                            None,
                        ),
                        "Owned Notepad did not open",
                        40,
                    )
                else:
                    owned_editor = OwnedInputEditor(
                        native,
                        output / f"editor-{architecture}",
                        document,
                        bits=architecture,
                        machine=WindowsInputService()._thread._machine,
                    )
                    report["owned_editors"].append(owned_editor.journal)
                    save()
                    editor = owned_editor.start()
                    process = owned_editor.process
                    gate, marker = owned_editor.gate, owned_editor.marker
                    assert gate is not None

                def focus(editor=editor, owned_editor=owned_editor):
                    focus_editor(
                        native,
                        editor,
                        owned=owned_editor,
                        journal=report.setdefault("editor_focus", {}),
                    )
                    context = api.foreground()
                    assert context and context.root == editor["hwnd"]
                    return context

                context = focus()
                input_service = WindowsInputService()
                actual_machine = input_service._thread._machine(context.pid)
                assert actual_machine == (0x8664 if architecture == 64 else 0x14C)
                initial = input_service.read(context, threading.Event())
                report["stages"].append(
                    {
                        "architecture": architecture,
                        "initial": asdict(initial) if initial else None,
                        "initial_environment": target_facts(context, input_service),
                        "profiles": [],
                    }
                )
                stage = report["stages"][-1]
                save()
                assert initial and initial.layout, "No input-thread layout snapshot"
                assert initial.layout & 0xFFFFFFFF == 0x04090409, (
                    "Fixture needs initial US"
                )
                stage["classic_switches"] = []
                for wanted in (classic, initial.layout):
                    context = focus()
                    assert api.request(context, wanted, threading.Event()), (
                        "Direct classic request failed"
                    )
                    readback = api.active(context.thread)
                    assert readback & 0xFFFFFFFF == wanted & 0xFFFFFFFF
                    assert api.active(0) & 0xFFFFFFFF == caller_layout & 0xFFFFFFFF, (
                        "Classic request changed caller layout"
                    )
                    assert service.active() == caller, (
                        "Classic request changed caller profile"
                    )
                    stage["classic_switches"].append(
                        {
                            "requested": f"{wanted & 0xFFFFFFFF:08x}",
                            "readback": f"{readback & 0xFFFFFFFF:08x}",
                            "caller_unchanged": True,
                        }
                    )
                    save()

                def caller_unchanged():
                    assert api.active(0) & 0xFFFFFFFF == caller_layout & 0xFFFFFFFF, (
                        "Reference input changed caller layout"
                    )
                    assert service.active() == caller, (
                        "Reference input changed caller profile"
                    )

                for profile in sorted(
                    profiles, key=lambda p: p.identifier != TELEX_IDENTIFIER
                ):
                    profile_result = {"requested": profile.code, "variants": []}
                    stage["profiles"].append(profile_result)
                    for routing in ("scan-code", "mapped-vk"):
                        context = focus()
                        # Clear the previous route while the owned editor uses
                        # classic US, before entering a new IME conversion scope.
                        assert api.active(context.thread) & 0xFFFFFFFF == 0x04090409
                        caller_unchanged()
                        native.key(0x24, control=True)
                        native.key(0x23, control=True, shift=True)
                        native.key(0x08)
                        if architecture == 64:
                            native.key(0x53, control=True)
                        native.wait_for(
                            lambda document=document: (
                                document.read_text(encoding="utf-8-sig") == ""
                            ),
                            "Owned reference document did not reset to empty",
                            10,
                        )
                        assert api.foreground() == context, (
                            "Reference reset changed the owned typing window"
                        )
                        variant = {
                            "routing": routing,
                            "expected": "â",
                            "document_reset": True,
                            "ime_mode": {},
                            "input_observations": {
                                "before_selection": target_facts(context, input_service)
                            },
                        }
                        profile_result["variants"].append(variant)
                        save()
                        state = None

                        def profile_available(code=profile.code):
                            nonlocal state
                            state = backend.query()
                            record_fixture_layouts()
                            return state if code in state.available else None

                        try:
                            state = native.wait_for(
                                profile_available,
                                "Enabled fixture did not reach "
                                "the available input catalog",
                                15,
                            )
                        except AssertionError:
                            stage["profile_catalog_timeout"] = {
                                "requested": profile.code,
                                "state": state._asdict() if state is not None else None,
                                "enabled": {p.code: enabled(p) for p in profiles},
                                "profiles": [
                                    asdict(p)
                                    for p in service.profiles()
                                    if is_fixture_profile(p)
                                ],
                                "loaded": [
                                    f"{handle & 0xFFFFFFFF:08x}"
                                    for handle in api.layouts()
                                ],
                            }
                            save()
                            raise
                        backend.switch(profile.code, context=state.context)
                        actual = backend.query()
                        selected = api.foreground()
                        assert selected == context, (
                            "Profile activation changed the owned typing window"
                        )
                        assert actual.active == profile.code, (
                            profile.code,
                            actual.active,
                        )
                        variant["input_observations"]["after_activation"] = (
                            target_facts(context, input_service)
                        )
                        layout = api.active(context.thread)
                        variant["target_layout"] = f"{layout & 0xFFFFFFFF:08x}"
                        save()

                        def validate_profile(
                            context=context,
                            input_service=input_service,
                            profile=profile,
                            layout=layout,
                        ):
                            if api.foreground() != context:
                                raise RuntimeError(
                                    "Owned reference typing window changed"
                                )
                            snapshot = input_service.read(context, threading.Event())
                            assert (
                                snapshot
                                and snapshot.profile
                                and snapshot.profile.code == profile.code
                                and snapshot.layout & 0xFFFFFFFF == layout & 0xFFFFFFFF
                            ), "Owned reference profile/layout readback changed"
                            caller_unchanged()
                            record_fixture_layouts()
                            return asdict(snapshot)

                        def mode_readback(validate_profile=validate_profile):
                            snapshot = validate_profile()
                            if not snapshot["ime_known"]:
                                raise RuntimeError("Owned IME state is unknown")
                            return IMEMode(
                                snapshot["ime_open"],
                                snapshot["conversion"],
                                snapshot["sentence"],
                            )

                        def exercise(
                            variant=variant,
                            context=context,
                            input_service=input_service,
                            routing=routing,
                            layout=layout,
                            profile=profile,
                            architecture=architecture,
                            document=document,
                            validate_profile=validate_profile,
                        ):
                            variant["input_observations"]["after_mode_setup"] = (
                                target_facts(context, input_service)
                            )
                            save()
                            validate_profile()
                            native.scan(
                                0x1E, mapped_vk=routing == "mapped-vk", layout=layout
                            )
                            validate_profile()
                            native.scan(
                                0x1E
                                if profile.identifier == TELEX_IDENTIFIER
                                else 0x07,
                                mapped_vk=routing == "mapped-vk",
                                layout=layout,
                            )
                            variant["input_observations"]["after_first_keys"] = (
                                target_facts(context, input_service)
                            )
                            save()
                            validate_profile()
                            native.key(0x20)
                            if architecture == 64:
                                native.key(0x53, control=True)
                            return _observe_composition(document)

                        def reset_classic(
                            stage=stage,
                            layout=layout,
                            context=context,
                            input_service=input_service,
                            validate_profile=validate_profile,
                        ):
                            validate_profile()
                            stage["keyboard_profiles"] = [
                                asdict(p) for p in service.profiles() if p.kind == 2
                            ]
                            state = backend.query()
                            plain = f"win32:{layout & 0xFFFFFFFF:08x}"
                            if not any(
                                p["layout"] & 0xFFFFFFFF == layout & 0xFFFFFFFF
                                and p["flags"] & 2
                                for p in stage["keyboard_profiles"]
                            ):
                                assert plain not in state.available, (
                                    "Disabled synthetic keyboard was offered"
                                )
                                stage["synthetic_layout_hidden"] = True
                            backend.switch("win32:04090409", context=state.context)
                            native.wait_for(
                                lambda: backend.query().active == "win32:04090409",
                                "Switching from a text service back to US "
                                "did not complete",
                            )
                            assert api.foreground() == context, (
                                "Classic reset changed the owned typing window"
                            )
                            snapshot = input_service.read(context, threading.Event())
                            assert (
                                snapshot
                                and snapshot.layout & 0xFFFFFFFF == 0x04090409
                                and snapshot.profile
                                and snapshot.profile.kind == 2
                            ), "Classic reset did not restore the target input thread"
                            caller_unchanged()
                            stage["returned_to_classic"] = True
                            return asdict(snapshot)

                        try:
                            _collect_reference(
                                result=variant,
                                mode=ime_conversion(
                                    window=context.window,
                                    root=context.root,
                                    pid=context.pid,
                                    thread=context.thread,
                                    journal=variant["ime_mode"],
                                    readback=mode_readback,
                                    conversion=VIETNAMESE_CONVERSION_MODE,
                                ),
                                exercise=exercise,
                                validate=validate_profile,
                                reset=reset_classic,
                            )
                        finally:
                            save()
                    profile_result["passed"] = all(
                        variant["passed"] for variant in profile_result["variants"]
                    )
                    save()
                if architecture == 32:
                    context = focus()
                    report["queued_cancellation"] = _cancel_queued(
                        native, context, profiles, gate, marker
                    )
                    save()
                    classic = next(
                        h
                        for h in api.layouts()
                        if h & 0xFFFFFFFF != api.active(context.thread) & 0xFFFFFFFF
                    )
                    report["queued_classic_cancellation"] = _cancel_queued(
                        native, context, profiles, gate, marker, layout=classic
                    )
                    save()
                if owned_editor is not None:
                    owned_editor.close()
                    owned_editor = None
                else:
                    native.user.PostMessageW(editor["hwnd"], 0x10, 0, 0)
                    native.wait_for(
                        lambda editor=editor: (
                            not any(
                                w["hwnd"] == editor["hwnd"] for w in native.windows()
                            )
                        ),
                        "Owned input window did not close",
                        30,
                    )
                editor = None
                if process is not None:
                    assert process.wait(timeout=10) == 0
                    process = None
            report["composition_failures"] = _composition_failures(report["stages"])
            save()
        except Exception as error:
            errors.append(error)
            report["ok"], report["error"] = False, repr(error)
            cleanup = CleanupJournal(report, errors, save)

            def failure_environment():
                context = api.foreground()
                if context is not None:
                    report["failure_environment"] = target_facts(
                        context, WindowsInputService()
                    )

            cleanup.attempt("failure-environment", failure_environment)
        finally:
            cleanup = CleanupJournal(report, errors, save)
            cleanup.attempt("backend-stop", backend.stop)

            def close_editor():
                nonlocal owned_editor, editor, process
                if owned_editor is not None:
                    owned_editor.close()
                    owned_editor = None
                    editor, process = None, None
                elif editor is not None:
                    native.user.PostMessageW(editor["hwnd"], 0x10, 0, 0)
                    native.wait_for(
                        lambda: (
                            not any(
                                w["hwnd"] == editor["hwnd"] for w in native.windows()
                            )
                        ),
                        "Owned input window did not close during cleanup",
                        30,
                    )
                    editor = None
                if process is not None:
                    process.wait(timeout=10)
                    process = None
                report["editors_closed"] = True

            cleanup.attempt("owned-editors", close_editor)
            cleanup.attempt("record-owned-layouts", record_fixture_layouts)
            for profile in reversed(owned):
                cleanup.attempt(
                    "disable-profile-" + profile.identifier,
                    lambda profile=profile: enable(profile, False),
                )

            def restored_profiles():
                report["enabled_after"] = {p.code: enabled(p) for p in profiles}
                report["profiles_restored"] = (
                    report["enabled_after"] == report["original_enabled"]
                )
                assert report["profiles_restored"], (
                    "Fixture profile settings did not restore"
                )

            cleanup.attempt("profile-state", restored_profiles)

            # No compatibility layout is released while the tool still holds
            # any TSF service/manager interface or an owned editor input queue.
            def release_interfaces():
                scope.close()
                report["text_service_references_released"] = True

            cleanup.attempt("text-service-references", release_interfaces)

            def release_classic():
                report["owned_classic_released"] = owned_classic is None
                if owned_classic is not None:
                    assert editor is None and process is None and owned_editor is None
                    report["owned_classic_released"] = bool(unload(owned_classic))
                    assert report["owned_classic_released"], (
                        "Owned French layout did not unload"
                    )

            cleanup.attempt("owned-classic-layout", release_classic)
            cleanup.attempt(
                "owned-compatibility-layouts",
                lambda: release_fixture_layouts(
                    owned=owned_fixture_layouts,
                    baseline=loaded_before,
                    loaded=api.layouts,
                    unload=unload,
                    targets_closed=editor is None
                    and process is None
                    and owned_editor is None,
                    profiles_restored=(
                        report.get("profiles_restored", False)
                        and report.get("text_service_references_released", False)
                    ),
                    journal=report,
                ),
            )

            def controller_restoration():
                controller_loaded_after = {
                    handle & 0xFFFFFFFF for handle in api.layouts()
                }
                report["controller_loaded_after"] = [
                    f"{h:08x}" for h in sorted(controller_loaded_after)
                ]
                report["loaded_set_restored"] = controller_loaded_after == loaded_before
                report["caller_layout_restored"] = (
                    api.active(0) & 0xFFFFFFFF == caller_layout & 0xFFFFFFFF
                )
                report["unexpected_controller_layouts"] = [
                    f"{h:08x}" for h in sorted(controller_loaded_after - loaded_before)
                ]
                report["missing_controller_layouts"] = [
                    f"{h:08x}" for h in sorted(loaded_before - controller_loaded_after)
                ]
                assert report["loaded_set_restored"], (
                    "Controller loaded set did not restore"
                )
                assert report["caller_layout_restored"], "Caller layout did not restore"

            cleanup.attempt("controller-restoration", controller_restoration)

            def independent_restoration():
                report["restored"] = oracle(preferences_only=True)
                report["independent_loaded_set_restored"] = {
                    entry["code"] for entry in report["restored"]["loaded"]
                } == {entry["code"] for entry in baseline["loaded"]}
                report["preferences_restored"] = all(
                    baseline[key] == report["restored"][key]
                    for key in ("default", "preload", "substitutes", "wheel_route")
                )
                assert report["preferences_restored"], (
                    "Independent input preferences did not restore"
                )
                assert report["independent_loaded_set_restored"], (
                    "Independent loaded set did not restore"
                )

            cleanup.attempt("independent-restoration", independent_restoration)
            cleanup.attempt("final-report", save)
    if errors:
        report.setdefault("error", repr(errors[0]))
        try:
            save()
        except Exception as error:
            errors.append(error)
        raise errors[0]
    try:
        assert report["preferences_restored"]
        assert report["owned_classic_released"] and report["profiles_restored"]
        assert report["independent_loaded_set_restored"]
        assert not report["unexpected_controller_layouts"]
        assert not report["missing_controller_layouts"]
        assert report["caller_layout_restored"]
        assert not report["composition_failures"], (
            f"{len(report['composition_failures'])}/8 independent input references "
            "did not compose physical input as â"
        )
    except AssertionError as error:
        report["error"] = repr(error)
        save()
        raise
    report["ok"] = True
    save()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if sys.platform != "win32":
        main({})
    else:
        from tools.windows.visual.input_environment import text_services

        monitor = {}
        try:
            with text_services() as monitor:
                main(monitor)
        finally:
            if "output_directory" in monitor:
                write_shared_json(
                    Path(monitor["output_directory"]) / "text-services.json", monitor
                )
