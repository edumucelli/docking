"""The documented machine camera policy on supported Windows editions.

AllowCamera blocks new camera sessions. Existing clients can retain access;
activity monitoring remains independent of this policy.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

KEY = r"SOFTWARE\Policies\Microsoft\Camera"
VALUE = "AllowCamera"
_EDITIONS = (
    "Professional",
    "Enterprise",
    "Education",
    "IoTEnterprise",
)


@dataclass(frozen=True)
class CameraPolicyState:
    value: int | None = None
    supported: bool = False

    @property
    def blocked(self) -> bool:
        return self.value == 0


class WindowsCameraPolicy:
    def snapshot(self) -> CameraPolicyState:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            edition, _kind = winreg.QueryValueEx(key, "EditionID")
        supported = isinstance(edition, str) and edition.startswith(_EDITIONS)
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                KEY,
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, kind = winreg.QueryValueEx(key, VALUE)
        except FileNotFoundError:
            return CameraPolicyState(None, supported)
        if kind != winreg.REG_DWORD or type(value) is not int or value not in (0, 1):
            raise OSError("Unrecognized Windows camera policy")
        return CameraPolicyState(value, supported)

    def set_value(self, value: int | None) -> bool:
        import winreg

        if value is not None and (type(value) is not int or value not in (0, 1)):
            raise ValueError("Invalid Windows camera policy")
        if value is None:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    KEY,
                    0,
                    winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY,
                ) as key:
                    with suppress(FileNotFoundError):
                        winreg.DeleteValue(key, VALUE)
                    winreg.FlushKey(key)
            except FileNotFoundError:
                pass
        else:
            with winreg.CreateKeyEx(
                winreg.HKEY_LOCAL_MACHINE,
                KEY,
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY,
            ) as key:
                winreg.SetValueEx(key, VALUE, 0, winreg.REG_DWORD, value)
                winreg.FlushKey(key)
        return self.snapshot().value == value
