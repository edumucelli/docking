# KDE Connect applet

The KDE Connect applet shows a paired device's connection status and battery,
lets you ring a phone, and sends local files to the selected device. KDE Connect
runs on the host; Docking uses its session D-Bus API. A Plasma desktop is not
required. The same applet works with a host KDE Connect daemon on GNOME and other
Linux desktops. Systems without KDE Connect keep a useful installation/setup
message and disable device actions.

## Set up a phone

1. Install your distribution's `kdeconnect` package on the computer and the
   [KDE Connect app](https://kdeconnect.kde.org/download.html) on your phone.
2. Open KDE Connect on both devices, connect them to the same network, and pair
   them. Accept the pairing request on the other device. Follow KDE Connect's
   [network troubleshooting](https://userbase.kde.org/KDEConnect#Troubleshooting)
   if the devices cannot discover each other.
3. Add **KDE Connect** from Docking's applet catalog. Use the applet's menu to
   select your phone. Clicking the icon opens KDE Connect's device settings.
4. Enable the phone's battery, find-my-phone, and file-sharing plugins as needed.
   Docking enables each action only when the selected device is online and its
   corresponding KDE Connect plugin is loaded.

The applet lists paired devices, including offline ones. Selecting a phone saves
its identifier. If that phone disconnects or is unpaired, Docking does not switch
to another recipient. Choose a different device explicitly in the menu.

Battery percentages include the full 0–100% range. Charging appears in the icon
and tooltip. Devices that do not report a battery retain their connection status
without an invented percentage. The applet listens for device, plugin, battery,
and daemon changes and refreshes every 30 seconds as a fallback.

## Send files and ring a phone

Drop one or more local files onto the icon, or choose **Send Files...** in the
menu. Directories, remote URLs, and missing files are rejected. The target stays
the device selected when the action started, including while the file chooser
is open. Docking checks pairing, reachability, and the sharing plugin again
before asking KDE Connect to transfer the files.

**File transfer requested** means the host daemon accepted the request. KDE
Connect handles transport, progress, and remote completion. Use KDE Connect's
transfer UI or check the receiving device to confirm receipt. Similarly, **Ring
request sent** confirms the request, not that a speaker played a sound. Device
permissions, plugin settings, and phone background restrictions still apply.

## Flatpak

The Flatpak manifest grants access only to the `org.kde.kdeconnect` session-bus
service for this integration. Install and run KDE Connect on the host. The
applet does not bundle another daemon. Files handed to the host must have paths
that the host daemon can read; a sandbox-only or document-portal path may not be
usable by a host process. The applet's settings action uses the existing
`flatpak-spawn --host` support to launch host KDE Connect settings.

## Validation without a KDE desktop

The normal test suite includes unit tests and a private D-Bus integration test:

```sh
PYTHONPATH="$PWD" .venv/bin/pytest tests/applets/test_kdeconnect.py tests/integration/test_kdeconnect_bus.py
```

The integration test starts its own `dbus-daemon` and a deterministic paired-phone
fixture, separate from the user's session bus. It checks device enumeration,
battery updates including 0% charging, real D-Bus ring/share calls, reachability
changes, daemon disappearance, and subscription cleanup. It does not contact a
physical phone.

A disposable Debian container can additionally run the real KDE Connect daemon
with Qt's offscreen platform. The runtime smoke check in `tools/kdeconnect/`
starts a real daemon with a fresh profile and private session bus, reads its
paired-device query through Docking's backend, and checks daemon disappearance
and teardown. See that directory's README for commands and the remaining
physical-phone and end-to-end file-delivery validation limits.

The D-Bus contract follows KDE Connect's upstream
[daemon](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/core/daemon.h),
[device](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/core/device.h),
[battery](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/battery/batteryplugin.h),
[find-my-phone](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/findmyphone/findmyphoneplugin.h),
and [share](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/share/shareplugin.h)
interfaces. The client supports the older `isTrusted` device property as well
as `isPaired`.
