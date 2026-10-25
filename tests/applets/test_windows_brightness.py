"""Windows display identity, real value readback, and monitor ownership."""

import base64
import ctypes
import subprocess
import sys
from types import SimpleNamespace

import pytest

from docking.applets.brightness import windows as mod
from docking.applets.brightness.state import BrightnessState

PANEL = mod.Display(42, r"\\.\DISPLAY1", r"\\?\DISPLAY#LCD123#abc#{guid}")


class MonitorAPI:
    def __init__(self):
        self.display = PANEL
        self.levels = {"ddc": 0.5, "wmi": None}
        self.calls = []

    def primary(self):
        return self.display

    def _access(self, provider, display, value):
        self.calls.append((provider, display, value))
        if value is not None:
            self.levels[provider] = round(value * 5) / 5
        level = self.levels[provider]
        return mod.MonitorBrightness(level) if level is not None else None

    def ddc(self, display, *, value=None):
        return self._access("ddc", display, value)

    def wmi(self, display, *, value=None):
        return self._access("wmi", display, value)


@pytest.mark.parametrize("provider", ["ddc", "wmi"])
def test_writes_read_back_discrete_hardware_level(provider):
    api = MonitorAPI()
    api.levels = {"ddc": None, "wmi": None} | {provider: 0.5}
    backend = mod.WindowsBrightnessBackend(api=api)
    state = backend.get_state()
    assert state.available and state.level == 0.5
    actual = backend.set_brightness(target=state.target, value=0.52)
    assert actual.level == 0.6
    assert actual.target == state.target
    assert [call for call in api.calls if call[2] is not None] == [
        (provider, PANEL, 0.52)
    ]


def test_stale_target_does_not_change_new_primary_or_a_new_transport():
    api = MonitorAPI()
    backend = mod.WindowsBrightnessBackend(api=api)
    original = backend.get_state()
    api.display = mod.Display(99, PANEL.name, "different-device-same-display-number")
    assert (
        backend.set_brightness(value=1, target=original.target).target
        != original.target
    )
    api.display = PANEL
    api.levels = {"ddc": None, "wmi": 0.7}
    assert backend.set_brightness(value=1, target=original.target).level == 0.7
    assert all(call[2] is None for call in api.calls)


@pytest.mark.parametrize("value", [None, float("nan"), -0.1, 1.1])
def test_missing_or_invalid_levels_are_unavailable(value):
    api = MonitorAPI()
    api.levels = {"ddc": value, "wmi": None}
    assert mod.WindowsBrightnessBackend(api=api).get_state() == BrightnessState()


def test_hotplug_mid_read_cannot_publish_or_write_a_different_panel():
    api = MonitorAPI()

    def removed(*_args):
        api.display = None
        return mod.MonitorBrightness(0.5)

    api.ddc = removed
    backend = mod.WindowsBrightnessBackend(api=api)
    assert backend.get_state() == BrightnessState()
    assert (
        backend.set_brightness(target="ddc:" + PANEL.identity, value=1)
        == BrightnessState()
    )


def test_stop_prevents_new_native_access():
    api = MonitorAPI()
    backend = mod.WindowsBrightnessBackend(api=api)
    state = backend.get_state()
    api.calls.clear()
    backend.stop()
    assert backend.get_state() == BrightnessState()
    assert backend.set_brightness(target=state.target, value=1) == BrightnessState()
    assert api.calls == []


@pytest.fixture
def ddc_api():
    api = mod.WindowsMonitorAPI.__new__(mod.WindowsMonitorAPI)
    api.primary = lambda: PANEL
    values = SimpleNamespace(
        minimum=20,
        current=120,
        maximum=220,
        caps=2,
        denied=False,
        destroyed=0,
        writes=[],
        count=1,
        read_ok=True,
    )

    def count(_monitor, result):
        result._obj.value = values.count
        return 1

    def physical(_monitor, _count, result):
        result[0].handle = 987
        return 1

    def caps(_monitor, result, _temperatures):
        result._obj.value = values.caps
        return 1

    def get(_monitor, minimum, current, maximum):
        minimum._obj.value, current._obj.value, maximum._obj.value = (
            values.minimum,
            values.current,
            values.maximum,
        )
        return values.read_ok

    def set_level(_monitor, requested):
        values.writes.append(requested)
        if not values.denied:
            values.current = requested
        return not values.denied

    def destroy(_count, _handles):
        values.destroyed += 1
        return 1

    api._count, api._physical, api._caps = count, physical, caps
    api._get, api._set, api._destroy = get, set_level, destroy
    return api, values


def test_ddc_nonzero_minimum_mapping_and_every_handle_is_released(ddc_api):
    api, values = ddc_api
    assert api.ddc(PANEL).level == 0.5
    assert api.ddc(PANEL, value=0.52).level == 0.52
    assert values.writes == [124]
    assert values.destroyed == 2


def test_ddc_denied_write_reports_actual_value(ddc_api):
    api, values = ddc_api
    values.denied = True
    assert api.ddc(PANEL, value=1).level == 0.5
    assert values.destroyed == 1


@pytest.mark.parametrize(
    "failure", ["no-capability", "invalid-range", "read-failed", "hotplug"]
)
def test_ddc_failure_releases_handles_and_never_writes(ddc_api, failure):
    api, values = ddc_api
    if failure == "no-capability":
        values.caps = 0
    elif failure == "invalid-range":
        values.maximum = values.minimum
    elif failure == "read-failed":
        values.read_ok = False
    else:
        calls = iter((PANEL, None))
        api.primary = lambda: next(calls)
    assert api.ddc(PANEL, value=0.8) is None
    assert values.writes == []
    assert values.destroyed == 1


def test_ddc_clone_is_not_guessed(ddc_api):
    api, values = ddc_api
    values.count = 2
    assert api.ddc(PANEL, value=0.8) is None
    assert values.destroyed == 0 and values.writes == []


def test_wmi_identity_is_exact_not_first_matching_output_name():
    assert mod._wmi_identity(PANEL.identity) == r"display\lcd123\abc"
    assert mod._wmi_identity("LCD123") is None
    assert mod._wmi_identity(r"\\?\DISPLAY#LCD123#abc#{guid}#extra") is None


@pytest.mark.parametrize("stdout", ["null", "-1", "101", "true", '"50"', "not-json"])
def test_wmi_rejects_unavailable_and_malformed_results(monkeypatch, stdout):
    api = mod.WindowsMonitorAPI.__new__(mod.WindowsMonitorAPI)
    api.primary = lambda: PANEL
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )
    assert api.wmi(PANEL) is None


def test_wmi_uses_bounded_hidden_process_and_encoded_identity(monkeypatch):
    api = mod.WindowsMonitorAPI.__new__(mod.WindowsMonitorAPI)
    api.primary = lambda: PANEL
    observed = []

    def run(argv, **kwargs):
        observed.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0, stdout='{"current":60,"levels":[0,20,40,60,100]}'
        )

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert api.wmi(PANEL, value=0.6) == mod.MonitorBrightness(0.6, (0.2, 0.4, 0.6, 1.0))
    argv, kwargs = observed[0]
    assert argv[-2] == "-EncodedCommand"
    script = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "REQUEST" not in script and "WmiSetBrightness" in script
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 5

    def hung(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(mod.subprocess, "run", hung)
    assert api.wmi(PANEL) is None


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows SDK ABI and native providers"
)
def test_native_abi_and_actual_read_only_provider_cycles():
    assert ctypes.sizeof(mod._MonitorInfo) == 104
    assert ctypes.sizeof(mod._DisplayDevice) == 840
    assert ctypes.sizeof(mod._PhysicalMonitor) == 264
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )

    def handles():
        result = ctypes.c_uint32()
        assert kernel.GetProcessHandleCount(
            kernel.GetCurrentProcess(), ctypes.byref(result)
        )
        return result.value

    backend = mod.WindowsBrightnessBackend()
    backend.get_state()  # Warm DLLs and provider infrastructure before measuring.
    before = handles()
    for _ in range(8):
        state = backend.get_state()
        assert state.level is None or 0 <= state.level <= 1
    assert handles() <= before + 2
    backend.stop()


# These are explicit provider fixtures executed by real Windows PowerShell.
# They validate CIM script syntax, identity matching, method arguments and
# discrete levels; they make no claim about physical hardware acceptance.
_CIM_FIXTURE = r"""
$global:panel=[pscustomobject]@{Active=$true;InstanceName='DISPLAY\LCD123\abc_0';
 CurrentBrightness=40;Level=@(0,20,40,60,100)};
function Get-CimInstance {
 param($Namespace,$ClassName)
 if($ClassName -eq 'WmiMonitorBrightness'){$global:panel}
 else {[pscustomobject]@{Active=$true;InstanceName=$global:panel.InstanceName}}
}
function Invoke-CimMethod {
 param($InputObject,$MethodName,$Arguments)
 if($MethodName -ne 'WmiSetBrightness' -or
  $Arguments.Timeout -ne 0 -or $Arguments.Brightness -isnot [byte]){throw 'Invalid method'};
 $global:panel.CurrentBrightness=$Arguments.Brightness;
 [pscustomobject]@{ReturnValue=0}
}
"""


@pytest.mark.skipif(
    sys.platform != "win32", reason="Actual Windows PowerShell CIM fixtures"
)
@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("read", 0.4),
        ("write", 0.6),
        ("unrelated", None),
        ("inactive", None),
        ("ambiguous", None),
        ("denied", None),
    ],
)
def test_native_wmi_script_with_explicit_provider_fixtures(
    monkeypatch, scenario, expected
):
    fixture = _CIM_FIXTURE
    if scenario == "unrelated":
        fixture += "$global:panel.InstanceName='DISPLAY\\OTHER\\abc_0';"
    elif scenario == "inactive":
        fixture += "$global:panel.Active=$false;"
    elif scenario == "ambiguous":
        fixture += "function Get-CimInstance { $global:panel;$global:panel };"
    elif scenario == "denied":
        fixture += "function Invoke-CimMethod {throw 'Access denied'};"
    monkeypatch.setattr(mod, "_WMI_SCRIPT", fixture + mod._WMI_SCRIPT)
    api = mod.WindowsMonitorAPI.__new__(mod.WindowsMonitorAPI)
    api.primary = lambda: PANEL
    assert api.wmi(PANEL, value=0.51 if scenario in ("write", "denied") else None) == (
        mod.MonitorBrightness(expected, (0.2, 0.4, 0.6, 1.0))
        if expected is not None
        else None
    )
