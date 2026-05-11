# Flathub Submission Plan

Goal: get Docking ready for Flathub under the app ID `cc.docking.Docking`, while keeping the GitHub release Flatpak artifacts usable as normal Docking release downloads.

References:

- Flathub submission process: <https://docs.flathub.org/docs/for-app-authors/submission>
- Flathub requirements: <https://docs.flathub.org/docs/for-app-authors/requirements>
- Flathub app maintenance: <https://docs.flathub.org/docs/for-app-authors/updates>

Important policy note: Flathub says submission pull requests must not be generated, opened, or automated using AI tools or agents. This plan can be used to prepare and verify the submission, but Eduardo should personally create and submit the Flathub PR.

## 0. Current Local Iteration Cycle

- [ ] Continue iterating on local Flatpak issues from the Docking branch `flathub-application-prep`.
- [ ] Commit fixes to `flathub-application-prep` and push them to the current upstream PR branch.
- [ ] Rebuild the local Flatpak bundle from the Docking repository:

```bash
./packaging/flatpak/build.sh
```

- [ ] Install the rebuilt bundle into the user Flatpak installation:

```bash
flatpak install --user -y --reinstall artifacts/cc.docking.Docking.flatpak
```

- [ ] Stop any already-running Docking Flatpak before testing the new install:

```bash
flatpak kill cc.docking.Docking || true
```

- [ ] Run Docking from the freshly installed Flatpak:

```bash
flatpak run cc.docking.Docking
```

- [ ] For host desktop icon issues, validate Calculator resolves to the host icon and not the generic fallback:

```bash
flatpak run --command=python3 cc.docking.Docking -c 'import gi, hashlib; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk; from docking.platform.launcher import Launcher; theme = Gtk.IconTheme.get_default(); launcher = Launcher();
for name in ("org.gnome.Calculator", "gnome-calculator", "application-x-executable"):
    info = theme.lookup_icon(name, 48, 0)
    print(name, "lookup", None if info is None else info.get_filename())
    pixbuf = launcher.load_icon(name, 48)
    data = pixbuf.get_pixels() if pixbuf else b""
    print(name, "pixbuf", bool(pixbuf), "sha", hashlib.sha256(data).hexdigest()[:16] if pixbuf else None)'
```

- [ ] Confirm `org.gnome.Calculator` and `gnome-calculator` produce the same hash, and `application-x-executable` produces a different hash.
- [ ] After pushing a new Docking commit, update the Flathub fork manifest source URL and SHA-256 to that exact commit while the branch is still being iterated.

## 1. Merge Docking Preparation

- [ ] Merge PR #61, `flathub-application-prep`, into `master`.
- [ ] Confirm the merged branch keeps the Flatpak app ID as `cc.docking.Docking`.
- [ ] Confirm the merged branch includes `website/.well-known/org.flathub.VerifiedApps.txt`.
- [ ] Confirm the merged branch includes `packaging/flatpak/cc.docking.Docking.json`.
- [ ] Confirm the merged branch includes `packaging/flatpak/cc.docking.Docking.metainfo.xml`.
- [ ] Confirm the merged branch includes `packaging/flatpak/cc.docking.Docking.desktop`.
- [ ] Confirm the merged branch includes `packaging/flatpak/python3-dependencies.json`.
- [ ] Confirm CI Flatpak jobs build with `packaging/flatpak/build.sh`.
- [ ] Confirm CI release artifact collection still publishes public release files as `docking-<version>-linux-<arch>.flatpak` and `docking-latest-linux-<arch>.flatpak`.

## 2. Release Docking

- [ ] Merge the version bump PR for `1.18.0`.
- [ ] Create the `v1.18.0` tag from the merged release commit.
- [ ] Push the `v1.18.0` tag.
- [ ] Wait for the release workflow to complete.
- [ ] Confirm the GitHub release includes x86_64 and aarch64 Flatpak artifacts.
- [ ] Download and smoke-test the x86_64 release Flatpak artifact.
- [ ] Download and smoke-test the aarch64 release Flatpak artifact if hardware or CI access is available.
- [ ] Confirm the release source archive is available from GitHub for `v1.18.0`.

## 3. Verify Domain Ownership

- [ ] Confirm `https://docking.cc/.well-known/org.flathub.VerifiedApps.txt` is publicly reachable.
- [ ] Confirm the verification file contains the expected Flathub verification content for `cc.docking.Docking`.
- [ ] Confirm the website deployment serving `docking.cc` includes the current `website/.well-known/` content.
- [ ] Keep the verification file live through Flathub review and after merge.

## 4. Prepare The Flathub Submission Fork

- [ ] Fork `flathub/flathub` on GitHub.
- [ ] Make sure the fork includes all branches, not only `master`.
- [ ] Clone the fork from the `new-pr` branch:

```bash
git clone --branch=new-pr git@github.com:<your-github-username>/flathub.git
cd flathub
```

- [ ] Create a submission branch from `new-pr`:

```bash
git checkout -b add-cc.docking.Docking new-pr
```

- [ ] Do not base the submission on `master`.
- [ ] Do not merge Flathub `master` into the submission branch.

## 5. Add Required Flathub Files

- [ ] Copy `packaging/flatpak/cc.docking.Docking.json` from Docking into the Flathub fork root as `cc.docking.Docking.json`.
- [ ] Copy `packaging/flatpak/python3-dependencies.json` from Docking into the Flathub fork root as `python3-dependencies.json`.
- [ ] Add `flathub.json` only if an architecture exception or special Flathub behavior is needed.
- [ ] Do not copy Docking source code into the Flathub submission repository.
- [ ] Do not copy build artifacts into the Flathub submission repository.
- [ ] Do not copy the generated GitHub release `.flatpak` bundle into the Flathub submission repository.

## 6. Convert The Manifest Source For Flathub

- [ ] Replace the local source entry:

```json
{
  "type": "dir",
  "path": "../.."
}
```

- [ ] Use a stable upstream source for the released Docking version, preferably the `v1.18.0` Git tag or release archive.
- [ ] If using a Git source, point to the Docking repository and tag:

```json
{
  "type": "git",
  "url": "https://github.com/edumucelli/docking.git",
  "tag": "v1.18.0",
  "commit": "<exact-v1.18.0-commit>"
}
```

- [ ] If using an archive source, use the GitHub `v1.18.0` archive URL and add the exact SHA-256 checksum.
- [ ] Confirm the manifest does not require network access during build.
- [ ] Confirm every Python dependency is represented in `python3-dependencies.json`.
- [ ] Confirm `python3-dependencies.json` sources use public URLs and fixed hashes.

## 7. Review Permissions And Runtime

- [ ] Confirm the runtime is hosted on Flathub.
- [ ] Confirm the runtime version is supported by Flathub.
- [ ] Review every `finish-args` entry and keep only required permissions.
- [ ] Document why `--share=network` is required, or remove it if Docking can function acceptably without static network access.
- [ ] Document why `--socket=x11` is required for Wnck/dock behavior.
- [ ] Document why `--device=dri` is required, or remove it if not needed.
- [ ] Document why `--talk-name=org.freedesktop.Notifications` is required.
- [ ] Document why `--system-talk-name=org.freedesktop.NetworkManager` is required, or remove it if optional.
- [ ] Confirm the app behaves gracefully under the Flatpak sandbox.

## 8. Validate Metadata

- [ ] Confirm `cc.docking.Docking.metainfo.xml` has valid AppStream metadata.
- [ ] Confirm the metainfo ID is `cc.docking.Docking`.
- [ ] Confirm the launchable desktop ID is `cc.docking.Docking.desktop`.
- [ ] Confirm the release entry includes `1.18.0`.
- [ ] Confirm screenshots are valid public URLs.
- [ ] Confirm summary and description are clear user-facing English.
- [ ] Confirm the license metadata is correct.
- [ ] Confirm the project URL points to the correct Docking homepage or repository.
- [ ] Confirm the developer/name metadata is correct.

## 9. Validate Desktop Integration

- [ ] Confirm `cc.docking.Docking.desktop` uses `Name=Docking`.
- [ ] Confirm `cc.docking.Docking.desktop` uses `Exec=docking`.
- [ ] Confirm `cc.docking.Docking.desktop` uses `Icon=cc.docking.Docking`.
- [ ] Confirm the Flatpak install path includes the desktop file under `/app/share/applications/`.
- [ ] Confirm app icons are installed as `cc.docking.Docking`.
- [ ] Confirm symbolic icons are installed as `cc.docking.Docking-symbolic`.
- [ ] Confirm legacy `org.docking.Docking` icons are installed only if still needed for runtime compatibility.

## 10. Build Locally With Flathub Builder

- [ ] Install Flathub Builder:

```bash
flatpak install -y flathub org.flatpak.Builder
```

- [ ] Add the Flathub remote if needed:

```bash
flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

- [ ] From the Flathub fork submission branch, build and install:

```bash
flatpak run --command=flathub-build org.flatpak.Builder --install cc.docking.Docking.json
```

- [ ] Confirm the build completes without network access during the build step.
- [ ] Confirm the app installs into the user Flatpak installation.
- [ ] Run the app:

```bash
flatpak run cc.docking.Docking
```

- [ ] Confirm the dock starts.
- [ ] Confirm the launcher icon resolves.
- [ ] Confirm the About dialog and app metadata look correct.
- [ ] Confirm applets still load as expected inside the Flatpak.
- [ ] Confirm theme assets and translations are present.
- [ ] Confirm logs do not show missing package data.

## 11. Run Flathub Linters

- [ ] Run the manifest linter:

```bash
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest cc.docking.Docking.json
```

- [ ] Fix all actionable manifest linter errors.
- [ ] Document any linter warning that needs a Flathub reviewer exception.
- [ ] Build the repo without installing if needed:

```bash
flatpak run --command=flathub-build org.flatpak.Builder cc.docking.Docking.json
```

- [ ] Run the repo linter:

```bash
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
```

- [ ] Fix all actionable repo linter errors.
- [ ] Re-run both linters until results are clean or only justified warnings remain.

## 12. Commit The Flathub Submission

- [ ] Review the Flathub fork status:

```bash
git status --short
```

- [ ] Confirm only Flathub submission files are staged.
- [ ] Commit the submission:

```bash
git add cc.docking.Docking.json python3-dependencies.json flathub.json
git commit -m "Add cc.docking.Docking"
```

- [ ] If `flathub.json` was not needed, do not add it.
- [ ] Push the branch:

```bash
git push -u origin add-cc.docking.Docking
```

## 13. Open The Flathub Pull Request Manually

- [ ] Open GitHub in a browser.
- [ ] Create a pull request from the fork branch to `flathub/flathub:new-pr`.
- [ ] Do not target `flathub/flathub:master`.
- [ ] Use the PR title:

```text
Add cc.docking.Docking
```

- [ ] In the PR body, state that this is the upstream author submission for Docking.
- [ ] Mention the upstream project URL.
- [ ] Mention the released version used by the manifest.
- [ ] Mention local build/install/linter results.
- [ ] Do not request or attach AI-generated review.

## 14. Handle Flathub Review

- [ ] Watch for reviewer comments.
- [ ] Answer reviewer questions directly.
- [ ] Keep the PR open while addressing review comments.
- [ ] Do not close and recreate the PR for normal review changes.
- [ ] If reviewers request app ID, permission, metadata, or dependency changes, update the same branch.
- [ ] When ready, comment:

```text
bot, build
```

- [ ] Review the Flathub test build logs.
- [ ] Fix any test build failures.
- [ ] Repeat until reviewers approve the submission.

## 15. After Approval And Merge

- [ ] Wait for Flathub to create the new application repository.
- [ ] Accept the Flathub repository invitation within one week.
- [ ] Make sure GitHub two-factor authentication is enabled.
- [ ] Confirm the official Flathub build completes.
- [ ] Confirm Docking appears on the Flathub website.
- [ ] Confirm screenshots and metadata render correctly on the Flathub app page.
- [ ] Install from Flathub:

```bash
flatpak install flathub cc.docking.Docking
flatpak run cc.docking.Docking
```

- [ ] Add Flathub installation instructions to Docking docs after publication.
- [ ] Add a Flathub badge/link to the website after publication.

## 16. Ongoing Maintenance

- [ ] For each Docking release, update the Flathub app repository manifest to the new tag.
- [ ] Update `python3-dependencies.json` when Python dependencies change.
- [ ] Update metainfo release entries when releasing new versions.
- [ ] Submit future updates as PRs to the dedicated Flathub app repository, not to `flathub/flathub:new-pr`.
- [ ] Consider using External Data Checker later if it fits the release workflow.

## Appendix: Flatpak Host Icon Investigation

### Debug Commands

**Run Docking with debug logging:**

```bash
DOCKING_LOG_LEVEL=DEBUG flatpak run cc.docking.Docking 2>&1
```

**Probe icon resolution for specific desktop IDs inside the Flatpak sandbox:**

```bash
flatpak run --command=python3 cc.docking.Docking -c "
from docking.platform.launcher import Launcher
l = Launcher()
for did in ['firefox-stable.desktop', 'org.gnome.Calculator.desktop',
            'caja.desktop', 'terminator.desktop']:
    info = l.resolve(did)
    pix = l.load_desktop_icon(info, 48) if info else None
    print(f'{did}: icon_name={info.icon_name if info else None} '
          f'exec={info.exec_line if info else None} pix={bool(pix)}')
"
```

**Check what icon files exist in host themes:**

```bash
flatpak run --command=sh cc.docking.Docking -c \
  "find /run/host/share/icons -name 'firefox*' 2>/dev/null"
```

**Check GTK icon theme resolution directly:**

```bash
flatpak run --command=python3 cc.docking.Docking -c "
import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk
t = Gtk.IconTheme.get_default()
for name in ['firefox','system-file-manager','terminator']:
    info = t.lookup_icon(name, 48, 0)
    print(name, info.get_filename() if info else None)
"
```

**Check host icon theme setting:**

```bash
flatpak run --command=sh cc.docking.Docking -c \
  "flatpak-spawn --host gsettings get org.gnome.desktop.interface icon-theme"
```

**Check /run/host filesystem layout:**

```bash
flatpak run --command=sh cc.docking.Docking -c "ls /run/host/"
```

**Verify installed Flatpak commit matches source:**

```bash
flatpak info cc.docking.Docking | grep Subject
git rev-parse --short HEAD  # compare commit hashes
```

**Reinstall after rebuild (the full cycle):**

```bash
rm -rf .flatpak-builder/rofiles build-flatpak
./packaging/flatpak/build.sh
flatpak install --user -y --reinstall artifacts/cc.docking.Docking.flatpak
flatpak kill cc.docking.Docking || true
flatpak run cc.docking.Docking
```

### Icon Resolution Flow

The icon loading pipeline lives in `docking/platform/launcher.py`. When
an app item (pinned, running, or drag/dropped) needs an icon:

1. `Launcher.resolve(desktop_id)` — parses the host .desktop file into
   `DesktopInfo` with fields: `name`, `icon_name`, `wm_class`, `exec_line`.

2. `Launcher.load_desktop_icon(info, size)` — loads the pixbuf using a
   cascade of candidates:
   a. The `Icon=` value from the desktop entry.
   b. The executable basename from `Exec=` (e.g. `firefox` from
      `/opt/firefox/firefox %u`).
   c. Falls back to `application-x-executable` (generic icon).

3. Each candidate goes through `_try_load_icon_without_fallback`:
   a. **Absolute path icons** (`/opt/firefox/.../icon.png`) — maps
      to `/run/host/...` and loads with `GdkPixbuf.new_from_file_at_scale`.
   b. **GTK theme lookup** — uses `Gtk.IconTheme.lookup_icon()` +
      `icon_info.load_icon()` (NOT `theme.load_icon()` because that
      emits a GTK warning when the icon is missing).
   c. **Host named icon file scan** — walks `/run/host/share/icons/**`
      looking for `<name>.(png|svg|xpm)` at various sizes. This is
      the last resort inside the Flatpak sandbox.

4. Callers that invoke the launcher:
   - `docking/ui/dnd.py:698` — `_item_from_uri()` for external drag/drop.
   - `docking/platform/model.py:297` — `_build_dock_item()` for pinned
     and running app items.

### Problems Solved

#### 1. Host icon theme not applied (commit `3bc4268d`)

GTK inside the Flatpak defaults to its own icon theme (Adwaita).
Host `.desktop` files specify icon names that exist in the host's
actual icon theme (e.g. `menta`) but not in the Flatpak's default.

**Fix:** `_detect_host_icon_theme()` runs `flatpak-spawn --host gsettings
get org.gnome.desktop.interface icon-theme` to discover the host theme
(`menta`), then creates a fresh `Gtk.IconTheme()` with
`set_custom_theme(host_theme)`. This makes GTK resolve icons through
the host's theme hierarchy (`menta` → `mate` → `hicolor`).

#### 2. GTK "Could not load a pixbuf from icon theme" warnings (commits `624396c0`, `3106c250`)

`Gtk.IconTheme.load_icon()` emits a `g_warning` when the icon is not
found, before throwing `GLib.Error`. Our `except GLib.Error` handler
could not suppress this noise.

**Fix:** Replaced all `theme.load_icon(name, ...)` calls with
`theme.lookup_icon(name, ...)` + `icon_info.load_icon()`. The
`lookup_icon()` method returns `None` silently when an icon is
missing — no warning.

Affected files:
- `docking/platform/launcher.py` — `_try_load_icon_without_fallback`,
  `_try_load_fallback_icon`
- `docking/applets/base.py` — `load_theme_icon()`

#### 3. Firefox /opt icons not reachable (commit `44c7e5c3`)

Firefox's desktop entry has `Icon=/opt/firefox/.../default128.png` but
`/run/host/opt` is NOT mounted in the Flatpak sandbox. The host `find`
output showed:
```
/run/host contains: bin, etc, fonts, lib, lib64, sbin, share, usr
```
Conspicuously absent: `opt`, `home`.

The absolute-path candidate fails (file doesn't exist), so the loader
falls back to the exec basename (`firefox`). The GTK theme lookup also
fails because `menta`/`mate`/`hicolor` don't ship a `firefox` icon.

Finally `_host_named_icon_file_candidates` scans host icon theme
directories — but only at the **exact** requested size. At dock icon
size 72, `firefox.png` was not found because it only existed at sizes
16, 22, 24, 32, 48, 256 in the ContrastHigh theme.

**Fix:** `_host_named_icon_file_candidates` now tries common fallback
sizes (256, 128, 96, 64, 48, 32, 24, 22, 16) when the exact size
isn't found. The loaded pixbuf is then scaled to the requested size
via `new_from_file_at_scale()`.

#### 4. Accessibility theme icons preferred (commit `07c048fa`)

After the size fix, `firefox` icons were found — but in the
`ContrastHigh` theme first (alphabetically first among themes that
have the icon). These are high-contrast accessibility variants that
look wrong on a normal Menta desktop.

Also, fallback sizes were tried in ascending order (16 → 256), so the
smallest available icon was loaded and then scaled UP, resulting in
blurry low-resolution icons.

**Fix:** Two changes:
- `_FALLBACK_ICON_SIZES` is now in **descending** order (256 → 16) so
  the highest-resolution available icon is used.
- Theme directories matching `contrast` (case-insensitive) are
  deprioritized to the end of the scan order. The host's actual theme
  (`menta`) is tried first, then all normal themes, then accessibility
  themes last.

### Key Source Files

| File | Role |
|------|------|
| `docking/platform/launcher.py` | Desktop entry resolution, icon loading, launching |
| `docking/platform/model.py` | Dock item construction, pinned/running item management |
| `docking/ui/dnd.py` | Drag-and-drop handler, external URI to DockItem conversion |
| `docking/applets/base.py` | Applet icon loading (`load_theme_icon`) |
| `packaging/flatpak/cc.docking.Docking.json` | Flatpak manifest with sandbox permissions |
| `packaging/flatpak/build.sh` | Build script for local Flatpak bundle |

### Sandbox Filesystem Notes

Inside the Flatpak, the host filesystem is partially exposed at `/run/host`:

- `/run/host/usr/share/applications/` — host .desktop files
- `/run/host/usr/share/icons/` — host icon themes
- `/run/host/share/icons/` — additional host icon themes (e.g. MATE)
- `/run/host/opt/` — **NOT available** (host `/opt` is not bind-mounted)

This means any desktop entry with an absolute `Icon=` path under
`/opt`, `/home`, or other non-standard prefixes cannot be loaded
directly. The exec-basename fallback (`_normalized_exec_basename`) and
host theme scans are the only recovery paths for those apps.
