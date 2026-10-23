# Docking GNOME Shell Bridge Prototype

This extension is an experimental bridge for GNOME Shell on Wayland. It does
not draw Docking inside GNOME Shell and does not provide panel edge
reservation. It exposes read-only window/workspace state plus a few actions over
the session bus so the Python app can use GNOME-owned Mutter state.

Install for local testing:

```bash
tools/gnome_bridge.sh install
tools/gnome_bridge.sh enable
```

Check the bridge:

```bash
gdbus call --session \
  --dest org.docking.Docking.GnomeShellBridge \
  --object-path /org/docking/Docking/GnomeShellBridge \
  --method org.docking.Docking.GnomeShellBridge1.ListWindows
```

Run Docking against it:

```bash
DOCKING_BACKEND=gnome-shell python3 run.py
```

On GNOME Wayland, a newly installed user extension may not be visible to the
running Shell until the session is restarted. GNOME Shell can also keep a GJS
module cached for an extension UUID after source edits, even if the extension is
disabled and enabled again. After changing `extension.js`, log out and back in
before trusting the live D-Bus result. `tools/gnome_bridge.sh status` reports
both GNOME's extension state and whether the bridge D-Bus API is available.
