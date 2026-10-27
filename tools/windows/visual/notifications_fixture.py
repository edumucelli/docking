"""Send and remove only owned native toast notifications in the Windows lab."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from contextlib import suppress
from xml.sax.saxutils import escape

_SCRIPT = r"""
$ErrorActionPreference='Stop';
$null=[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];
$null=[Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime];
$null=[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime];
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('REQUEST'))|ConvertFrom-Json;
if($request.action -eq 'send') {
 $xml=[Windows.Data.Xml.Dom.XmlDocument]::new();$xml.LoadXml([string]$request.xml);
 $toast=[Windows.UI.Notifications.ToastNotification]::new($xml);
 $toast.Tag=[string]$request.tag;$toast.Group='Docking-fixture';
 $toast.ExpirationTime=[DateTimeOffset]::Now.AddMinutes(10);
 [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier([string]$request.id).Show($toast);
} else {
 [Windows.UI.Notifications.ToastNotificationManager]::History.RemoveGroup('Docking-fixture',[string]$request.id);
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("send", "remove"))
    parser.add_argument("--id", required=True)
    parser.add_argument("--tag", default="first")
    parser.add_argument("--title", default="Docking generated notification")
    parser.add_argument("--body", default="Native Windows notification fixture")
    args = parser.parse_args()
    if sys.platform != "win32" or not args.id.startswith(
        "Docking.Notification.Fixture."
    ):
        parser.error(
            "Requires Windows and an owned Docking.Notification.Fixture.* identity"
        )
    if not all(character.isalnum() or character in ".-" for character in args.id):
        parser.error("Invalid fixture identity")
    import winreg

    path = "Software\\Classes\\AppUserModelId\\" + args.id
    if args.action == "send":
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(
                key, "DisplayName", 0, winreg.REG_SZ, "Docking generated notifications"
            )
    request = {
        "id": args.id,
        "action": args.action,
        "tag": args.tag,
        "xml": '<toast><visual><binding template="ToastGeneric"><text>'
        + escape(args.title)
        + "</text><text>"
        + escape(args.body)
        + "</text></binding></visual></toast>",
    }
    encoded = base64.b64encode(json.dumps(request).encode()).decode()
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(
                _SCRIPT.replace("REQUEST", encoded).encode("utf-16-le")
            ).decode(),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if args.action == "remove":
        with suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        settings = (
            "Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings\\"
            + args.id
        )
        with suppress(FileNotFoundError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, settings)
    print(
        json.dumps(
            {"action": args.action, "id": args.id, "exit_code": completed.returncode}
        )
    )


if __name__ == "__main__":
    main()
