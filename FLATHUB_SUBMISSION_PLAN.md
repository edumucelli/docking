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
- [ ] Document why `--filesystem=home:ro` is required: Docking supports pinned
  files/folders and folder stacks, which need read access to user-selected
  local directories. Opening files/folders is delegated back to the host.
- [x] Remove `--device=dri`; Docking renders with GTK/Cairo and does not need
  direct GPU device access in the Flatpak sandbox.
- [ ] Document why `/snap:ro` and `/var/lib/snapd/desktop:ro` are required:
  Snap desktop entries live under `/var/lib/snapd/desktop/applications`, and
  their icons can be absolute `/snap/...` paths that are not visible through
  `/run/host`.
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
- [x] Do not install legacy `org.docking.Docking` Flatpak icons; runtime code
  uses `FLATPAK_ID` for the About dialog logo and system packages keep the
  legacy icon ID separately.

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

## Appendix: Flatpak Host Launcher Notes

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
for did in ['firefox_firefox.desktop', 'awsvpnclient.desktop',
            'mongodb-compass.desktop']:
    info = l.resolve(did)
    pix = l.load_desktop_icon(info, 48) if info else None
    print(f'{did}: resolved={info.desktop_id if info else None} '
          f'icon={info.icon_name if info else None} '
          f'exec={info.exec_line if info else None} pix={bool(pix)}')
"
```

**Check sandbox-visible host launcher locations:**

```bash
flatpak run --command=sh cc.docking.Docking -c \
  "ls /run/host/usr/share/applications /var/lib/snapd/desktop/applications 2>/dev/null"
```

**Reinstall after rebuild:**

```bash
./packaging/flatpak/build.sh
flatpak install --user -y --reinstall artifacts/cc.docking.Docking.flatpak
flatpak kill cc.docking.Docking || true
flatpak run cc.docking.Docking
```

### Resolution Model

Docking now keeps Flatpak launcher/icon handling close to native behavior:

1. Read the best available `.desktop` file from sandbox-visible host locations:
   `/run/host/.../applications`, `~/.local/share/applications`, and Snap's
   `/var/lib/snapd/desktop/applications`.
2. Use the desktop entry's `Icon=` field directly.
3. Load absolute icon paths as files, mapping normal host paths to `/run/host/...`
   when needed. Snap icons under `/snap/...` are loaded directly because the
   manifest exposes `/snap:ro`.
4. Let GTK resolve named theme icons using its normal `Gtk.IconTheme` lookup.
5. As a narrow compatibility path, load literal pixmap filenames such as
   `Icon=acvc-64.png` from host `pixmaps` directories.
6. Fall back to `application-x-executable` when none of the above works.

This intentionally avoids detecting the host icon theme, scanning all host
themes, or ranking accessibility themes. Missing icons should usually be fixed
by exposing the correct launcher/icon location in the Flatpak manifest rather
than by inventing a parallel icon theme resolver.

### Sandbox Filesystem Notes

- `/run/host/usr/share/applications/` — host system `.desktop` files.
- `~/.local/share/applications/` — host user `.desktop` files exposed directly
  by `--filesystem=xdg-data/applications:ro`.
- `/var/lib/snapd/desktop/applications/` — Snap-exported `.desktop` files.
- `/snap/` — Snap application files and absolute Snap icon paths.
*** End of File
