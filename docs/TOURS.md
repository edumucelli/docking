# Recording Docking tours

One command from the checkout generates three MP4s in a [private Linux/X11
desktop](adr/0001-record-tours-in-a-private-x11-desktop.md). The silent videos
show the full desktop and real pointer input. A context bar inside the frame
gives each scene a title and one short line. Keep it above the shelf and position
it per scene as needed to leave the menus, tooltips, stacks, and windows visible.

## Run the recorder

Use the [development environment](DEVELOPMENT.md#source-setup) on Linux with
Xvfb, xauth, D-Bus, the Marco window manager, GTK 3, Wnck, the
AT-SPI 2 GI bindings, and FFmpeg with X11 capture, libx264, and libass.

```sh
.venv/bin/python -m tools.linux.tours --profile /path/to/demo-profile
```

The recorder writes `overview.mp4`, `applets.mp4`, and `preferences.mp4`
to `artifacts/tours/`. Use `--tour preferences` to record only Preferences or
`--tour overview` to record the overview. The overview uses Paris for Weather
when no city is configured. `--tour` can be repeated. Use `--output` to choose
a different directory.

### Prepare the demo profile

Provision a dedicated recording machine with the runtime services and devices
used by the [applet catalog](APPLETS.md). Prepare Docking in a normal desktop
session on that machine and configure the applets' live sources. Pair the demo
Bluetooth and KDE Connect devices, connect the camera and microphone, and
provide the audio output, desktop screenshot tool, and file manager with
`trash:///` support. Use a recording network and devices whose names can
appear in the video.

Close Docking and copy the demo session's XDG config, data, and state into
`config/`, `data/`, and `state/` in the profile directory. Keep
`config/docking/dock.json` and the app-specific data under `data/docking/`.
Use `home/` for any demo files needed under the recording home directory.
The recorder uses a temporary copy of this profile. Keep it outside version
control. WhatsApp and Telegram open fresh sessions to show their QR sign-in
screens.

An optional executable `startup` in the profile runs inside the private
display and session bus. Use it to start any session services needed for the
tour, such as a media player playing a sample track or a notification daemon.
Set `PULSE_SERVER` when the recording session needs to use an existing
PulseAudio-compatible server, for example `unix:/run/user/1000/pulse/native`
for a recording user with ID 1000.
Review recordings and failure screenshots before publishing.

## Overview tour

Show the pointer moving across the shelf and opening an application context
menu and a folder stack. Open the window preview for an application with two
demo windows, hover a thumbnail, and click it to bring its window forward.

| Applet | Scene |
| --- | --- |
| Screenshot | Open the capture menu, select Region, and show the screenshot tool's area selection. |
| WhatsApp | Open the app and show the QR code for signing in. |
| Weather | Hover to show current conditions and the forecast for a configured city. |
| Clock | Left-click to open the calendar. |
| Network | Show the live connection and traffic tooltip, then open the context menu. |
| Bluetooth | Open the menu and show a paired demo device and its submenu. |
| Hacker News | Show a live headline and scroll to the next one. |
| Trash | Open the trash containing a sample file. |
| Volume | Show the live volume level and its optional icon display. |
| Applications | Open the categorized launcher and filter applications using search. |

Close with the shelf in view and this context bar cue.

**More applets in action**

Watch the Docking Applets Tour for a chapter-by-chapter walkthrough.

## Applet tour

Follow the catalog categories as chapters. Show each built-in applet's menu and
available submenus, with selected dialogs and [live, populated applet
data](adr/0002-record-applets-with-live-data.md). Open the QR sign-in screens
for WhatsApp and Telegram.

## Preferences tour

Visit Appearance, Behavior, Applets, Updates, and Import. Scroll through
Appearance and Behavior. End with Diagnostics Overview and Features and open
the Save Report dialog.

## Regeneration

The [scene scripts](../tools/linux/tours/scenes.py) target dock items by ID and
controls by visible label where practical. A failed required scene reports the
interaction or missing live source, saves a screenshot when the display is
available, and preserves the previous successful videos. Failure evidence
stays in the `recording-*` directory under the output directory.
