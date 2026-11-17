"""Independent, read-only Win32 input snapshot in an isolated Python process.

Schema 1 retains active/default low-32-bit HKL codes, wheel routing and every
HKCU Preload/Substitutes value. Loaded rows contain only ``code``: names are
not needed to compare the independent process's loaded set. No production
package, GUI toolkit, shell, compiler, COM or input setter is imported/called.
Preference-only snapshots explicitly omit active foreground state; they remain
valid before the first owned GUI queue exists and after the final one closes.
"""

from __future__ import annotations

import ctypes as c
import json
import re
import subprocess
import sys
from pathlib import Path

_P, _U, _B = c.c_void_p, c.c_uint32, c.c_int32
_CODE = re.compile(r"[0-9a-f]{8}")


class Rect(c.Structure):
    _fields_ = [(name, c.c_int32) for name in ("left", "top", "right", "bottom")]


class GUIThreadInfo(c.Structure):
    _fields_ = [
        ("size", _U),
        ("flags", _U),
        ("active", _P),
        ("focus", _P),
        ("capture", _P),
        ("menu", _P),
        ("move", _P),
        ("caret", _P),
        ("rect", Rect),
    ]


class OracleError(RuntimeError):
    def __init__(self, api: str, code: int):
        super().__init__(f"{api} failed ({code})")
        self.api, self.code = api, code


def _bind(library, name, result, *arguments):
    # Standalone external ctypes boundary; never import production FFI helpers.
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


def _check(value, api):
    if not value:
        raise OracleError(api, c.get_last_error())
    return value


def _code(handle):
    if not handle or handle & 0xFFFFFFFF <= 1:
        raise OracleError("GetKeyboardLayout", 13)  # ERROR_INVALID_DATA.
    return f"{handle & 0xFFFFFFFF:08x}"


def registry_values(registry, path):
    try:
        key = registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0, registry.KEY_READ)
    except FileNotFoundError:
        return {}
    with key as opened:
        values = {}
        for index in range(registry.QueryInfoKey(opened)[1]):
            name, value, kind = registry.EnumValue(opened, index)
            # Non-string values are unusual here, but must not disappear from
            # the preference comparison. Encode REG_BINARY/REG_NONE losslessly.
            values[name] = {
                "type": kind,
                "data": {"binary_hex": value.hex()}
                if isinstance(value, bytes)
                else value,
            }
        return values


def snapshot(user, registry, *, preferences_only=False):
    layouts = _bind(user, "GetKeyboardLayoutList", c.c_int32, c.c_int32, c.POINTER(_P))
    loaded = None
    for _ in range(3):
        count = _check(layouts(0, None), "GetKeyboardLayoutList")
        if not 1 <= count <= 4096:
            raise OracleError("GetKeyboardLayoutList", 13)
        handles = (_P * (count + 1))()
        copied = _check(layouts(len(handles), handles), "GetKeyboardLayoutList")
        if 0 < copied < len(handles):
            loaded = [{"code": _code(handle)} for handle in handles[:copied]]
            if len({row["code"] for row in loaded}) != len(loaded):
                raise OracleError("GetKeyboardLayoutList", 13)
            break
    if loaded is None:
        raise OracleError("GetKeyboardLayoutList", 234)  # ERROR_MORE_DATA.
    active = {}
    if not preferences_only:
        foreground = _bind(user, "GetForegroundWindow", _P)()
        if not foreground:
            raise OracleError("GetForegroundWindow", 1400)
        thread_of = _bind(user, "GetWindowThreadProcessId", _U, _P, c.POINTER(_U))
        pid = _U()
        thread = _check(thread_of(foreground, c.byref(pid)), "GetWindowThreadProcessId")
        info = GUIThreadInfo(size=c.sizeof(GUIThreadInfo))
        _check(
            _bind(user, "GetGUIThreadInfo", _B, _U, c.POINTER(GUIThreadInfo))(
                thread, c.byref(info)
            ),
            "GetGUIThreadInfo",
        )
        if info.focus:
            thread = _check(
                thread_of(info.focus, c.byref(pid)), "GetWindowThreadProcessId"
            )
        active["active"] = _code(_bind(user, "GetKeyboardLayout", _P, _U)(thread))
    parameters = _bind(user, "SystemParametersInfoW", _B, _U, _U, _P, _U)
    default, wheel = _P(), _U()
    _check(parameters(0x59, 0, c.byref(default), 0), "SPI_GETDEFAULTINPUTLANG")
    _check(parameters(0x201C, 0, c.byref(wheel), 0), "SPI_GETMOUSEWHEELROUTING")
    return {
        "schema": 1,
        "pointer_bits": c.sizeof(_P) * 8,
        "loaded": loaded,
        **active,
        "default": _code(default.value),
        "wheel_route": wheel.value,
        "preload": registry_values(registry, r"Keyboard Layout\Preload"),
        "substitutes": registry_values(registry, r"Keyboard Layout\Substitutes"),
    }


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate oracle JSON key")
        result[name] = value
    return result


def _constant(_value):
    raise ValueError("Non-finite oracle JSON value")


def validate(value, *, preferences_only=False):
    expected = {
        "schema",
        "pointer_bits",
        "loaded",
        "default",
        "wheel_route",
        "preload",
        "substitutes",
    }
    if not preferences_only:
        expected.add("active")
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid input oracle schema")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("Unsupported input oracle schema")
    if (
        type(value["pointer_bits"]) is not int
        or value["pointer_bits"] != c.sizeof(_P) * 8
    ):
        raise ValueError("Input oracle subprocess ABI differs")

    def code(text):
        return (
            isinstance(text, str)
            and _CODE.fullmatch(text) is not None
            and int(text, 16) > 1
        )

    loaded = value["loaded"]
    if (
        not isinstance(loaded, list)
        or not loaded
        or any(
            not isinstance(row, dict) or set(row) != {"code"} or not code(row["code"])
            for row in loaded
        )
        or len({row["code"] for row in loaded}) != len(loaded)
        or (not preferences_only and not code(value["active"]))
        or not code(value["default"])
        or type(value["wheel_route"]) is not int
        or not 0 <= value["wheel_route"] <= 0xFFFFFFFF
    ):
        raise ValueError("Invalid input oracle native state")
    for name in ("preload", "substitutes"):

        def registry_value(item):
            if (
                not isinstance(item, dict)
                or set(item) != {"type", "data"}
                or type(item["type"]) is not int
                or not 0 <= item["type"] <= 0xFFFFFFFF
            ):
                return False
            data = item["data"]
            if data is None or type(data) in (str, int):
                return True
            if isinstance(data, list):
                return all(isinstance(part, str) for part in data)
            return (
                isinstance(data, dict)
                and set(data) == {"binary_hex"}
                and isinstance(data["binary_hex"], str)
                and re.fullmatch(r"(?:[0-9a-f]{2})*", data["binary_hex"]) is not None
            )

        if not isinstance(value[name], dict) or any(
            not registry_value(item) for item in value[name].values()
        ):
            raise ValueError("Invalid input oracle registry snapshot")
    return value


def oracle(*, preferences_only=False):
    command = [sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--snapshot"]
    if preferences_only:
        command.append("--preferences-only")
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    value = json.loads(
        result.stdout, object_pairs_hook=_object, parse_constant=_constant
    )
    if isinstance(value, dict) and "error" in value:
        error = value["error"]
        if (
            set(value) != {"schema", "error"}
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or not isinstance(error, dict)
            or set(error) != {"api", "code"}
            or not isinstance(error["api"], str)
            or type(error["code"]) is not int
        ):
            raise ValueError("Invalid input oracle failure")
        raise OracleError(error["api"], error["code"])
    if result.returncode != 0:
        raise RuntimeError(f"Input oracle subprocess exited ({result.returncode})")
    return validate(value, preferences_only=preferences_only)


def main():
    try:
        arguments = sys.argv[1:]
        if sys.platform != "win32" or arguments not in (
            ["--snapshot"],
            ["--snapshot", "--preferences-only"],
        ):
            raise OracleError("Windows input oracle", 50)
        import winreg

        value = snapshot(
            c.WinDLL("user32", use_last_error=True),
            winreg,
            preferences_only="--preferences-only" in arguments,
        )
    except OracleError as error:
        value = {"schema": 1, "error": {"api": error.api, "code": error.code}}
    except OSError as error:
        # Registry/loader errors contain paths in their messages. Keep numeric
        # Windows errors exactly; errno is the independent Linux test boundary.
        code = error.winerror if sys.platform == "win32" else error.errno
        value = {
            "schema": 1,
            "error": {"api": "registry-or-loader", "code": code},
        }
    print(json.dumps(value, ensure_ascii=True, allow_nan=False))
    return 1 if "error" in value else 0


if __name__ == "__main__":
    raise SystemExit(main())
