"""Own a real Windows MediaPlayer, two generated tracks and its SMTC publisher.

Write OUTPUT/stop to close normally. State reports come from MediaPlayer's actual
playback session; transport events change that player, not the observer app.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

_SCRIPT = r"""
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
Add-Type -AssemblyName System.Runtime.WindowsRuntime;
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.IO;
using System.Threading;
using System.Text;
public sealed class MediaFixtureControls {
 [DllImport("kernel32", CharSet=CharSet.Unicode, SetLastError=true)]
 static extern bool MoveFileEx(string oldName,string newName,uint flags);
 public static void Publish(string path,string text) {
  string pending=path+".tmp";
  File.WriteAllText(pending,text,new UTF8Encoding(false));
  for(int i=0;i<50;i++) {
   if(MoveFileEx(pending,path,9))return;
   Thread.Sleep(10);
  }
  throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
 }
 readonly object gate=new object(); readonly object player;
 readonly object[] sources; readonly Queue<string> commands=new Queue<string>();
 int index; bool stopped; public string Error { get; private set; }
 public MediaFixtureControls(object player, object[] sources) {
  this.player=player; this.sources=sources;
 }
 public int Index { get {lock(gate){return index;}} }
 public string[] Commands { get {lock(gate){return commands.ToArray();}} }
 public void Stop() {lock(gate){stopped=true;}}
 public void OnButton(object sender, object args) {
  lock(gate) {
   if(stopped)return;
   try {
    string button=args.GetType().GetProperty("Button").GetValue(args,null).ToString();
    commands.Enqueue(button); Type type=player.GetType();
    if(button=="Next" || button=="Previous") {
     index=(index+1)%sources.Length;
     type.GetProperty("Source").SetValue(player,sources[index],null);
     type.GetMethod("Play").Invoke(player,null);
    } else if(button=="Play" || button=="Pause") {
     type.GetMethod(button).Invoke(player,null);
    }
   } catch(Exception error) {Error=error.ToString();}
  }
 }
}
'@;
$null=[Windows.Media.Playback.MediaPlayer,Windows.Media.Playback,ContentType=WindowsRuntime];
$null=[Windows.Media.Core.MediaSource,Windows.Media.Core,ContentType=WindowsRuntime];
$null=[Windows.Media.Playback.MediaPlayerAudioCategory,Windows.Media.Playback,ContentType=WindowsRuntime];
$null=[Windows.Media.MediaPlaybackStatus,Windows.Media,ContentType=WindowsRuntime];
$null=[Windows.Media.MediaPlaybackType,Windows.Media,ContentType=WindowsRuntime];
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime];
$null=[Windows.Storage.Streams.RandomAccessStreamReference,Windows.Storage.Streams,ContentType=WindowsRuntime];
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
 $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
})[0];
function Await($operation,[Type]$type) {
 $task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation));
 if(-not $task.Wait(5000)){throw 'Media fixture operation timed out'};
 return $task.Result;
}
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('REQUEST'))|ConvertFrom-Json;
$sources=@();$thumbs=@();
foreach($track in $request.tracks) {
 $sources+=,[Windows.Media.Core.MediaSource]::CreateFromUri([uri]$track.uri);
 $op=[Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$track.art);
 $file=Await $op ([Windows.Storage.StorageFile]);
 $thumbs+=,[Windows.Storage.Streams.RandomAccessStreamReference]::CreateFromFile($file);
}
$player=[Windows.Media.Playback.MediaPlayer]::new();$job=$null;$everPlayed=$false;
try {
 $player.Volume=0.15;$player.IsLoopingEnabled=$true;
 $player.AudioCategory=[Windows.Media.Playback.MediaPlayerAudioCategory]::Media;
 $player.CommandManager.IsEnabled=$false;
 $controls=$player.SystemMediaTransportControls;$controls.IsEnabled=$true;
 $controls.IsPlayEnabled=$true;$controls.IsPauseEnabled=$true;
 $controls.IsNextEnabled=$true;$controls.IsPreviousEnabled=$true;
 $context=[MediaFixtureControls]::new($player,[object[]]$sources);
 $add=$controls.GetType().GetMethod('add_ButtonPressed');
 $remove=$controls.GetType().GetMethod('remove_ButtonPressed');
 $handlerType=$add.GetParameters()[0].ParameterType;
 $handler=[Delegate]::CreateDelegate($handlerType,$context,
  $context.GetType().GetMethod('OnButton'));
 $token=$add.Invoke($controls,@($handler));
 $last=-1;$publishedArt=-1;$artAt=[DateTime]::MaxValue;
 $player.Source=$sources[0];$player.Play();
 $deadline=[DateTime]::UtcNow.AddSeconds($request.timeout);
 $stop=Join-Path $request.output 'stop';
 while([DateTime]::UtcNow -lt $deadline -and -not (Test-Path $stop)) {
  Start-Sleep -Milliseconds 100;
  $state=[int]$player.PlaybackSession.PlaybackState;
  if($last -ne $context.index -and $state -eq 3) {
   $display=$controls.DisplayUpdater;$display.Type=[Windows.Media.MediaPlaybackType]::Music;
   $display.MusicProperties.Title=[string]$request.tracks[$context.index].title;
   $display.MusicProperties.Artist='Docking native fixture';
   $display.MusicProperties.AlbumTitle='Generated two-track album';
   $display.Thumbnail=$null;$display.Update();$last=$context.index;
   $artAt=[DateTime]::UtcNow.AddMilliseconds(500);
  }
  if($publishedArt -ne $last -and $last -ge 0 -and
     [DateTime]::UtcNow -ge $artAt) {
   $display.Thumbnail=$thumbs[$context.index];$display.Update();
   $publishedArt=$last;
  }
  $state=[int]$player.PlaybackSession.PlaybackState;
  if($state -eq 3){
   $controls.PlaybackStatus=[Windows.Media.MediaPlaybackStatus]::Playing;
   $everPlayed=$true
  }
  elseif($state -eq 4){
   $controls.PlaybackStatus=[Windows.Media.MediaPlaybackStatus]::Paused
  }
  else{$controls.PlaybackStatus=[Windows.Media.MediaPlaybackStatus]::Changing};
  $report=@{pid=$PID;index=$context.index;
    state=[string]$player.PlaybackSession.PlaybackState;
    artwork_published=($publishedArt -eq $context.index);
    position=$player.PlaybackSession.Position.TotalSeconds;commands=$context.Commands;error=$context.Error;
    title=[string]$request.tracks[$context.index].title;volume=$player.Volume}|
    ConvertTo-Json -Compress -Depth 4;
  [MediaFixtureControls]::Publish((Join-Path $request.output 'state.json'),$report);
 }
 if($context.Error){throw $context.Error};
 if(-not $everPlayed){throw 'MediaPlayer never entered real playback'};
} finally {
 if($null -ne $context){$context.Stop()};
 if($null -ne $handler) {
  $argument=if($remove.GetParameters()[0].ParameterType.IsInstanceOfType($handler))
   {$handler}else{$token};
  $null=$remove.Invoke($controls,@($argument));
 }

 $controls.IsEnabled=$false;$player.Dispose();
 foreach($source in $sources){$source.Dispose()};
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    report = {"ok": False}
    try:
        with tempfile.TemporaryDirectory(prefix="Docking-music-fixture-") as folder:
            local = Path(folder)
            tracks = []
            for index, color in enumerate(((20, 60, 110), (180, 40, 80))):
                track, art = local / f"tone-{index}.wav", local / f"cover-{index}.png"
                with wave.open(str(track), "wb") as wav:
                    wav.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                    wav.writeframes(
                        b"".join(
                            struct.pack(
                                "<h",
                                int(
                                    800
                                    * math.sin(
                                        2 * math.pi * (220 + index * 110) * n / 22050
                                    )
                                ),
                            )
                            for n in range(22050 * 5)
                        )
                    )
                with Image.new("RGB", (96, 96), color) as image:
                    image.save(art)
                tracks.append(
                    {
                        "uri": track.as_uri(),
                        "art": str(art),
                        "title": f"Docking generated track {index + 1}",
                    }
                )
            request = {"output": str(output), "timeout": args.timeout, "tracks": tracks}
            data = base64.b64encode(json.dumps(request).encode()).decode()
            script = _SCRIPT.replace("REQUEST", data)
            process = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    base64.b64encode(script.encode("utf-16-le")).decode(),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=args.timeout + 20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            report.update(
                ok=process.returncode == 0,
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
            )
    finally:
        (output / "result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
