# Changelog

Docking versions are listed in descending version order. Dates are in UTC.

## Unreleased

- Added contributor guidance, a code of conduct, and a security reporting policy.
- Moved developer requirements under `tools/` and updated setup commands.

## [2.34.0](https://github.com/edumucelli/docking/releases/tag/v2.34.0) (2026-11-21)

- Added import from Plank, Cairo-Dock, and Docking configuration files.
- Added a live editor for arranging dock items.
- Smoothed hover zoom on aligned docks and kept the resting shelf centered.
- Reduced routine log output and stabilized native desktop acceptance tests.

## [2.33.0](https://github.com/edumucelli/docking/releases/tag/v2.33.0) (2026-11-18)

- Added the **Always behind** dock visibility mode across desktop backends.
- Improved folder browsing actions, fixed folder preferences and window previews, and
  fixed Docker applet startup.
- Updated model pricing in AI Usage and expanded native desktop visual coverage.

## [2.32.0](https://github.com/edumucelli/docking/releases/tag/v2.32.0) (2026-11-15)

- Added a pinned-only layout mode and expanded Clippy's clipboard history with stack
  browsing.
- Fixed Caja launches after session autostart, WebKit diagnostics conflicts, and APT
  setup command pasting in Zsh.
- Updated the GTK/GIO typing baseline and moved the website to Tailwind 4 while
  retaining its appearance.

## [2.31.0](https://github.com/edumucelli/docking/releases/tag/v2.31.0) (2026-11-11)

- Added a shortcut-only mode for revealing the dock.
- Improved Windows preview installation instructions on the website and in the platform
  guides.
- Fixed data safety and accounting issues in desktop applets.

## [2.30.0](https://github.com/edumucelli/docking/releases/tag/v2.30.0) (2026-11-09)

- Added the Windows preview using native implementations behind Docking's shared
  platform interfaces, with a portable ZIP and per-user installer.
- Used typed messages for the Windows browser companion and shared process metadata
  across Windows features.

## [2.29.0](https://github.com/edumucelli/docking/releases/tag/v2.29.0) (2026-11-08)

- Added NetBSD desktop support and native packages.
- Added a KDE Connect applet for phone integration.
- Fixed error handling for desktop launches and tightened type checks at native
  boundaries.

## [2.28.3](https://github.com/edumucelli/docking/releases/tag/v2.28.3) (2026-11-04)

- Fixed Linux Mint Update Manager window detection and fitted thumbnail highlights to
  the image edges.
- Corrected SVG dependencies and Arch package validation and moved the website to
  CI-approved Hetzner deployment through Komodo.

## [2.28.2](https://github.com/edumucelli/docking/releases/tag/v2.28.2) (2026-11-03)

- Switched Weather requests to standard-library HTTP and replaced scikit-image with
  local metrics for visual tests.

## [2.28.1](https://github.com/edumucelli/docking/releases/tag/v2.28.1) (2026-11-02)

- Fixed Ambient white noise, low-battery charging visibility, and zero-percent
  peripheral battery readings.
- Added Portuguese, French, and Spanish website pages and improved search indexing.
- Moved development tools into platform-specific directories and completed the Unix
  platform interfaces needed by the Windows port.

## [2.28.0](https://github.com/edumucelli/docking/releases/tag/v2.28.0) (2026-10-25)

- Added native FreeBSD and OpenBSD packages with desktop checks in virtual machines.
- Added continuous dock alignment along a screen edge.
- Added a Telegram Web applet with persistent, isolated sessions.

## [2.27.0](https://github.com/edumucelli/docking/releases/tag/v2.27.0) (2026-10-24)

- Recognized running Steam games using local library metadata.
- Fixed X11 activation, pointer movement into stacks, and stack scrolling on Cinnamon
  XWayland.
- Preserved queued hardware control changes, rearmed edited alarms, and ignored stale
  applet replies after user actions or unload.

## [2.26.0](https://github.com/edumucelli/docking/releases/tag/v2.26.0) (2026-10-23)

- Improved recovery from failed remote requests and protected the artwork cache.
- Fixed Bluetooth discovery cleanup and audio mute controls across locales and paused
  recordings.
- Corrected matching for the Software Sources window when launched through `pkexec`.

## [2.25.0](https://github.com/edumucelli/docking/releases/tag/v2.25.0) (2026-10-21)

- Added Pulse and Wobble click effects alongside Bounce.
- Recovered from malformed applet preferences and fixed currency retries, certificate
  targets, and reminder state.
- Kept currency and unit conversion results finite at numeric limits.

## [2.24.0](https://github.com/edumucelli/docking/releases/tag/v2.24.0) (2026-10-19)

- Added a configurable dock item click effect.
- Protected Trash contents and restore metadata and corrected handling of local file
  URIs, dropped files, and generated launchers.
- Fixed desktop launch arguments, artwork fallback, and brightness output selection.

## [2.23.0](https://github.com/edumucelli/docking/releases/tag/v2.23.0) (2026-10-16)

- Fixed dock drag identity, reveal timing, and badge visibility.
- Improved Cinnamon placement and input regions and restored accurate native window
  captures.
- Supported NASA-hosted videos in Astronomy Picture of the Day and handled oversized
  saved timing values safely.

## [2.22.0](https://github.com/edumucelli/docking/releases/tag/v2.22.0) (2026-10-14)

- Added an optional X11 outline around the actual window while hovering over its
  preview.
- Added native window picking on Niri and improved Phoc placement and Wayland service
  coverage.
- Fixed search and calculator edge cases, configuration recovery, alarm handling,
  clipboard history, and several applet input and feed parsing issues.

## [2.21.0](https://github.com/edumucelli/docking/releases/tag/v2.21.0) (2026-10-10)

- Made preview thumbnail width configurable and grouped preview options in Preferences.
- Sharpened Recent Files thumbnails and loaded folder thumbnails only for visible rows.
- Reduced allocations in fuzzy search and sped up Wayland preview pixel conversion.

## [2.20.0](https://github.com/edumucelli/docking/releases/tag/v2.20.0) (2026-10-08)

- Highlighted the window preview thumbnail under the pointer and fixed Wayland capture
  cleanup.
- Filled missing translations across 74 locales and cached gettext catalogs.
- Moved System Monitor sampling to the background and bounded the work needed to open
  Recent Files and other stacks.
- Fixed signed RPM verification on Fedora and openSUSE.

## [2.19.0](https://github.com/edumucelli/docking/releases/tag/v2.19.0) (2026-10-07)

- Added native compositor services for windows, visibility, and idle state.
- Published RPM packages through Cloudsmith with installation instructions.
- Fixed Astronomy Picture of the Day fetching when NASA metadata labels end in a colon.

## [2.18.1](https://github.com/edumucelli/docking/releases/tag/v2.18.1) (2026-10-06)

- Kept Wayland stack popups within the available display area.
- Reduced tray CPU spikes by sharing StatusNotifier polling and avoiding unnecessary
  DBusMenu icon work.

## [2.18.0](https://github.com/edumucelli/docking/releases/tag/v2.18.0) (2026-10-06)

- Matched X11 windows using explicit application identities and repaired running-app
  detection and window actions on Cinnamon Wayland.
- Added native Cinnamon workspace and applet services and corrected the dock's Cinnamon
  Wayland window role.

## [2.17.0](https://github.com/edumucelli/docking/releases/tag/v2.17.0) (2026-10-05)

- Expanded window matching diagnostics and corrected GNOME StatusNotifier item IDs.
  Individual tray item read failures are isolated.

## [2.16.0](https://github.com/edumucelli/docking/releases/tag/v2.16.0) (2026-10-04)

- Fixed window event decoding on COSMIC, tooltip bounds on Wayland, and recovery when
  the GNOME Shell bridge disconnects.
- Added evidence about how windows were matched to applications to diagnostics reports.

## [2.15.0](https://github.com/edumucelli/docking/releases/tag/v2.15.0) (2026-10-03)

- Fixed Cinnamon Wayland placement and screen reservation without layer shell and
  corrected surface sizing on unanchored Wayland axes.
- Added a visual compositor test harness and expanded CI coverage for Wayland sessions.
- Included more placement information in diagnostics reports.

## [2.14.0](https://github.com/edumucelli/docking/releases/tag/v2.14.0) (2026-10-02)

- Made the APT setup instructions work when a Debian codename is missing.

## [2.13.9](https://github.com/edumucelli/docking/releases/tag/v2.13.9) (2026-10-01)

- Published verified APT instructions and added checks for package installation and
  desktop startup.
- Restored Astronomy Picture of the Day fetching from NASA Science and updated OpenAI
  model pricing in AI Usage.

## [2.13.8](https://github.com/edumucelli/docking/releases/tag/v2.13.8) (2026-10-01)

- Started publishing Debian packages to a Cloudsmith APT repository.

## [2.13.7](https://github.com/edumucelli/docking/releases/tag/v2.13.7) (2026-09-30)

- Fixed overflowing stack popups and Wayland compositor surface sizing.
- Corrected KWin's accessibility session bus discovery.

## [2.13.6](https://github.com/edumucelli/docking/releases/tag/v2.13.6) (2026-09-29)

- Kept preview popups within their available space and restored previews when the
  pointer moves back onto the same dock item.
- Corrected pointer transitions that trigger autohide on Wayland.

## [2.13.5](https://github.com/edumucelli/docking/releases/tag/v2.13.5) (2026-09-18)

- Corrected dock placement on edges occupied by an external panel.

## [2.13.4](https://github.com/edumucelli/docking/releases/tag/v2.13.4) (2026-09-17)

- Kept item insertion and removal animations tied to elapsed time when frames are
  delayed, and cleared stale geometry when an item is removed.

## [2.13.3](https://github.com/edumucelli/docking/releases/tag/v2.13.3) (2026-09-16)

- Fixed X11 urgency state refresh and aligned the dock's drawn geometry with its actual
  surface.
- Ran CI package builds in parallel with tests after the quality checks.

## [2.13.2](https://github.com/edumucelli/docking/releases/tag/v2.13.2) (2026-09-15)

- Kept autohide and hover zoom on time when frames arrive late or the animation
  changes direction.

## [2.13.1](https://github.com/edumucelli/docking/releases/tag/v2.13.1) (2026-09-14)

- Updated AI Usage model pricing and moved Python 3.10 CI coverage to Debian 12.

## [2.13.0](https://github.com/edumucelli/docking/releases/tag/v2.13.0) (2026-08-27)

- Added a Recent Files stack using the same stack interface as folders.
- Consolidated application discovery, launch, and matching code and added integration
  coverage.

## [2.12.1](https://github.com/edumucelli/docking/releases/tag/v2.12.1) (2026-08-11)

- Corrected X11 Intelligent Hide's evaluation of overlapping windows.

## [2.12.0](https://github.com/edumucelli/docking/releases/tag/v2.12.0) (2026-08-05)

- Added global search for applications and other desktop actions.
- Improved application matching using running executables and updated the Music applet's
  player integration.
- Expanded Wayland compositor detection and added idle notifications for generic Wayland
  sessions and window thumbnails for Phosh and Phoc.

## [2.11.0](https://github.com/edumucelli/docking/releases/tag/v2.11.0) (2026-07-30)

- Displayed status notifications on application launchers and showed application search
  results directly.
- Added Show HN and job feeds to Hacker News, hid empty Devices stacks, and wrapped long
  Today in History tooltips.

## [2.10.0](https://github.com/edumucelli/docking/releases/tag/v2.10.0) (2026-07-21)

- Added the country-based News applet and a WhatsApp applet.
- Added a configuration guide, an applet showcase, and automated theme gallery
  screenshots.

## [2.9.0](https://github.com/edumucelli/docking/releases/tag/v2.9.0) (2026-07-19)

- Added Plant Care, showed mounted network devices in Devices, and made the Add Applet
  menu more compact.
- Added controls for launcher overlays and kept refreshed tooltips below open popups.

## [2.8.0](https://github.com/edumucelli/docking/releases/tag/v2.8.0) (2026-07-17)

- Added a Devices applet with a live stack of mounted devices and opened a centered
  calendar from Clock.
- Allowed pinning entries by dragging them from Applications and improved curvature for
  short stacks.

## [2.7.0](https://github.com/edumucelli/docking/releases/tag/v2.7.0) (2026-07-15)

- Added a Reddit applet and made ty diagnostics fail CI validation.

## [2.6.0](https://github.com/edumucelli/docking/releases/tag/v2.6.0) (2026-07-10)

- Warned when emptying Trash fails, improved startup and quit handling, and updated AI
  model pricing.
- Completed translations across 74 locales.

## [2.5.1](https://github.com/edumucelli/docking/releases/tag/v2.5.1) (2026-07-08)

- Fixed Trash count fallback on MATE and reduced idle CPU usage.

## [2.5.0](https://github.com/edumucelli/docking/releases/tag/v2.5.0) (2026-07-07)

- Added a startup tips popup and rounded startup popup surfaces.
- Fixed X11 compositor detection and clarified the warning about lost transparency.
- Isolated applet lifecycle and model notification failures to keep the rest of the dock
  responsive.

## [2.4.1](https://github.com/edumucelli/docking/releases/tag/v2.4.1) (2026-07-04)

- Allowed custom icons for dock items and made Preferences fit shorter displays.
- Improved website metadata for search engines.

## [2.4.0](https://github.com/edumucelli/docking/releases/tag/v2.4.0) (2026-06-23)

- Added Wayfire IPC support, fixed dragging items off the dock on Wayland, and restored
  Session applet logout on GNOME.
- Enabled dropping files into Trash and opening the dock menu with Ctrl+right-click.
- Shared window-to-application matching and applied a seven-day dependency cooldown to
  CI pip installs.

## [2.3.0](https://github.com/edumucelli/docking/releases/tag/v2.3.0) (2026-06-21)

- Added a per-application Recent Documents menu and support for pinning executables by
  dragging them onto the dock.
- Fixed fallback commands for opening Trash and shared desktop-entry handling across
  launch paths.

## [2.2.0](https://github.com/edumucelli/docking/releases/tag/v2.2.0) (2026-06-17)

- Added a System Tray applet and a section for recently used applications.
- Added Niri IPC support for windows, previews, workspace and idle state, and monitor
  selection by connector. Enabled selection of the Hyprland backend.
- Added explanatory tooltips to settings.

## [2.1.0](https://github.com/edumucelli/docking/releases/tag/v2.1.0) (2026-06-13)

- Added a Run Application applet and a diagnostics dialog showing the current desktop
  session and backend.
- Corrected logout to use the actual session ID and expanded core and applet test
  coverage.

## [2.0.1](https://github.com/edumucelli/docking/releases/tag/v2.0.1) (2026-06-11)

- Added GPU details to System Monitor and pricing for newer Claude Opus and Sonnet
  models.

## [2.0.0](https://github.com/edumucelli/docking/releases/tag/v2.0.0) (2026-06-11)

- Enabled first-class Wayland support across packaged installations, including Wayland
  dependencies in place of forced X11 launchers.
- Added native support for KWin and COSMIC, Hyprland window tracking through IPC, and a
  GNOME Shell bridge.
- Fixed Wayland popup placement, anchored applet popups to their icons, and corrected
  GNOME's initial workspace state.

## [1.29.0](https://github.com/edumucelli/docking/releases/tag/v1.29.0) (2026-06-06)

- Added experimental Wayland runtime support and a reduced backend for sessions with
  limited capabilities.
- Routed X11 window actions, visibility, menu rows, and previews through shared backend
  interfaces.

## [1.28.0](https://github.com/edumucelli/docking/releases/tag/v1.28.0) (2026-05-30)

- Added Caffeine and expanded Battery details to include power and peripheral readings.

## [1.27.0](https://github.com/edumucelli/docking/releases/tag/v1.27.0) (2026-05-29)

- Added Last.fm, theme controls for filled indicators and active items, and a help
  tooltip for the pressure threshold.

## [1.26.0](https://github.com/edumucelli/docking/releases/tag/v1.26.0) (2026-05-28)

- Added Docker and Crypto applets and an option to sort the window list alphabetically
  in the context menu.

## [1.25.1](https://github.com/edumucelli/docking/releases/tag/v1.25.1) (2026-05-23)

- Added the UV index to Weather and showed the chart interval in Currency FX's tooltip.
- Fixed translated System Monitor tooltips and tightened settings slider widths.

## [1.25.0](https://github.com/edumucelli/docking/releases/tag/v1.25.0) (2026-05-23)

- Added dock distance from the screen edge and a pressure-based reveal option.

## [1.24.1](https://github.com/edumucelli/docking/releases/tag/v1.24.1) (2026-05-22)

- Shared HTTP and text handling across applets, improved Network state change detection,
  and simplified folder stack placement and indicator rendering.

## [1.24.0](https://github.com/edumucelli/docking/releases/tag/v1.24.0) (2026-05-21)

- Introduced a nested theme format, migrated older custom theme settings, and converted
  the bundled themes.

## [1.23.1](https://github.com/edumucelli/docking/releases/tag/v1.23.1) (2026-05-19)

- Applied theme changes at runtime, displayed relative applet update times, and kept
  dragged indicators aligned with the pointer.

## [1.23.0](https://github.com/edumucelli/docking/releases/tag/v1.23.0) (2026-05-18)

- Added ways to display open-window counts, raised the maximum number of indicator dots,
  and aligned count badges to physical pixels.

## [1.22.0](https://github.com/edumucelli/docking/releases/tag/v1.22.0) (2026-05-16)

- Added the Sunrise and Alarm applets.
- Required TLS 1.2 or newer for Cert Watch checks.

## [1.21.0](https://github.com/edumucelli/docking/releases/tag/v1.21.0) (2026-05-15)

- Added USB Watch and bundled NetworkManager support with the Flatpak.
- Corrected pointer barrier placement on HiDPI displays and updated translations.

## [1.20.0](https://github.com/edumucelli/docking/releases/tag/v1.20.0) (2026-05-13)

- Improved Flatpak integration and split AI Usage's provider-specific logic into
  backends.

## [1.19.0](https://github.com/edumucelli/docking/releases/tag/v1.19.0) (2026-05-12)

- Improved running application tracking and integrated Flatpak launchers and applet
  support.

## [1.18.0](https://github.com/edumucelli/docking/releases/tag/v1.18.0) (2026-05-08)

- Added an Always on Top mode, support for custom themes in the user config directory,
  and a system icon option for compatible applets.
- Reworked folder stack handling.

## [1.17.0](https://github.com/edumucelli/docking/releases/tag/v1.17.0) (2026-05-06)

- Added update checks against GitHub releases and improved applet selector autocomplete.
- Added a sysfs temperature backend and KDE Trash support.

## [1.16.0](https://github.com/edumucelli/docking/releases/tag/v1.16.0) (2026-04-30)

- Added the dark Pill theme.
- Added cost and token display options to AI Usage and clarified status and errors
  in Astronomy Picture of the Day, Bluetooth, and Cam Shield.

## [1.15.0](https://github.com/edumucelli/docking/releases/tag/v1.15.0) (2026-04-29)

- Added Hacker News and Thermals, clarified compact network speed units, and fitted
  applet labels within their badge bounds.

## [1.14.1](https://github.com/edumucelli/docking/releases/tag/v1.14.1) (2026-04-29)

- Fixed keyboard backend selection on MATE and added Codex session polling to AI Usage.

## [1.14.0](https://github.com/edumucelli/docking/releases/tag/v1.14.0) (2026-04-28)

- Added Currency FX and Mic Shield and updated the Nix GObject Introspection setup.

## [1.13.0](https://github.com/edumucelli/docking/releases/tag/v1.13.0) (2026-04-28)

- Added Cam Shield, Drag Share, and Caps Lock applets.
- Allowed incomplete translation catalogs in regular validation.

## [1.12.1](https://github.com/edumucelli/docking/releases/tag/v1.12.1) (2026-04-25)

- Added a New Year greeting inspired by Cairo-Dock and switched Debian packages to xz
  compression.

## [1.12.0](https://github.com/edumucelli/docking/releases/tag/v1.12.0) (2026-04-24)

- Added Astronomy Picture of the Day, Cert Watch, Desk Presence, and Speedtest.
- Cached folder stack entries and thumbnails.

## [1.11.0](https://github.com/edumucelli/docking/releases/tag/v1.11.0) (2026-04-23)

- Added support for Unity LauncherEntry overlays, including application badges and
  progress, and made folder stack unfolding configurable.

## [1.10.0](https://github.com/edumucelli/docking/releases/tag/v1.10.0) (2026-04-21)

- Added an option to open the most recent window on left-click and used WM_CLASS aliases
  to match transient launchers.
- Expanded headless tests and isolated the Python 3.14 smoke suite.

## [1.9.13](https://github.com/edumucelli/docking/releases/tag/v1.9.13) (2026-04-20)

- Made Bluetooth initialization avoid blocking dock startup.

## [1.9.12](https://github.com/edumucelli/docking/releases/tag/v1.9.12) (2026-04-20)

- Improved dock performance measurement and caching, corrected input method warnings in
  Preferences, and published theme screenshots.
- Added retries for transient APT failures in AppImage builds.

## [1.9.11](https://github.com/edumucelli/docking/releases/tag/v1.9.11) (2026-04-12)

- Documented the XWayland redraw freeze investigation and fixed a dock window
  integration test fixture.

## [1.9.10](https://github.com/edumucelli/docking/releases/tag/v1.9.10) (2026-04-11)

- Standardized launchers on XWayland for Wayland sessions and fixed AppImage and Nix
  launcher packaging.

## [1.9.9](https://github.com/edumucelli/docking/releases/tag/v1.9.9) (2026-04-08)

- Improved dock placement and Brightness, Keyboard Layout, and Screenshot behavior
  in Wayland desktop sessions.

## [1.9.8](https://github.com/edumucelli/docking/releases/tag/v1.9.8) (2026-04-08)

- Restored the Session applet's screen lock action.

## [1.9.7](https://github.com/edumucelli/docking/releases/tag/v1.9.7) (2026-04-07)

- Fixed Debian packaging for Python 3.10 and newer.

## [1.9.6](https://github.com/edumucelli/docking/releases/tag/v1.9.6) (2026-04-07)

- Expanded Bluetooth controls and added tools for managing keyboard layouts.

## [1.9.4](https://github.com/edumucelli/docking/releases/tag/v1.9.4) (2026-04-07)

- Added available networks, Wi-Fi and networking toggles, VPN entries, and
  connection settings to the Network applet's menu.

## [1.9.3](https://github.com/edumucelli/docking/releases/tag/v1.9.3) (2026-04-07)

- Added a settings menu to Volume.

## [1.9.2](https://github.com/edumucelli/docking/releases/tag/v1.9.2) (2026-04-07)

- Added battery time estimates and corrected the Debian package entry point.

## [1.9.1](https://github.com/edumucelli/docking/releases/tag/v1.9.1) (2026-04-05)

- Fixed the Weather city dialog and updated the Weather documentation.

## [1.9.0](https://github.com/edumucelli/docking/releases/tag/v1.9.0) (2026-04-05)

- Added a starter dock layout for first-run setups.

## [1.8.2](https://github.com/edumucelli/docking/releases/tag/v1.8.2) (2026-04-04)

- Reorganized Preferences and added a dock transparency slider.

## [1.8.1](https://github.com/edumucelli/docking/releases/tag/v1.8.1) (2026-04-04)

- Corrected the support link in the dock menu.

## [1.8.0](https://github.com/edumucelli/docking/releases/tag/v1.8.0) (2026-04-04)

- Added alarms and a seconds display to Clock.

## [1.7.0](https://github.com/edumucelli/docking/releases/tag/v1.7.0) (2026-04-03)

- Added configurable click actions for applications and documented the x64 and ARM64
  release downloads.

## [1.6.0](https://github.com/edumucelli/docking/releases/tag/v1.6.0) (2026-04-03)

- Added ARM64 release packages and improved desktop application matching.

## [1.5.0](https://github.com/edumucelli/docking/releases/tag/v1.5.0) (2026-04-03)

- Added an X11 blur-region hint so supporting compositors can blur the area behind the
  dock.

## [1.4.1](https://github.com/edumucelli/docking/releases/tag/v1.4.1) (2026-04-03)

- Fixed switching keyboard layouts on MATE.

## [1.4.0](https://github.com/edumucelli/docking/releases/tag/v1.4.0) (2026-04-02)

- Added the Paper and Candy themes, made configuration saving and the startup layout
  more robust, and introduced visual regression and Behave interaction tests.

## [1.3.1](https://github.com/edumucelli/docking/releases/tag/v1.3.1) (2026-03-31)

- Corrected the launcher targeted when an external file is dropped onto the dock.

## [1.3.0](https://github.com/edumucelli/docking/releases/tag/v1.3.0) (2026-03-31)

- Standardized applet UI and polling defaults and improved exception logging.

## [1.2.9](https://github.com/edumucelli/docking/releases/tag/v1.2.9) (2026-03-30)

- Moved display and screen-position helpers out of the runtime and added dead-code
  checks.

## [1.2.8](https://github.com/edumucelli/docking/releases/tag/v1.2.8) (2026-03-29)

- Simplified menu event handling and moved drag-related autohide updates into the
  drag-and-drop handler.

## [1.2.7](https://github.com/edumucelli/docking/releases/tag/v1.2.7) (2026-03-29)

- Applied changes to the dock hide mode immediately.

## [1.2.6](https://github.com/edumucelli/docking/releases/tag/v1.2.6) (2026-03-29)

- Tightened how the dock window receives and tracks autohide state.

## [1.2.5](https://github.com/edumucelli/docking/releases/tag/v1.2.5) (2026-03-29)

- Added folder stacks that open a popup to browse folder contents from the dock.

## [1.2.4](https://github.com/edumucelli/docking/releases/tag/v1.2.4) (2026-03-28)

- Unified the theme styling of applet popups and cleaned up their open and close
  handling.

## [1.2.3](https://github.com/edumucelli/docking/releases/tag/v1.2.3) (2026-03-28)

- Discovered applets from metadata without importing every implementation at startup.

## [1.2.2](https://github.com/edumucelli/docking/releases/tag/v1.2.2) (2026-03-27)

- Documented AI Usage and refined how the dock model notifies listeners.

## [1.2.1](https://github.com/edumucelli/docking/releases/tag/v1.2.1) (2026-03-27)

- Added AI Usage to track model use and corrected its Codex hook state lookup.

## [1.1.2](https://github.com/edumucelli/docking/releases/tag/v1.1.2) (2026-03-26)

- Marked the Debian package release as stable.

## [1.1.1](https://github.com/edumucelli/docking/releases/tag/v1.1.1) (2026-03-26)

- Corrected the applet and theme counts in the README and refreshed the release
  metadata.

## [1.1.0](https://github.com/edumucelli/docking/releases/tag/v1.1.0) (2026-03-26)

- Expanded developer documentation and added regression tests for dock hiding.

## [1.0.0](https://github.com/edumucelli/docking/releases/tag/v1.0.0) (2026-03-25)

- Polished the website's applet carousel and refreshed translations for the 1.0 release.

## [0.1.45](https://github.com/edumucelli/docking/releases/tag/v0.1.45) (2026-03-22)

- Launched the Docking website and renamed CPU Monitor to System Monitor.
- Made applet and UI discovery load code lazily and added Python 3.14 smoke coverage.

## [0.1.44](https://github.com/edumucelli/docking/releases/tag/v0.1.44) (2026-03-15)

- Saved icon order after reordering pinned items and fixed Today in History when the
  date changes.
- Improved applet worker and window listener cleanup.

## [0.1.43](https://github.com/edumucelli/docking/releases/tag/v0.1.43) (2026-03-14)

- Added Today in History and introduced Fedora and openSUSE smoke tests.

## [0.1.42](https://github.com/edumucelli/docking/releases/tag/v0.1.42) (2026-03-13)

- Added Stretch Coach and Random Trivia and prepared Launchpad PPA packaging.
- Removed compatibility for the old boolean autohide setting and synchronized package
  data declarations.

## [0.1.40](https://github.com/edumucelli/docking/releases/tag/v0.1.40) (2026-03-11)

- Added search to the Applications menu and a dodge mode that hides the dock when
  windows overlap it.

## [0.1.39](https://github.com/edumucelli/docking/releases/tag/v0.1.39) (2026-03-10)

- Added Bookmarks, Keyboard Layout, Pet, Quick Note, and Recent Files.
- Made Preferences more compact and expanded targeted GTK tests.

## [0.1.38](https://github.com/edumucelli/docking/releases/tag/v0.1.38) (2026-03-09)

- Fixed the dock jumping during autohide and introduced a shared worker for applet
  background tasks.

## [0.1.37](https://github.com/edumucelli/docking/releases/tag/v0.1.37) (2026-03-08)

- Enabled stricter Ruff checks for the Python code.

## [0.1.36](https://github.com/edumucelli/docking/releases/tag/v0.1.36) (2026-03-08)

- Improved recovery from malformed configuration values and revised how Preferences
  binds to settings.

## [0.1.35](https://github.com/edumucelli/docking/releases/tag/v0.1.35) (2026-03-08)

- Synchronized version metadata across the Python, Debian, Arch, Flatpak, Nix, RPM, and
  Snap packaging files.

## [0.1.34](https://github.com/edumucelli/docking/releases/tag/v0.1.34) (2026-03-08)

- Corrected Snap desktop and build asset paths and improved packaging and headless test
  tooling.

## [0.1.33](https://github.com/edumucelli/docking/releases/tag/v0.1.33) (2026-03-08)

- Refreshed application icons and fixed Network's device selection.

## [0.1.32](https://github.com/edumucelli/docking/releases/tag/v0.1.32) (2026-03-08)

- Included the applet catalog's assets in packaged builds.

## [0.1.31](https://github.com/edumucelli/docking/releases/tag/v0.1.31) (2026-03-08)

- Added a Preferences window and generated icons for the applet catalog.

## [0.1.30](https://github.com/edumucelli/docking/releases/tag/v0.1.30) (2026-03-07)

- Refined dock component setup and improved Bluetooth polling.

## [0.1.29](https://github.com/edumucelli/docking/releases/tag/v0.1.29) (2026-03-07)

- Simplified runtime assembly and standardized window tracker naming.

## [0.1.28](https://github.com/edumucelli/docking/releases/tag/v0.1.28) (2026-03-07)

- Separated layout from zoom calculations and expanded pointer scenarios covering
  interaction cleanup.

## [0.1.27](https://github.com/edumucelli/docking/releases/tag/v0.1.27) (2026-03-07)

- Kept the dock visible immediately after dropping an item.

## [0.1.26](https://github.com/edumucelli/docking/releases/tag/v0.1.26) (2026-03-07)

- Removed duplicate interaction forwarding from the dock window and updated the
  integration tests to exercise the handlers directly.

## [0.1.25](https://github.com/edumucelli/docking/releases/tag/v0.1.25) (2026-03-07)

- Simplified how the dock builds geometry for hover, menus, and drag-and-drop handling.

## [0.1.24](https://github.com/edumucelli/docking/releases/tag/v0.1.24) (2026-03-07)

- Split placement, interactions, and runtime setup out of the dock window and added
  focused tests for each.

## [0.1.23](https://github.com/edumucelli/docking/releases/tag/v0.1.23) (2026-03-07)

- Centralized dock geometry used by rendering, drag and drop, and pointer interactions,
  with new edge and geometry tests.

## [0.1.22](https://github.com/edumucelli/docking/releases/tag/v0.1.22) (2026-03-06)

- Improved autohide while the pointer moves between the dock and a window preview.

## [0.1.21](https://github.com/edumucelli/docking/releases/tag/v0.1.21) (2026-03-06)

- Moved edge spacing settings into themes.

## [0.1.20](https://github.com/edumucelli/docking/releases/tag/v0.1.20) (2026-03-06)

- Grouped icon menu options, added line and space separator styles, and allowed
  anchoring files.

## [0.1.19](https://github.com/edumucelli/docking/releases/tag/v0.1.19) (2026-03-06)

- Added 70 translation catalogs, consolidated translation tooling, and made tooltips
  optional.

## [0.1.18](https://github.com/edumucelli/docking/releases/tag/v0.1.18) (2026-03-05)

- Added X11 pointer barriers for revealing the dock and theme controls for indicator
  styles.
- Expanded the dock and applet documentation.

## [0.1.17](https://github.com/edumucelli/docking/releases/tag/v0.1.17) (2026-03-05)

- Added gettext translations and catalog compilation, including Snap build support.
- Added a current-workspace filter, applet anchoring, and configurable edge gaps.

## [0.1.15](https://github.com/edumucelli/docking/releases/tag/v0.1.15) (2026-03-05)

- Added logging and configuration branch tests and brought measured test coverage to
  95%.

## [0.1.14](https://github.com/edumucelli/docking/releases/tag/v0.1.14) (2026-03-04)

- Fixed the width of title labels on window previews.

## [0.1.13](https://github.com/edumucelli/docking/releases/tag/v0.1.13) (2026-03-04)

- Added a Power Profiles applet with fallbacks for different system power management
  services.

## [0.1.12](https://github.com/edumucelli/docking/releases/tag/v0.1.12) (2026-03-04)

- Added Bluetooth controls and improved handling of adapter power and discovery through
  BlueZ.

## [0.1.11](https://github.com/edumucelli/docking/releases/tag/v0.1.11) (2026-03-04)

- Added a Notifications applet with notification history.

## [0.1.10](https://github.com/edumucelli/docking/releases/tag/v0.1.10) (2026-03-04)

- Added a monitor selector for multi-monitor dock placement.

## [0.1.9](https://github.com/edumucelli/docking/releases/tag/v0.1.9) (2026-03-04)

- Restored autohide after context menus close and corrected license information in
  About.
- Documented Brightness, Color Picker, and Moon with screenshots.

## [0.1.8](https://github.com/edumucelli/docking/releases/tag/v0.1.8) (2026-03-04)

- Added Brightness, Color Picker, and Moon applets and the Gruvbox theme.
- Excluded Arch debug packages when publishing the primary release asset.

## [0.1.7](https://github.com/edumucelli/docking/releases/tag/v0.1.7) (2026-03-03)

- Added stable `latest` download aliases and improved how CI identifies the package
  files behind those aliases.

## [0.1.6](https://github.com/edumucelli/docking/tree/v0.1.6) (2026-03-03) · Tag only

- Standardized filenames and handling for release packages in CI.

## [0.1.5](https://github.com/edumucelli/docking/releases/tag/v0.1.5) (2026-03-03)

- Improved the Music applet, added timed screenshots, and drew custom Cairo applet
  icons.
- Expanded applet and UI test coverage.

## [0.1.4](https://github.com/edumucelli/docking/releases/tag/v0.1.4) (2026-03-02)

- Fixed a crash in window previews, updated applet icons and Snap metadata, and moved
  applets into their own packages.

## [0.1.3](https://github.com/edumucelli/docking/releases/tag/v0.1.3) (2026-03-01)

- Added Arch Linux and Nix release artifacts, published test coverage to Codecov, and
  laid the groundwork for Debian package tests.

## [0.1.2](https://github.com/edumucelli/docking/releases/tag/v0.1.2) (2026-03-01)

- Added Snap, AppImage, and RPM builds and grouped the applet menu by category.
- Stopped executing desktop launchers through a shell and added automated dependency,
  security, and coverage checks.

## [0.1.1](https://github.com/edumucelli/docking/releases/tag/v0.1.1) (2026-03-01)

- Added Flatpak release artifacts and the Quote applet, and improved Hydration's
  visuals.

## [0.1.0](https://github.com/edumucelli/docking/releases/tag/v0.1.0) (2026-02-28)

- First published release of the Linux dock, with pinned launchers, running-window
  indicators, hover zoom, window thumbnails, and drag-and-drop reordering.
- Included built-in applets, configurable screen edges, and Debian packaging.
