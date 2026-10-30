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
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict
from functools import partial
from pathlib import Path

_X86_EDITOR = r"""
using System;
using System.IO;
using System.Text;
using System.Windows.Forms;
using System.Threading;
using System.Runtime.InteropServices;
public sealed class PauseForm : Form {
 [DllImport("user32.dll", CharSet=CharSet.Unicode)]
 static extern int RegisterWindowMessage(string name);
 readonly int message=RegisterWindowMessage("Docking.OwnedInputFixture.Pause");
 readonly EventWaitHandle gate;
 readonly string journal;
 public PauseForm(string name,string path) {
  gate=EventWaitHandle.OpenExisting(name);journal=path;
 }
 protected override void WndProc(ref Message m) {
  if(m.Msg==message) {
   File.WriteAllText(journal,"paused");gate.WaitOne(30000);return;
  }
  base.WndProc(ref m);
 }
 protected override void Dispose(bool disposing) {
  if(disposing)gate.Dispose();base.Dispose(disposing);
 }
}
public static class InputFixture {
 [STAThread] public static void Main(string[] args) {
  var form=new PauseForm(args[2],args[3]);
  form.Text=args[0];form.Width=520;form.Height=300;
  var entry=new TextBox();entry.Multiline=true;entry.Dock=DockStyle.Fill;
  entry.TextChanged += delegate {
   File.WriteAllText(args[1],entry.Text,new UTF8Encoding(false));
  };
  form.Controls.Add(entry);form.Shown += delegate { entry.Focus(); };
  Application.Run(form);
 }
}
"""


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
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test desktop")
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True)
    monitor["output_directory"] = str(output)
    if args.installed_root:
        sys.path.insert(0, str(args.installed_root.resolve()))
    from docking.applets.keyboardlayout.windows import (
        WindowsKeyboardAPI,
        WindowsKeyboardBackend,
    )
    from docking.platform.backends.windows.com import apartment
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.input_profiles import TextProfiles
    from docking.platform.backends.windows.input_service import WindowsInputService
    from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
    from tools.windows.visual.input_environment import (
        runtime_snapshot,
        target_modules,
        thread_snapshot,
    )
    from tools.windows.visual.input_profile_fixture import (
        TELEX_IDENTIFIER,
        is_fixture_profile,
        profile_enabled,
        set_profile_enabled,
    )
    from tools.windows.visual.keyboardlayout_smoke import oracle

    report: dict = {
        "stages": [],
        "scope": "owned input fixtures only",
        "text_services": monitor,
        "text_services_report": "text-services.json",
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
    baseline = oracle()
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
    with apartment(), ExitStack() as scope:
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
                if architecture == 64:
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
                    source, executable = output / "editor.cs", output / "editor32.exe"
                    source.write_text(_X86_EDITOR, encoding="utf-8")
                    compiler = (
                        Path(os.environ["SYSTEMROOT"])
                        / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
                    )
                    subprocess.run(
                        [
                            str(compiler),
                            "/nologo",
                            "/target:winexe",
                            "/platform:x86",
                            "/reference:System.Windows.Forms.dll",
                            f"/out:{executable}",
                            str(source),
                        ],
                        check=True,
                        capture_output=True,
                        timeout=30,
                    )
                    title = "Docking owned x86 input fixture"
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    gate_name = "Local\\DockingInputFixture." + uuid.uuid4().hex
                    gate = bind(
                        kernel,
                        "CreateEventW",
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_int32,
                        ctypes.c_int32,
                        ctypes.c_wchar_p,
                    )(None, True, False, gate_name)
                    assert gate
                    scope.callback(
                        bind(kernel, "CloseHandle", ctypes.c_int32, ctypes.c_void_p),
                        gate,
                    )
                    marker = output / "editor-paused.txt"
                    process = subprocess.Popen(
                        [str(executable), title, str(document), gate_name, str(marker)]
                    )
                    editor = native.wait_for(
                        lambda process=process, title=title: next(
                            (
                                w
                                for w in native.windows()
                                if w["pid"] == process.pid and w["title"] == title
                            ),
                            None,
                        ),
                        "Owned x86 editor did not open",
                        30,
                    )
                left, top, _right, _bottom = editor["rect"]

                def focus(left=left, top=top, editor=editor):
                    native.click(left + 150, top + 160)
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
                for profile in sorted(
                    profiles, key=lambda p: p.identifier != TELEX_IDENTIFIER
                ):
                    context = focus()
                    profile_result = {
                        "requested": profile.code,
                        "input_observations": {
                            "before_selection": target_facts(context, input_service)
                        },
                    }
                    stage["profiles"].append(profile_result)
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
                            "Enabled fixture did not reach the available input catalog",
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
                                f"{handle & 0xFFFFFFFF:08x}" for handle in api.layouts()
                            ],
                        }
                        save()
                        raise
                    backend.switch(profile.code, context=state.context)
                    actual = backend.query()
                    context = api.foreground()
                    assert context is not None
                    profile_result["input_observations"]["after_activation"] = (
                        target_facts(context, input_service)
                    )
                    save()
                    assert context.root == editor["hwnd"], (
                        "Profile activation changed fixture foreground"
                    )
                    assert actual.active == profile.code, (profile.code, actual.active)
                    # Legacy multiline Windows Forms editors do not uniformly
                    # implement Ctrl+A. Select the document with native navigation.
                    native.key(0x24, control=True)
                    native.key(0x23, control=True, shift=True)
                    native.key(0x08)
                    native.scan(0x1E)
                    native.scan(
                        0x1E if profile.identifier == TELEX_IDENTIFIER else 0x07
                    )
                    profile_result["input_observations"]["after_first_keys"] = (
                        target_facts(context, input_service)
                    )
                    save()
                    native.key(0x20)
                    if architecture == 64:
                        native.key(0x53, control=True)
                    native.wait_for(
                        lambda document=document: (
                            document.read_text(encoding="utf-8-sig").strip() == "â"
                        ),
                        "Selected profile did not compose the expected physical input",
                        10,
                    )
                    snapshot = input_service.read(api.foreground(), threading.Event())
                    assert (
                        snapshot
                        and snapshot.profile
                        and snapshot.profile.code == profile.code
                    )
                    assert service.active() == caller, "Caller input profile changed"
                    record_fixture_layouts()
                    profile_result.update(
                        readback=asdict(snapshot),
                        text=document.read_text(encoding="utf-8-sig"),
                    )
                    save()
                stage["keyboard_profiles"] = [
                    asdict(p) for p in service.profiles() if p.kind == 2
                ]
                state = backend.query()
                plain = f"win32:{api.active(context.thread) & 0xFFFFFFFF:08x}"
                if not any(
                    p["layout"] & 0xFFFFFFFF == api.active(context.thread) & 0xFFFFFFFF
                    and p["flags"] & 2
                    for p in stage["keyboard_profiles"]
                ):
                    assert plain not in state.available, (
                        "Disabled synthetic keyboard was offered"
                    )
                    stage["synthetic_layout_hidden"] = True
                state = backend.query()
                backend.switch("win32:04090409", context=state.context)
                native.wait_for(
                    lambda: backend.query().active == "win32:04090409",
                    "Switching from a text service back to US did not complete",
                )
                stage["returned_to_classic"] = True
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
                native.user.PostMessageW(editor["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda editor=editor: (
                        not any(w["hwnd"] == editor["hwnd"] for w in native.windows())
                    ),
                    "Owned input window did not close",
                    30,
                )
                editor = None
                if process is not None:
                    assert process.wait(timeout=10) == 0
                    process = None
            report["ok"] = True
        except Exception as error:
            report["ok"], report["error"] = False, repr(error)
            context = api.foreground()
            if context is not None:
                report["failure_environment"] = target_facts(
                    context, WindowsInputService()
                )
            save()
            raise
        finally:
            backend.stop()
            try:
                if editor is not None:
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
            finally:
                try:
                    record_fixture_layouts()
                    with ExitStack() as restore:
                        for profile in owned:
                            restore.callback(enable, profile, False)
                finally:
                    report["owned_classic_released"] = owned_classic is None
                    if owned_classic is not None and editor is None and process is None:
                        report["owned_classic_released"] = bool(unload(owned_classic))
                    controller_loaded_after = {
                        handle & 0xFFFFFFFF for handle in api.layouts()
                    }
                    report["controller_loaded_after"] = [
                        f"{handle:08x}" for handle in sorted(controller_loaded_after)
                    ]
                    report["loaded_set_restored"] = (
                        controller_loaded_after == loaded_before
                    )
                    report["caller_layout_restored"] = (
                        api.active(0) & 0xFFFFFFFF == caller_layout & 0xFFFFFFFF
                    )
                    save()
                report["enabled_after"] = {p.code: enabled(p) for p in profiles}
                report["profiles_restored"] = (
                    report["enabled_after"] == report["original_enabled"]
                )
                report["restored"] = oracle()
                report["independent_loaded_set_restored"] = {
                    entry["code"] for entry in report["restored"]["loaded"]
                } == {entry["code"] for entry in baseline["loaded"]}
                retained = (
                    owned_fixture_layouts if report["profiles_restored"] else set()
                )
                extras = controller_loaded_after - loaded_before
                report["retained_owned_fixture_layouts"] = [
                    f"{handle:08x}" for handle in sorted(extras & retained)
                ]
                report["unexpected_controller_layouts"] = [
                    f"{handle:08x}" for handle in sorted(extras - retained)
                ]
                report["missing_controller_layouts"] = [
                    f"{handle:08x}"
                    for handle in sorted(loaded_before - controller_loaded_after)
                ]
                report["preferences_restored"] = all(
                    baseline[key] == report["restored"][key]
                    for key in ("default", "preload", "substitutes", "wheel_route")
                )
                save()
    assert report["preferences_restored"]
    assert report["owned_classic_released"] and report["profiles_restored"]
    assert report["independent_loaded_set_restored"]
    assert not report["unexpected_controller_layouts"]
    assert not report["missing_controller_layouts"]
    assert report["caller_layout_restored"]
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
                from tools.windows.visual.desktop_driver import write_shared_json

                write_shared_json(
                    Path(monitor["output_directory"]) / "text-services.json", monitor
                )
