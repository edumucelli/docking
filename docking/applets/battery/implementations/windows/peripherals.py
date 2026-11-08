"""Read battery properties from installed Windows device containers."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import uuid
from pathlib import Path

from docking.applets.battery.peripherals import PeripheralBattery

_SCRIPT = r"""
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
Add-Type -AssemblyName System.Runtime.WindowsRuntime;
$null=[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime];
$null=[Windows.Devices.Enumeration.DeviceInformationKind,Windows.Devices.Enumeration,ContentType=WindowsRuntime];
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
 $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
})[0];
$properties=[string[]]@('System.Devices.BatteryLife','System.Devices.Connected',
 'System.Devices.ChargingState','System.Devices.Category','System.ItemNameDisplay',
 'System.Devices.LocalMachine');
$operation=[Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync('',
 $properties,[Windows.Devices.Enumeration.DeviceInformationKind]::DeviceContainer);
$task=$asTask.MakeGenericMethod([Windows.Devices.Enumeration.DeviceInformationCollection]).Invoke($null,@($operation));
if(-not $task.Wait(5000)){throw 'Device battery query timed out'};
$rows=@();
foreach($item in $task.Result){
 $row=@{id=[string]$item.Id;name=[string]$item.Name;connected=$false;
  percent=$null;charging=$null;categories=@();local_machine=$null};
 # PowerShell projects this WinRT map as KeyValuePair enumeration.
 foreach($property in $item.Properties){
  switch -Exact ($property.Key){
   'System.Devices.Connected' {$row.connected=$property.Value}
   'System.Devices.BatteryLife' {$row.percent=$property.Value}
   'System.Devices.ChargingState' {$row.charging=$property.Value}
   'System.Devices.Category' {$row.categories=@($property.Value)}
   'System.Devices.LocalMachine' {$row.local_machine=$property.Value}
   'System.ItemNameDisplay' {if($null -ne $property.Value){$row.name=$property.Value}}
  }
 }
 if($row.connected -ceq $true -and $null -ne $row.percent){$rows+=@($row)};
 if($rows.Count -gt 1024){throw 'Device battery query exceeded its limit'};
};
@{devices=@($rows)} | ConvertTo-Json -Depth 5 -Compress
"""


def _parse_response(value: object) -> list[PeripheralBattery]:
    if not isinstance(value, dict):
        raise ValueError("Invalid Windows device battery response")
    rows = value.get("devices")
    if not isinstance(rows, list) or len(rows) > 1024:
        raise ValueError("Invalid Windows device battery collection")
    result: list[PeripheralBattery] = []
    seen: set[uuid.UUID] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid Windows device battery entry")
        if row.get("connected") is not True or row.get("local_machine") is not False:
            continue
        percent, name, identifier = row.get("percent"), row.get("name"), row.get("id")
        # BatteryLife is a Byte; values above 100 denote unknown status.
        if type(percent) is not int or not 0 <= percent <= 100:
            continue
        if not isinstance(name, str) or not name.strip() or "\0" in name:
            continue
        if not isinstance(identifier, str):
            continue
        try:
            identity = uuid.UUID(identifier)
        except ValueError:
            continue
        if not identity.int or identity in seen:
            continue
        seen.add(identity)
        categories = row.get("categories")
        kind = (
            next((text for text in categories if isinstance(text, str) and text), "")
            if isinstance(categories, list)
            else ""
        )
        charging = row.get("charging")
        result.append(
            PeripheralBattery(
                name.strip(),
                kind,
                float(percent),
                type(charging) is int and charging == 1,
                "",
            )
        )
    return sorted(
        result,
        key=lambda item: (
            item.percent if item.percent is not None else 101,
            item.model.casefold(),
        ),
    )


def read_peripheral_batteries() -> list[PeripheralBattery]:
    executable = (
        Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    response = subprocess.run(
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(_SCRIPT.encode("utf-16-le")).decode(),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=8,
        # CREATE_NO_WINDOW: this provider is called only by the Windows backend.
        creationflags=0x08000000,
    )
    if response.returncode:
        raise OSError("Windows device battery query failed")
    if len(response.stdout) > 2 * 1024 * 1024:
        raise ValueError("Windows device battery response exceeded its limit")
    return _parse_response(json.loads(response.stdout))
