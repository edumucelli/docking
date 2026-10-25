"""Independent .NET Core Audio readback for owned microphone acceptance."""

from __future__ import annotations

import base64
import json

from tools.windows_powerprofiles_smoke import powershell

_ORACLE = r"""
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class MicOracle {
 [ComImport,Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
 class EnumeratorClass {}
 [ComImport,Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),
  InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface Enumerator {
  [PreserveSig]int EnumAudioEndpoints(int flow,uint state,out Collection devices);
  [PreserveSig]int GetDefaultAudioEndpoint(int flow,int role,out Device device);
  [PreserveSig]int GetDevice([MarshalAs(UnmanagedType.LPWStr)]string id,
   out Device device);
 }
 [ComImport,Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"),
  InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface Collection {
  [PreserveSig]int GetCount(out uint count);
  [PreserveSig]int Item(uint index,out Device device);
 }
 [ComImport,Guid("D666063F-1587-4E43-81F1-B948E807363F"),
  InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface Device {
  [PreserveSig]int Activate(ref Guid iid,uint context,IntPtr parameters,
    [MarshalAs(UnmanagedType.IUnknown)]out object value);
  [PreserveSig]int OpenPropertyStore(uint access,out IntPtr store);
  [PreserveSig]int GetId([MarshalAs(UnmanagedType.LPWStr)]out string id);
  [PreserveSig]int GetState(out uint state);
 }
 [ComImport,Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),
  InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface Volume {
  [PreserveSig]int RegisterControlChangeNotify(IntPtr value);
  [PreserveSig]int UnregisterControlChangeNotify(IntPtr value);
  [PreserveSig]int GetChannelCount(out uint count);
  [PreserveSig]int SetMasterVolumeLevel(float value,IntPtr context);
  [PreserveSig]int SetMasterVolumeLevelScalar(float value,IntPtr context);
  [PreserveSig]int GetMasterVolumeLevel(out float value);
  [PreserveSig]int GetMasterVolumeLevelScalar(out float value);
  [PreserveSig]int SetChannelVolumeLevel(uint channel,float value,IntPtr context);
  [PreserveSig]int SetChannelVolumeLevelScalar(uint channel,float value,IntPtr context);
  [PreserveSig]int GetChannelVolumeLevel(uint channel,out float value);
  [PreserveSig]int GetChannelVolumeLevelScalar(uint channel,out float value);
  [PreserveSig]int SetMute([MarshalAs(UnmanagedType.Bool)]bool muted,IntPtr context);
  [PreserveSig]int GetMute([MarshalAs(UnmanagedType.Bool)]out bool muted);
 }
 static void Check(int value) {if(value<0)Marshal.ThrowExceptionForHR(value);}
 static void Release(object value) {if(value!=null)Marshal.ReleaseComObject(value);}
 static Volume Open(Device device) {
  Guid iid=new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");object value=null;
  try {Check(device.Activate(ref iid,1,IntPtr.Zero,out value));return (Volume)value;}
  catch {Release(value);throw;}
 }
 public static object[] Read() {
  Enumerator enumerator=null;Collection collection=null;Device defaultDevice=null;
  try {
   enumerator=(Enumerator)new EnumeratorClass();string defaultId="";
   if(enumerator.GetDefaultAudioEndpoint(1,1,out defaultDevice)>=0
      && defaultDevice!=null)
    Check(defaultDevice.GetId(out defaultId));
   Check(enumerator.EnumAudioEndpoints(1,1,out collection));uint count;
   Check(collection.GetCount(out count));
   if(count>256)throw new Exception("Invalid count");
   var rows=new List<object>();
   for(uint i=0;i<count;i++) {
    Device device=null;Volume volume=null;
    try {
     Check(collection.Item(i,out device));string id;Check(device.GetId(out id));
     volume=Open(device);bool muted;Check(volume.GetMute(out muted));
     rows.Add(new {id=id,muted=muted,is_default=(id==defaultId)});
    } finally {Release(volume);Release(device);}
   }
   return rows.ToArray();
  } finally {Release(defaultDevice);Release(collection);Release(enumerator);}
 }
 public static void Set(string id,bool muted) {
  Enumerator enumerator=null;Collection collection=null;
  try {
   enumerator=(Enumerator)new EnumeratorClass();
   Check(enumerator.EnumAudioEndpoints(1,1,out collection));uint count;
   Check(collection.GetCount(out count));
   for(uint i=0;i<count;i++) {
    Device device=null;Volume volume=null;
    try {
     Check(collection.Item(i,out device));string current;
     Check(device.GetId(out current));
     if(current!=id)continue;
     volume=Open(device);Check(volume.SetMute(muted,IntPtr.Zero));return;
    } finally {Release(volume);Release(device);}
   }
   throw new Exception("Requested capture endpoint is not active");
  } finally {Release(collection);Release(enumerator);}
 }
}
"""


def oracle(changes: dict[str, bool] | None = None):
    request = base64.b64encode(json.dumps(changes or {}).encode()).decode()
    return powershell(
        "Add-Type -TypeDefinition @'\n"
        + _ORACLE
        + "\n'@;\n"
        + "$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + request
        + "')) | ConvertFrom-Json;\n"
        + "foreach($p in $request.PSObject.Properties){"
        "[MicOracle]::Set($p.Name,[bool]$p.Value)};\n"
        + "ConvertTo-Json -InputObject ([MicOracle]::Read()) -Depth 4 -Compress"
    )
