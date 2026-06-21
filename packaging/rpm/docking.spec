Name:           docking
Version:        %{?pkg_version}%{!?pkg_version:2.3.0}
Release:        1%{?dist}
Summary:        A lightweight, feature-rich dock for Linux written in Python with GTK 3 and Cairo

License:        GPL-3.0-or-later
URL:            https://github.com/edumucelli/docking
Source0:        %{name}-%{version}.tar.gz

Requires:       python3
Requires:       gtk-layer-shell
Recommends:     python3-pywayland
BuildRequires:  gcc
BuildRequires:  gettext
BuildRequires:  python3-devel
BuildRequires:  wayland-devel

%description
Docking is a lightweight, feature-rich dock for Linux written in Python
with GTK 3 and Cairo. Inspired by Plank and Cairo-Dock, it provides
pinned launchers, window indicators, previews, autohide, drag-and-drop,
and an extensible applet system.

%prep
%autosetup -n %{name}-%{version}

%build
# No build step required; Python package install is done in %install.

%install
rm -rf %{buildroot}

bash tools/i18n.sh --compile

mkdir -p %{buildroot}/usr/lib/docking/python
python3 -m pip install --no-compile --no-deps \
  --target %{buildroot}/usr/lib/docking/python .
rm -rf %{buildroot}/usr/lib/docking/python/*.dist-info
rm -rf %{buildroot}/usr/lib/docking/python/bin

mkdir -p %{buildroot}/usr/lib/docking/vendor
python3 -m pip install --no-compile --target %{buildroot}/usr/lib/docking/vendor \
  openmeteo-requests requests-cache retry-requests
rm -rf %{buildroot}/usr/lib/docking/vendor/*.dist-info
rm -rf %{buildroot}/usr/lib/docking/vendor/bin

py_minor="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
mkdir -p "%{buildroot}/usr/lib/docking/vendor-python${py_minor}"
python3 -m pip install --no-compile \
  --target "%{buildroot}/usr/lib/docking/vendor-python${py_minor}" \
  "pywayland>=0.4.18,<0.5"
rm -rf %{buildroot}/usr/lib/docking/vendor-python*/bin

install -Dm755 /dev/stdin %{buildroot}/usr/bin/docking << 'EOF'
#!/bin/sh
set -eu
PYTHON_VERSION="$(/usr/bin/python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYWAYLAND_VENDOR="/usr/lib/docking/vendor-python${PYTHON_VERSION}"
PYTHONPATH_PREFIX="/usr/lib/docking/python:/usr/lib/docking/vendor"
if [ -d "${PYWAYLAND_VENDOR}" ]; then
  PYTHONPATH_PREFIX="${PYWAYLAND_VENDOR}:${PYTHONPATH_PREFIX}"
fi
export PYTHONPATH="${PYTHONPATH_PREFIX}${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m docking.app "$@"
EOF

install -Dm755 packaging/shared/docking-camshield-helper \
  %{buildroot}/usr/bin/docking-camshield-helper

install -Dm644 packaging/shared/org.docking.Docking.desktop \
  %{buildroot}/usr/share/applications/org.docking.Docking.desktop

install -Dm644 packaging/shared/org.docking.camshield.policy \
  %{buildroot}/usr/share/polkit-1/actions/org.docking.camshield.policy

install -Dm755 packaging/shared/refresh-desktop-caches.sh \
  %{buildroot}/usr/lib/docking/refresh-desktop-caches

install -Dm644 docking/platform/backends/gnome/extension/metadata.json \
  %{buildroot}/usr/share/gnome-shell/extensions/docking-bridge@docking.org/metadata.json
install -Dm644 docking/platform/backends/gnome/extension/extension.js \
  %{buildroot}/usr/share/gnome-shell/extensions/docking-bridge@docking.org/extension.js

if [ -d packaging/deb/icons/hicolor ]; then
  mkdir -p %{buildroot}/usr/share/icons/hicolor
  cp -a packaging/deb/icons/hicolor/. %{buildroot}/usr/share/icons/hicolor/
fi

%post
if [ -x /usr/lib/docking/refresh-desktop-caches ]; then
  /usr/lib/docking/refresh-desktop-caches
fi
if command -v gnome-extensions >/dev/null 2>&1 \
   && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  gnome-extensions enable docking-bridge@docking.org || true
fi

%postun
if [ -x /usr/lib/docking/refresh-desktop-caches ]; then
  /usr/lib/docking/refresh-desktop-caches
fi
if [ "$1" -eq 0 ] && command -v gnome-extensions >/dev/null 2>&1 \
   && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  gnome-extensions disable docking-bridge@docking.org || true
fi

%files
%license LICENSE
/usr/bin/docking
/usr/bin/docking-camshield-helper
/usr/lib/docking/python
/usr/lib/docking/vendor
/usr/lib/docking/vendor-python*
/usr/lib/docking/refresh-desktop-caches
/usr/share/applications/org.docking.Docking.desktop
/usr/share/polkit-1/actions/org.docking.camshield.policy
/usr/share/gnome-shell/extensions/docking-bridge@docking.org
/usr/share/icons/hicolor

%changelog
* Sun Jun 21 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 2.3.0-1
- Release 2.3.0.

* Wed Jun 17 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 2.2.0-1
- Add recently used apps section between pinned launchers and running apps
- Add settings option tooltips across the preferences window
- Add connector-based monitor targeting in config and preferences
- Add Run Application and System Tray icons to the applet catalog
- Make README applet entries collapsible with details/summary tags
- Fix broken ARCHITECTURE.md link and untrack stale docs from git
- Prevent Docking's own windows from being tracked as running apps

* Sat Jun 13 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 2.1.0-1
- Add Niri IPC backend with native window tracking, previews, and color picker
- Add runtime diagnostics dialog for backend and session troubleshooting
- Add Run Application applet for quick command launching
- Expand test coverage across core and applet modules

* Wed Jun 11 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 2.0.1-1
- Refresh README, packaging docs, and website for v2.0.1
- Add GPU utilization summary to System Monitor applet
- Fix Opus 4.7/4.8 and Sonnet 4.5/4.6 pricing tiers
- Replace getattr calls with direct attribute access in placement.py

* Sat Jun 06 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.29.0-1
- Add experimental Wayland runtime support
- Add reduced session backend
- Refactor X11 backend service layout
- Move X11 dodge monitor behind visibility service
- Complete X11 session backend shape
- Route preview popup through preview service
- Use window snapshots for menu window rows
- Wire X11 runtime through window service
- Add X11 window service facade
- Add platform backend contracts

* Sat May 30 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.28.0-1
- Stop tracking architecture notes
- Add battery power and peripheral details
- Add Caffeine applet

* Sat May 30 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.27.0-1
- Move pressure threshold help to info tooltip
- Bump ty from 0.0.37 to 0.0.40
- Bump ruff from 0.15.13 to 0.15.15
- Add Last.fm applet
- Add indicator fill and active item theme styles

* Thu May 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.26.0-1
- Add optional alphabetical sorting for context menu window list
- Add Crypto applet
- Add Docker applet

* Sun May 24 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.25.1-1
- Tighten settings slider widths
- Fix system monitor tooltip translations
- Show Currency FX chart interval in tooltip
- Add UV index to weather applet

* Sat May 23 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.25.0-1
- Add pressure reveal support
- Add distance from edge setting

* Sat May 23 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.24.1-1
- Group renderer state inputs
- Detect network applet state changes
- Adopt shared applet HTTP helpers
- Dispatch indicator rendering by style
- Use position enum for folder stack placement
- Add shared applet text and HTTP helpers
- Add shared math clamp helpers
- Simplify environment and XDG path helpers

* Thu May 21 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.24.0-1
- Convert bundled themes to nested schema
- Document theme schema naming conventions
- Support full nested theme schema
- Rename theme horizontal padding field
- Add theme migration infrastructure

* Tue May 19 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.23.1-1
- Keep dragged indicators under cursor
- Propagate runtime theme updates
- Use relative freshness update labels

* Mon May 18 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.23.0-1
- Snap count badge geometry to pixels
- Add window count indicator display modes
- Raise running indicator dot cap
- Bump ruff from 0.15.12 to 0.15.13
- Bump ty from 0.0.34 to 0.0.37
- Add GPL header to docking Python files

* Sun May 17 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.22.0-1
- Add Alarm applet
- Add README project badges
- Add Sunrise applet
- Tighten test None narrowing
- Require TLS 1.2 for Cert Watch checks

* Fri May 15 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.21.0-1
- Update translations
- Fix HiDPI pointer barrier placement
- Add USB Watch applet
- Bundle NetworkManager in Flatpak

* Wed May 13 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.20.0-1
- Refactor AI usage backends
- Update Flatpak integration

* Tue May 12 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.19.0-1
- Deduplicate Flatpak runtime detection
- Improve Flatpak launcher integration
- Prepare applets for Flatpak runtime
- Apply section 4 running-app code patch

* Fri May 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.18.0-1
- Load custom themes from user config
- Add system icon source option for supported applets
- Add "Always on Top" hide mode
- Fix ty type-checking warnings
- Refactor folder stack handling

* Wed May 06 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.17.0-1
- Add KDE trash support
- Add sysfs thermals backend
- Improve applet selector autocomplete
- Bump ty from 0.0.32 to 0.0.34
- Add update release checks

* Thu Apr 30 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.16.0-1
- Release: bump version to 1.16.0
- Add dark pill dock theme
- Apply applet usability updates

* Wed Apr 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.15.0-1
- Release: bump version to 1.15.0
- Fit applet icon labels within badge bounds
- Clarify compact network speed units
- Add Hacker News and Thermals applets

* Wed Apr 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.14.1-1
- Release: bump version to 1.14.1
- Poll Codex sessions in AI usage applet
- Fix keyboard layout backend selection on MATE

* Tue Apr 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.14.0-1
- Release: bump version to 1.14.0
- Add Currency FX and Mic Shield applets
- Refactor gobject-introspection placement in default.nix

* Tue Apr 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.13.0-1
- Release: bump version to 1.13.0
- Add Caps Lock applet
- Relax i18n catalog completeness checks
- Add Drag Share applet
- Bump ruff from 0.15.11 to 0.15.12
- Bump ty from 0.0.22 to 0.0.32
- Add Cam Shield applet

* Sat Apr 25 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.12.1-1
- Use xz compression for deb packages
- Add Cairo-Dock-inspired New Year greeting

* Fri Apr 24 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.12.0-1
- Release: bump version to 1.12.0
- Add APOD, Cert Watch, Desk Presence, and Speedtest applets
- Cache folder stack content and thumbnails

* Thu Apr 23 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.11.0-1
- Release: bump version to 1.11.0
- Add folder stack unfold behavior setting
- Add Unity LauncherEntry overlays
- Bump ruff from 0.15.9 to 0.15.10
- Refactor worktree cleanups

* Tue Apr 21 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.10.0-1
- Release: bump version to 1.10.0
- Docs: document i18n update workflow
- Skip Bluetooth logs for missing optional values
- Match transient launchers by WM_CLASS aliases
- Add most-recent left-click action
- Ci: isolate Python 3.14 smoke test files
- Test: expand headless coverage harnesses

* Mon Apr 20 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.13-1
- Avoid blocking Bluetooth applet startup

* Mon Apr 20 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.12-1
- Fix preferences IM warnings and bump to 1.9.12
- Retry transient AppImage apt failures
- Adjust theme screenshot layout in README
- Add theme screenshots to README
- Remove unused shelf transform sizes
- Improve dock performance instrumentation and caching

* Sun Apr 12 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.11-1
- Fix dock window integration test stub
- Document XWayland freeze investigation
- Investigate XWayland redraw issues

* Sat Apr 11 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.10-1
- Fix Nix and AppImage launcher packaging
- Fix packaging CI checks
- Align package launchers on Wayland
- Force Debian launches to use X11 backend on Wayland

* Thu Apr 09 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.9-1
- Align placement test formatting with pinned Ruff
- Apply Wayland compatibility fixes and bump version to 1.9.9

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.8-1
- Align session test formatting with pinned Ruff
- Fix session applet lock action and bump version to 1.9.8

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.7-1
- Fix Debian packaging for Python 3.10+ and bump 1.9.7

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.6-1
- Pin ruff and ty and align keyboardlayout formatting
- Add keyboard layout tools and bump version to 1.9.6
- Expand Bluetooth applet menu and bump version to 1.9.5

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.4-1
- Expand network applet menu and bump version to 1.9.4

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.3-1
- Add volume settings menu and bump version to 1.9.3

* Tue Apr 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.2-1
- Add battery time estimates and fix deb entrypoint

* Sun Apr 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.1-1
- Refresh architecture and weather docs, fix weather city dialog, and bump vers...

* Sun Apr 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.0-1
- Seed starter dock on first run and bump version to 1.9.0

* Sat Apr 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.8.2-1
- Reorganize preferences tabs and bump version to 1.8.2
- Add transparency slider

* Sat Apr 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.8.1-1
- Fix support menu link and bump version to 1.8.1

* Sat Apr 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.8.0-1
- Add clock alarms and seconds display

* Sat Apr 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.7.0-1
- Add configurable app click actions and bump version to 1.7.0
- Skip CI for website-only changes
- Document x64 and arm64 release assets

* Fri Apr 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.6.0-1
- Fix ARM64 Arch CI image
- Fix remaining ARM64 package builders
- Fix ARM64 packaging CI jobs
- Add ARM64 package builds and bump version to 1.6.0
- Improve desktop candidate matching

* Fri Apr 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.5.0-1
- Add X11 blur region hint and bump version to 1.5.0

* Fri Apr 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.4.1-1
- Fix keyboard layout switching on MATE and bump version to 1.4.1

* Thu Apr 02 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.4.0-1
- Harden config persistence and dock startup layout
- Clarify applet rendering and state comments
- Add visual regression and BDD interaction coverage
- Add paper and candy themes
- Refresh README and website docs

* Tue Mar 31 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.3.1-1
- Fix external drop targeting and bump version to 1.3.1

* Tue Mar 31 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.3.0-1
- Improve exception logging and bump version to 1.3.0
- Constantize applet UI and fetch defaults

* Tue Mar 31 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.9-1
- Refine screen position helpers and bump version to 1.2.9
- Move display helpers out of runtime
- Remove dead UI helpers and add test-only code scan
- Add vulture dead code checks
- Remove stale UI helpers and normalize dashes

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.8-1
- Simplify dock UI helpers and bump version to 1.2.8

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.7-1
- Reconcile hide mode changes immediately and bump version to 1.2.7

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.6-1
- Tighten dock window autohide contracts and bump version to 1.2.6

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.5-1
- Simplify dock window assembly and bump version to 1.2.5
- Add folder stacks popup

* Sat Mar 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.4-1
- Refine applet popups and bump version to 1.2.4

* Sat Mar 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.3-1
- Make applet discovery metadata-only and bump version to 1.2.3
- Skip CI for docs and site-only changes
- Remove simplification plan from repo

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.2-1
- Refine dock model listeners and bump version to 1.2.2
- Document AI Usage applet

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.1-1
- Fix aiusage Codex hook state lookup
- Rename loggers and bump version to 1.2.1
- Add AI usage applet and bump version to 1.2.0

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.1.2-1
- Mark Debian release stable and bump version to 1.1.2

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.1.1-1
- Update 1.1.1 release metadata
- Update stale README applet and theme counts

* Thu Mar 26 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.1.0-1
- Release 1.1.0
- Increase coverage and add dock hide regression tests
- Expand long-form module documentation

* Thu Mar 26 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.0.0-1
- Sync locale catalogs
- Release 1.0.0
- Polish Docking website carousel interactions
- Update Docking website interactions and applet carousel

* Sun Mar 22 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.45-1
- Rename system monitor applet and release 0.1.45
- Refine Docking website color system
- Add initial Docking website landing page
- Stabilize UI GI fallback mocks
- Fix settings GI fallback mock
- Lazy-load UI package exports
- Install pycairo in Python 3.14 smoke lanes
- Use smoke tests for Python 3.14 lanes
- Add Python 3.14 CI coverage
- Decouple applet catalog from imports

* Mon Mar 16 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.44-1
- Fix app bootstrap import test stubs
- Harden CI test dependencies
- Fix UI test GI and dodge monitor isolation
- Guard applet polling workers
- Fix today in history day rollover
- Disconnect dodge window handlers on stop
- Persist dock model pin order changes
- Rename applet present method
- Expand Ruff lint coverage

* Sat Mar 14 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.43-1
- Add today in history applet
- Add openSUSE Wnck typelib
- Add openSUSE GI typelibs
- Use Xvfb directly in openSUSE smoke
- Use venv for openSUSE smoke job
- Harden openSUSE smoke setup
- Fix openSUSE smoke dependencies
- Add openSUSE smoke CI job
- Add Fedora smoke CI job

* Sat Mar 14 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.42-1
- Sync package data declarations
- Add random trivia applet
- Fix type-check warnings
- Add stretch coach applet
- Add Launchpad PPA packaging scaffolding
- Replace magic numbers with named constants
- Remove legacy autohide config compatibility

* Wed Mar 11 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.40-1
- Add autohide dodge and bump version to 0.1.40
- Add application menu search

* Tue Mar 10 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.39-1
- Add new applets and bump version to 0.1.39
- Add targeted UI coverage tests
- Tighten preferences dialog layout
- Rewrite architecture documentation

* Mon Mar 09 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.38-1
- Fix autohide jump and bump version to 0.1.38
- Add shared applet worker service

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.37-1
- Enable stricter Ruff checks

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.36-1
- Harden config normalization and exception logging
- Refactor settings bindings

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.35-1
- Release 0.1.35.

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.34-1
- Fix snap build asset source paths
- Fix snap desktop asset install paths
- Harden headless tool and applet tests
- Improve packaging and release tooling
- Update README header image

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.33-1
- Refresh app icons and fix network device selection

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.32-1
- Include applet catalog assets in packages

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.31-1
- Use generated applet catalog icons
- Add dock preferences window

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.30-1
- Refine dock component assembly and bluetooth polling

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.29-1
- Normalize dock window tracker naming
- Refine dock runtime assembly

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.28-1
- Refine dock interaction cleanup
- Split dock layout from zoom module
- Expand module documentation and pointer scenarios

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.27-1
- Keep dock visible after drag drop

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.26-1
- Remove dock window interaction wrappers

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.25-1
- Simplify dock geometry builder

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.24-1
- Decompose DockWindow collaborators

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.23-1
- Refine dock geometry boundaries

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.22-1
- Refine preview autohide behavior
- Tighten internal attribute access

* Sat Mar 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.21-1
- Make edge spacing theme-owned
- Update README to match current config and locale count

* Fri Mar 06 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.20-1
- Apply docking changes and bump version to 0.1.20
- Group icon menu options and refresh i18n catalogs

* Fri Mar 06 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.19-1
- Sync i18n catalogs for tooltip toggle
- Add tooltip toggle setting and bump version to 0.1.19
- Add 70 locale catalogs and complete existing translations
- Consolidate translation tooling into unified i18n script
- Ignore compiled gettext .mo catalogs
- Enforce strict i18n checks and update display menu translations

* Fri Mar 06 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.18-1
- Apply all-features updates and bump version to 0.1.18

* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.17-1
- Fix(snap): use CRAFT_PART_SRC for translation compile script
- Feat: apply plank quick wins and bump version to 0.1.17
- Feat(i18n): add gettext translations, compile step, and bump 0.1.16

* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.15-1
- Test: raise coverage to 95% and bump version to 0.1.15
- Test: expand log and config branch coverage

* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.14-1
- Fix preview title chip width and bump version to 0.1.14
- Keep power profile documentation in code; remove standalone doc file

* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.13-1
- Add Power Profiles applet with backend fallbacks and extensive docs; bump 0.1.13

* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.12-1
- Add Bluetooth applet and harden BlueZ power/discovery flow; bump 0.1.12

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.11-1
- Style: apply ruff formatting in notifications files
- Feat: add notifications applet with history and bump version to 0.1.11

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.10-1
- Feat: add multi-monitor dock selection and bump version to 0.1.10

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.9-1
- Fix: reconcile autohide after menu close and bump version to 0.1.9
- Docs: add brightness/color/moon applet sections and screenshots
- Fix: configure about dialog license metadata
- Chore: remove committed patch artifact

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.8-1
- Feat: apply patch changes and bump release version to 0.1.8
- Docs: update latest release installation links
- Ci: ignore arch debug package in release normalization

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.7-1
- Ci: harden release asset normalization for latest aliases
- Ci: add stable latest release asset aliases

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.6-1
- Ci: standardize release assets and bump version to 0.1.6

* Wed Mar 04 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.5-1
- Add music applet improvements and timed screenshot captures
- Raise tests to 95% coverage and expand applet/UI coverage
- Extract About dialog module and commit current WIP
- Docs: expand architecture narratives for core dock modules
- Create custom Cairo icons for applets visual identity

* Tue Mar 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.4-1
- Refactor applets into modular packages and migrate weather applet
- Refine applet icons and harden preview/window error handling
- Apply preview segfault patch with merge conflict resolution
- Extract DockItem to core and stabilize runtime imports
- Standardize exception logging across runtime modules
- Increase coverage to 80% with applet and model integration tests
- Refine Session applet with Cairo user-avatar icon
- Improve Snap metadata and icon wiring for lint warnings

* Sun Mar 01 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.3-1
- Fix Arch build by syncing PKGBUILD pkgver with project version
- Increase test coverage to 75% with app/platform/ui tests and add Debian autop...
- Fix Codecov workflow condition by using env token guard
- Fix Nix build inputs and relax weather deps for CI reproducibility
- Require CODECOV_TOKEN for coverage upload in CI
- Polish README badge and layout tweaks
- Add Python version badge to README
- Add CI/release/coverage badges and publish coverage to Codecov
- Adjust applet categories and add Nix packaging with CI artifacts
- Make Arch source tarball fallback work without git metadata

* Sun Mar 01 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.2-1
- Rename CI Ubuntu test job id to test-ubuntu
- Replace raw applet id strings with AppletId constants
- Centralize applet IDs and reuse them in Applets category mapping
- Group Applets submenu by categories and fix RPM arch packaging
- Fix Snap asset sourcing and stabilize RPM Python install layout
- Fix Snap asset path resolution and RPM install fallback
- Fix Snap build paths and add initial RPM packaging pipeline
- Fix Snap/AppImage CI packaging failures
- Center README header image with valid CSS
- Expand integration coverage and add AppImage artifact pipeline

* Sun Mar 01 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.1-1
- Add Quote applet, improve hydration visuals, and publish CI coverage artifacts
- Fix Flatpak AppStream icon lookup during compose
- Fix Flatpak builder option compatibility
- Fix Flatpak builder networking in CI and local script
- Fix Flatpak CI permissions by using user-scoped remotes
- Add Flatpak packaging and CI build artifact

* Sat Feb 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.0-1
- Fix release version parsing for Python 3.10 runners
- Fix CI YAML syntax error in release version check
- Make CI release automation master-only with version gating
- Another adjustement
- Remove visible borders from README title table
- Use table layout for README title icon alignment
- Tune README title icon alignment via local render loop
- Fine-tune README title icon vertical alignment
- Align README title icon using trimmed header asset
- Refresh WM_CLASS mapping on each window scan

