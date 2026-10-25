"""Real Windows power-mode/menu acceptance in an owned VM.

Uses independent C# P/Invoke through PowerShell, native keyboard/mouse input,
temporary power-plan fixtures and normal dock shutdown. Restores both supply
preferences and the original active plan, and deletes only its own plans.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

_ORACLE = r"""
using System;
using System.Runtime.InteropServices;
using System.Threading;
public static class PowerModeOracle {
 [StructLayout(LayoutKind.Sequential)] public struct Status {
  public byte ac, flags, percent, saver;
  public uint life, full;
 }
 public delegate void Changed(int mode, IntPtr context);
 [DllImport("kernel32.dll")]
 static extern bool GetSystemPowerStatus(out Status s);
 [DllImport("kernel32.dll")]
 static extern IntPtr LocalFree(IntPtr p);
 [DllImport("powrprof.dll")]
 static extern uint PowerGetActiveScheme(IntPtr root, out IntPtr p);
 [DllImport("powrprof.dll")]
 static extern uint PowerGetUserConfiguredACPowerMode(out Guid g);
 [DllImport("powrprof.dll")]
 static extern uint PowerGetUserConfiguredDCPowerMode(out Guid g);
 [DllImport("powrprof.dll")]
 static extern uint PowerSetUserConfiguredACPowerMode(ref Guid g);
 [DllImport("powrprof.dll")]
 static extern uint PowerSetUserConfiguredDCPowerMode(ref Guid g);
 [DllImport("powrprof.dll")]
 static extern int PowerRegisterForEffectivePowerModeNotifications(
  uint version, Changed cb, IntPtr ctx, out IntPtr h);
 [DllImport("powrprof.dll")]
 static extern int PowerUnregisterFromEffectivePowerModeNotifications(IntPtr h);
 static void Check(long code) {
  if(code!=0) throw new Exception("Native power error "+code);
 }
 public static void Set(string source, string value) {
  Guid g=new Guid(value);
  Check(source=="AC" ? PowerSetUserConfiguredACPowerMode(ref g)
                    : PowerSetUserConfiguredDCPowerMode(ref g));
 }
 public static object Read() {
  Guid ac,dc; Status s; IntPtr plan;
  Check(PowerGetUserConfiguredACPowerMode(out ac));
  Check(PowerGetUserConfiguredDCPowerMode(out dc));
  if(!GetSystemPowerStatus(out s))
   throw new Exception("Power status unavailable");
  Check(PowerGetActiveScheme(IntPtr.Zero,out plan)); string active;
  try {active=((Guid)Marshal.PtrToStructure(plan,typeof(Guid))).ToString();}
  finally {LocalFree(plan);}
  int effective=-1; IntPtr handle;
  using(var ready=new ManualResetEvent(false)) {
   Changed cb=(mode,ctx)=>{effective=mode;ready.Set();};
   Check(PowerRegisterForEffectivePowerModeNotifications(
    2,cb,IntPtr.Zero,out handle));
   try {
    if(!ready.WaitOne(3000)) throw new Exception("No effective-mode callback");
   } finally {
    Check(PowerUnregisterFromEffectivePowerModeNotifications(handle));
    GC.KeepAlive(cb);
   }
  }
  return new {AC=ac.ToString(),DC=dc.ToString(),source=s.ac,
   battery_saver=s.saver,plan=active,effective=effective};
 }
}
"""


def powershell(command):
    command = (
        "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    ) + command
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(command.encode("utf-16-le")).decode(),
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=40,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout)


def oracle(*, source=None, value=None):
    command = "Add-Type -TypeDefinition @'\n" + _ORACLE + "\n'@;\n"
    if source is not None:
        assert source in ("AC", "DC")
        value = str(uuid.UUID(value))
        command += f"[PowerModeOracle]::Set('{source}','{value}');"
    return powershell(command + "[PowerModeOracle]::Read()|ConvertTo-Json -Compress")


def energy_controls(hwnd):
    # Read accessibility metadata only; actual interaction uses desktop input.
    return powershell(
        r"""
Add-Type -AssemblyName UIAutomationClient;
Add-Type -AssemblyName UIAutomationTypes;
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HWND);
$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
 [System.Windows.Automation.Condition]::TrueCondition);
$rows=@();foreach($node in $nodes){
 $v=$node.Current;if($v.Name -notmatch 'energy saver'){continue};
 $toggle=$null;
 $has=$node.TryGetCurrentPattern(
  [System.Windows.Automation.TogglePattern]::Pattern,[ref]$toggle);
 $rows += [ordered]@{name=$v.Name;id=$v.AutomationId;
  type=$v.ControlType.ProgrammaticName;enabled=$v.IsEnabled;
  toggle=if($has){$toggle.Current.ToggleState.ToString()}else{$null};
  rect=@($v.BoundingRectangle.Left,$v.BoundingRectangle.Top,
   $v.BoundingRectangle.Width,$v.BoundingRectangle.Height)}
};ConvertTo-Json -InputObject $rows -Depth 5 -Compress
""".replace("HWND", str(int(hwnd)))
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.powerprofiles.state import PROFILES
    from docking.applets.powerprofiles.windows import (
        _MODES,
        WindowsPowerProfilesBackend,
        _PowerModes,
    )
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from tools.windows_desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] in ("Docking", "Settings") for w in native.windows())
    api = _PowerModes()
    baseline = oracle()
    assert baseline["source"] == 1 and baseline["battery_saver"] == 0
    assert baseline["AC"] in _MODES.values() and baseline["DC"] in _MODES.values()
    assert api.plan()[1] == "381b4222-f694-41f0-9685-ff5bb260df2e"
    assert (api.configured("AC"), api.configured("DC")) == (
        baseline["AC"],
        baseline["DC"],
    )
    workarea = native.workarea()
    report = {
        "baseline": baseline,
        "baseline_workarea": workarea,
        "states": {},
        "menus": {},
    }
    errors, shared, owned_plans = [], {}, []
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://powerprofiles"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    powercfg = str(Path(os.environ["SYSTEMROOT"]) / "System32/powercfg.exe")

    def plan_command(*argv):
        return subprocess.run(
            [powercfg, *argv],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def publish():
        temporary = output / "result.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")

    publish()
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    app.build_dock_window = build

    def call(callback):
        done, values, failures = threading.Event(), [], []

        def invoke():
            try:
                values.append(callback())
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(20), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        energy_may_be_on = False
        energy_original = None

        def energy_saver(enabled):
            nonlocal energy_original
            os.startfile("ms-settings:powersleep")
            window = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Energy Saver Settings did not map",
                30,
            )
            try:
                time.sleep(2)
                controls = energy_controls(window["hwnd"])
                identity = (
                    "SystemSettings_PowerAndBattery_EnergySaverAlwaysOn_ToggleSwitch"
                )
                toggle = next((r for r in controls if r["id"] == identity), None)
                if toggle is None:
                    group = next(
                        r
                        for r in controls
                        if r["type"] == "ControlType.Group"
                        and r["name"] == "Energy saver"
                    )
                    x, y, width, _height = group["rect"]
                    native.click(x + width - 24, y + 40)
                    controls = energy_controls(window["hwnd"])
                    toggle = next(r for r in controls if r["id"] == identity)
                assert toggle["enabled"] and toggle["toggle"] in ("On", "Off")
                before_toggle = dict(toggle)
                if energy_original is None:
                    energy_original = toggle["toggle"] == "On"
                    report["baseline_energy_saver_ui"] = before_toggle
                if (toggle["toggle"] == "On") != enabled:
                    x, y, width, height = toggle["rect"]
                    native.click(x + width * 0.8, y + height / 2)
                controls = energy_controls(window["hwnd"])
                toggle = next(r for r in controls if r["id"] == identity)
                assert toggle["toggle"] == ("On" if enabled else "Off")
                capture("energy-saver-on" if enabled else "energy-saver-off")
                return {"before": before_toggle, "after": toggle}
            finally:
                native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: (
                        not any(w["hwnd"] == window["hwnd"] for w in native.windows())
                    ),
                    "Owned Energy Saver Settings did not close",
                )

        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://powerprofiles"))
            assert isinstance(applet._backend, WindowsPowerProfilesBackend)
            shared["applet"] = applet
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )

            def point():
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://powerprofiles"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def state():
                return call(lambda: applet._state)

            def wait_profile(profile):
                native.wait_for(
                    lambda: call(
                        lambda: (
                            not applet._set_in_progress
                            and applet._state.available
                            and applet._state.active_profile == profile
                            and not applet._state.degraded_reason
                        )
                    ),
                    "Profile did not reach configured/effective state: " + profile,
                    25,
                )
                assert not call(lambda: applet._action_error)

            def menu(name):
                native.click(*call(point), right=True)
                root = native.wait_for(
                    lambda: call(
                        lambda: (
                            g
                            if isinstance(g := Gtk.grab_get_current(), Gtk.Menu)
                            else None
                        )
                    ),
                    "Power Profiles menu did not open",
                )
                rows = call(
                    lambda: [
                        {
                            "label": r.get_label(),
                            "enabled": r.get_sensitive(),
                            "active": r.get_active()
                            if isinstance(r, Gtk.RadioMenuItem)
                            else None,
                        }
                        for r in root.get_children()
                        if r.get_label()
                    ]
                )
                time.sleep(0.5)
                capture(name)
                report["menus"][name] = rows
                return root, rows

            def select(profile):
                menu("before-" + profile)
                native.key(0x24)
                for _ in range(PROFILES.index(profile)):
                    native.key(0x28)
                native.key(0x0D)
                wait_profile(profile)

            def verify(profile, name, *, plan=baseline["plan"]):
                snapshot = oracle()
                assert (
                    snapshot["AC"] == _MODES[profile]
                    and snapshot["DC"] == baseline["DC"]
                )
                assert (
                    snapshot["effective"]
                    == {"power-saver": 1, "balanced": 2, "performance": 4}[profile]
                )
                assert snapshot["plan"] == plan
                assert api.read().configured == _MODES[profile]
                report["states"][name] = {
                    "os": snapshot,
                    "applet": asdict(state()),
                    "tooltip": call(lambda: applet.item.name),
                }
                _menu, rows = menu(name)
                radios = [r for r in rows if r["active"] is not None]
                assert len(radios) == 3 and [r["active"] for r in radios] == [
                    p == profile for p in PROFILES
                ]
                native.key(0x1B)
                publish()

            def settings(name):
                assert not any(w["title"] == "Settings" for w in native.windows())
                os.startfile("ms-settings:powersleep")
                window = native.wait_for(
                    lambda: next(
                        (w for w in native.windows() if w["title"] == "Settings"), None
                    ),
                    "Native Power Settings did not map",
                    30,
                )
                time.sleep(2)
                capture(name)
                report.setdefault("settings_windows", {})[name] = window
                native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: (
                        not any(w["hwnd"] == window["hwnd"] for w in native.windows())
                    ),
                    "Owned Settings window did not close",
                )

            time.sleep(2)
            for profile in PROFILES:
                select(profile)
                verify(profile, "native-" + profile)
            settings("native-power-settings")
            native.click(*call(point))
            wait_profile("power-saver")
            verify("power-saver", "native-click-cycle")
            oracle(source="AC", value=_MODES["performance"])
            wait_profile("performance")
            verify("performance", "external-power-mode")

            energy_may_be_on = True
            energy_ui = energy_saver(True)
            effective = oracle()
            report["energy_saver"] = {"on": effective, "ui_on": energy_ui}
            publish()
            assert energy_original is False, (
                "Start with the owned VM's Energy Saver off"
            )
            assert effective["AC"] == _MODES["performance"]
            # Energy Saver Standard changes the effective mode without setting
            # SYSTEM_POWER_STATUS's older battery-saver flag on this Windows VM.
            assert effective["effective"] in (0, 1)
            native.wait_for(
                lambda: (
                    state().active_profile == "performance"
                    and bool(state().degraded_reason)
                ),
                "System Energy Saver override did not reach the applet",
                15,
            )
            _menu, rows = menu("energy-saver-override")
            radios = [r for r in rows if r["active"] is not None]
            assert [r["active"] for r in radios] == [False, False, True]
            native.key(0x1B)
            report["energy_saver"]["applet"] = asdict(state())
            report["energy_saver"]["ui_off"] = energy_saver(False)
            energy_may_be_on = False
            wait_profile("performance")
            restored_energy = oracle()
            assert restored_energy["effective"] == 4
            report["energy_saver"]["off"] = restored_energy
            publish()

            # An owned clone keeps its identity; the backend reads personality.
            cloned = str(uuid.uuid4())
            plan_command("/duplicatescheme", baseline["plan"], cloned)
            owned_plans.append(cloned)
            report["owned_plans"] = list(owned_plans)
            publish()
            plan_command("/changename", cloned, "Docking balanced power-mode fixture")
            plan_command("/setactive", cloned)
            native.wait_for(lambda: state().available, "Balanced clone unavailable", 15)
            select("balanced")
            verify("balanced", "custom-balanced-plan", plan=cloned)

            performance = str(uuid.uuid4())
            plan_command(
                "/duplicatescheme", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", performance
            )
            owned_plans.append(performance)
            report["owned_plans"] = list(owned_plans)
            publish()
            plan_command(
                "/changename", performance, "Docking performance power-mode fixture"
            )
            plan_command("/setactive", performance)
            native.wait_for(
                lambda: not state().available,
                "Non-Balanced plan did not disable modes",
                15,
            )
            assert "Balanced power plan" in state().error
            before_click = oracle()
            native.click(*call(point))
            after_click = oracle()
            assert all(after_click[k] == before_click[k] for k in ("AC", "DC", "plan"))
            _menu, rows = menu("non-balanced-plan")
            assert (
                rows[0]["label"] == "Power Profiles unavailable"
                and not rows[0]["enabled"]
            )
            native.key(0x1B)
            report["non_balanced"] = {"applet": asdict(state()), "os": before_click}
            settings("non-balanced-power-settings")
            plan_command("/setactive", baseline["plan"])
            native.wait_for(
                lambda: state().available,
                "Modes did not recover with original plan",
                15,
            )
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                try:
                    call(close)
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, close_error=repr(exc))
            if energy_may_be_on and energy_original is not None:
                try:
                    energy_saver(energy_original)
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, energy_cleanup_error=repr(exc))
            try:
                plan_command("/setactive", baseline["plan"])
                oracle(source="AC", value=baseline["AC"])
                restored = oracle(source="DC", value=baseline["DC"])
                assert all(restored[k] == baseline[k] for k in ("AC", "DC", "plan"))
                for plan in owned_plans:
                    plan_command("/delete", plan)
                listing = plan_command("/list").stdout
                assert all(plan not in listing.lower() for plan in owned_plans)
                report.update(restored=restored, owned_plans_removed=True)
            except Exception as exc:
                errors.append(exc)
                report.update(ok=False, cleanup_error=repr(exc))
            finally:
                api.stop()
                publish()

    driver = threading.Thread(
        target=drive, name="windows-powerprofiles-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(180)
        assert not driver.is_alive()
        assert native.workarea() == workarea
        applet = shared["applet"]
        assert applet._poll_id == 0 and applet._stopped
        assert (
            not applet._backend._api._registration.value
            and applet._backend._api._callback is None
        )
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
