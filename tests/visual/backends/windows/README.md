# Windows visual checks

Run from the repository root on an interactive Windows desktop. The shared
entry point is `python -m tests.visual --target windows-server`. Use a fresh
output directory under `artifacts/` for each run.

- `lab`: submit durable jobs to the interactive Windows lab agent.
- `vm_input`, `vm_pointer`, `vm_capture`, `vm_audio`: independent input and recording.
- `desktop_driver`: shared native desktop observations and input.
- `smoke`: run the complete dock, including an isolated wheel or native bundled launcher.
- `bundle_smoke`: verify and extract a portable ZIP, then check the isolated runtime.
- `installer_smoke`: install, upgrade, check startup and the installed desktop,
  then uninstall while preserving preferences and unknown files.
- `*_smoke`: feature checks through the running dock.
- `*_fixture`, `*_capture`, `*_probe`, `*_oracle`: owned providers and independent observations.
- `camera_source.cpp`: generated-frame camera provider built by `camera_fixture`.

```sh
python -m tools.windows.lab.run --share /path/to/shared status
python -m tests.visual.backends.windows.smoke --output artifacts/windows/smoke
python -m tests.visual.backends.windows.runtime_pin_smoke --output artifacts/windows/runtime-pin
python -m tests.visual.backends.windows.runtime_pin_smoke --multiple-windows --uncataloged --output artifacts/windows/multiple-windows
python -m tests.visual.backends.windows.fullscreen_smoke --output artifacts/windows/fullscreen
python -m tests.visual.backends.windows.keyboardlayout_smoke --search-input --output artifacts/windows/search-layouts
python -m tests.visual.backends.windows.input_profile_smoke --installed-root artifacts/windows/runtime --output artifacts/windows/input-profiles
python -m tests.visual.backends.windows.runtime_pin_smoke --explorer-drop --output artifacts/windows/explorer-pin
python -m tests.visual.backends.windows.runtime_pin_smoke --explorer-drop --shortcut installed --output artifacts/windows/installed-shortcut
python -m tests.visual.backends.windows.runtime_pin_smoke --explorer-drop --shortcut unlisted --output artifacts/windows/unlisted-shortcut
python -m tests.visual.backends.windows.launch_provenance_smoke --output artifacts/windows/launch-provenance
python -m tests.visual.backends.windows.application_export_smoke --output artifacts/windows/application-export
python -m tests.visual.backends.windows.application_export_smoke --classic --output artifacts/windows/classic-export
python -m tests.visual.backends.windows.application_export_smoke --cancel-export --output artifacts/windows/export-cancel
python -m tests.visual.backends.windows.application_export_smoke --close-during-export --output artifacts/windows/export-close
```

See [Windows support](../../../../docs/WINDOWS.md) for capabilities and source
setup, and the [desktop lab](../../../../tools/windows/README.md#desktop-lab)
for the interactive VM.

## Validation and CI

The installed wheel and standalone desktop checks require the actual dock window
to use per-monitor-v2 DPI awareness and record its DPI. The fullscreen check
exercises F11 transitions, startup during fullscreen, and Always behind stacking
and input. The Search input checks exercise US/French/Dvorak layouts, dead keys,
shortcut conflicts and shutdown during retry. The Telex/VNI checks compare Search
with a plain GTK entry and retain text-service ownership and cleanup evidence.
See [Windows packaging](../../../../packaging/windows/README.md) for ZIP and
installer acceptance.

### Required CI coverage

| Gate | Required coverage |
| --- | --- |
| Shared Linux/BSD checks | Existing tests, typing/lint, visual/BDD, compositor and package/desktop checks remain required for shared changes. |
| [Windows Server](../../../../.github/workflows/windows.yml) | Native backend/core/GTK tests, installed wheel, uncataloged multiwindow grouping/cycling and restored pins, fullscreen recovery, input API/layouts/shortcuts, tray/application domains, Bluetooth fixtures, standalone ZIP, installer/upgrades/startup/uninstall and startup/session measurements. |
| [Windows 11 input](../../../../.github/workflows/windows-input.yml) | The exact Server-built AMD64 wheel, verified installed bytes, independent AMD64/x86 editors, twelve request controls, eight Telex/VNI compositions, both key routes and plain GTK/Search preedit/Return/Escape behavior. |
| [Candidate evidence](../../../../.github/workflows/ci.yml) | Validate Server and client reports against the same source commit, wheel and package identities before release staging. Missing, failed or dependency-skipped evidence cannot authorize publication. |

The explicit Server `server-api` input profile does not claim composition.
Windows 11 `workstation-composition` requires actual composed text. Successful
TSF/profile readback alone cannot satisfy that requirement.

### Installed-wheel checks

Build/install the candidate wheel as described in
[Windows packaging](../../../../packaging/windows/README.md). Use its actual filename
and full source commit in domain checks:

```sh
python -m tools.windows.ci --stage input --installed-root artifacts/windows/runtime --wheel dist/docking.whl --source-sha <full-source-commit>
python -m tools.windows.ci --stage tray --installed-root artifacts/windows/runtime --wheel dist/docking.whl
python -m tools.windows.ci --stage applications --installed-root artifacts/windows/runtime --wheel dist/docking.whl
```

Replace `<full-source-commit>` before running the command. Use
`--input-coverage server-api` only for explicit Server API coverage.
Use `--owned-amd64-editor` for the emulated AMD64 client fixture.
Domain batches collect independent cases after a failure when cleanup permits;
cancellation stops them immediately.

Installed acceptance verifies every shipped `docking/*` file against the
candidate wheel, permits generated bytecode caches and rejects modified,
missing or unshipped implementation files. API return values, protocol
observations, physical input and reviewed screenshots are separate evidence.
A capture labeled `captured-not-reviewed` is not visual acceptance.

The hosted-only `tests.visual.backends.windows.hosted_desktop` wrapper prepares
recognized first-login/runner windows, hides the verified Start surface,
records actions and temporarily scopes
the privacy-experience policy. It restores that policy afterward and does not
handle arbitrary user windows. Its cleanup policy must stay confined to
disposable hosted runners.

The Windows 11 hosted workflow first ensures the WSL package is installed,
using a pinned ARM64 MSI when `wsl --version` fails. This prevents repeated
inbox bootstrap prompts from interrupting physical input. It installs no Linux
distribution or Windows optional features; WSL is not a Docking dependency.

### Feature probes

Run as `python -m tests.visual.backends.windows.<module> --output PATH`. Use `--help`
for device/fixture arguments and destructive desktop modes.

| Area | Probe modules/options |
| --- | --- |
| Startup, placement, fullscreen | `smoke`, `desktop_smoke`, `fullscreen_smoke` |
| Windows, previews and ghosts | `window_smoke`, `windowidentity_smoke`, `capture_smoke`, `preview_smoke`, `preview_cache_smoke`, `ghost_smoke` |
| Catalog, pins and documents | `applications_smoke` (`--classic`, `--check-visibility`, `--check-artwork`), `classic_smoke`, `runtime_pin_smoke` (`--multiple-windows --uncataloged`, `--explorer-drop --shortcut installed`), `launch_provenance_smoke`, `applications_domain_smoke`, `application_export_smoke` |
| Files, folders and drag/drop | `file_smoke`, `trash_smoke --exercise-internal` |
| Search, focus and shortcuts | `shortcut_smoke`, `focus_smoke`, `search_icon_smoke`, `keyboardlayout_smoke --search-input`, `input_profile_smoke` |
| Tray and Explorer | `tray_provider_smoke`, `tray_smoke`; see [tray acceptance](#native-windows-tray-acceptance) |
| Battery and display | `battery_smoke --exercise-fixtures`, `brightness_probe`, `brightness_smoke` |
| Network and Bluetooth | `network_smoke`, `vpn_smoke`, `bluetooth_smoke`, `pairing_smoke` |
| Audio and media | `ambient_smoke`, `volume_smoke`, `micshield_smoke`, `music_smoke` |
| Privacy, power and session | `camshield_smoke`, `caffeine_smoke`, `powerprofiles_smoke`, `session_smoke` |
| Desktop utilities | `desktop_action_smoke`, `workspace_smoke`, `windowpicker_smoke`, `colorpicker_smoke`, `screenshot_smoke`, `idle_smoke`, `capslock_smoke` |
| Notifications and usage | `notifications_smoke`, `aiusage_smoke` |
| WebView2 | `webapp_smoke`, `webapp_download_smoke`, `webapp_worker_smoke` |
| Qt companion | `webengine_smoke`, `webengine_dock_smoke`, `webengine_live_smoke`, `webengine_download_smoke` |

Hardware and generated fixtures must be labeled correctly. For example, Wi-Fi,
Bluetooth and DDC/WMI snapshots exercise controls without proving a physical
connection or brightness change. Camera fixtures generate frames rather than
opening a physical camera. Session shutdown/restart needs an external observer;
an accepted API request alone does not prove the transition.

## Native Windows tray acceptance

Run in an owned interactive session with the regular dock stopped. Fixtures
create bounded provider processes and generated icons; they do not promote
icons through registry edits or change user notification settings. Explorer
continues to render balloons.

The `--stage tray` batch collects four fresh-process reports: independent
Explorer API comparison, installed-backend API comparison, GTK versions 0/4,
and GTK versions 3/4 with GUID-only updates. Release staging requires all four.

### API and ownership matrix

```powershell
python -m tests.visual.backends.windows.tray_provider_smoke --mode explorer --all --output artifacts/tray-explorer
python -m tests.visual.backends.windows.tray_provider_smoke --mode backend --all --installed-root artifacts/windows/runtime --wheel dist/docking.whl --output artifacts/tray-backend
```

The independent C# provider calls `Shell_NotifyIconA/W` and
`Shell_NotifyIconGetRect` without importing Docking. Its 40 cases cover x64/x86,
ANSI/Unicode, HWND/uID and GUID identity, versions 0/3/4 and valid V1/V2/V3/full
structures. A second PID tests GUID ownership collisions and GUID-only changes.

Acceptance checks exact raw/decoded flags, Shell32 source-size normalization,
callback ownership, title/SHOWTIP changes, copied pixels and cleanup. Legacy
V1/V2/V3 sizes remove SHOWTIP; the full structure preserves it. Successful API
calls do not establish visible tooltip, keyboard or focus behavior.

Backend traces retain bounded owned packet headers, lengths and decoded state,
without unrelated providers or uninitialized tails. A rejected ADD preserves
the original owner; GUID-only MODIFY metadata and live callback identity are
checked separately. Providers must exit normally and shell routing must return
to Explorer.

### GTK and physical input

```powershell
python -m tests.visual.backends.windows.tray_smoke --verify-geometry --verify-interactions --installed-root artifacts/windows/runtime --wheel dist/docking.whl --output artifacts/tray-gtk-v0-v4
python -m tests.visual.backends.windows.tray_smoke --verify-interactions --legacy-version 3 --modern-version 4 --guid-only-updates --installed-root artifacts/windows/runtime --wheel dist/docking.whl --output artifacts/tray-gtk-v3-v4-guid
```

Require actual hover, rich popups, SHOWTIP off/on/off, Enter/Space, Shift+F10,
native context-menu selection, pixels, hidden/delete/re-add and normal shutdown.
Version 0 uses its legacy keyboard callback sequence; versions 3/4 use
`NIN_KEYSELECT`. Geometry uses independent SDK queries and native popup bounds,
including movement, signed origins and invocation anchors.

Menu Escape must restore both native HWND and GTK row focus. First, duplicate
and ineligible locally attributed `NIM_SETFOCUS` requests must not forward to
Explorer; an outside provider click invalidates the return. A never-registered
provider's request still reaches Explorer. Attribution is bounded to five
minutes while awaiting return, with one second of duplicate grace afterward.

`--restart-explorer` is for an owned VM and verifies shell replacement/handoff.
`--notification-point X Y` requires actual balloon SHOW, HIDE, USERCLICK and
TIMEOUT callbacks. Without that point, balloon acceptance is `not-run`; callbacks
are never synthesized.

### Controlled provider inspection

```powershell
python -m tests.visual.backends.windows.tray_provider_smoke --mode explorer --case 64-unicode-guid-v4-full --hold-phase fresh --hold-seconds 120 --output artifacts/tray-hover-fresh
```

Holds are available at `fresh`, `showtip-on`, `showtip-off`, `collision`,
`guid-modify` and `readded`. Inspect fresh hover/SHOWTIP before GUID-only requests
or ownership collisions alter routing. Explorer may put new icons in overflow.

Owned provider journals expose callback sequences and revisioned
`command-NNNNNN.json` controls for rect, showtip, balloon, menu, focus, visibility,
deletion and snapshots. Do not overwrite unacknowledged commands. An owned
`continue` file ends a hold; `stop` requests normal exit; providers also have
bounded deadlines.

Contracts: [Shell_NotifyIcon](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw),
[NOTIFYICONDATA](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyicondataw),
[Shell_NotifyIconGetRect](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyicongetrect)
and [notification-area guidance](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area).
