# Cloudsmith repository maintenance

Stable releases publish their original amd64 and arm64 `.deb` assets to the public
[`docking/docking-apt`](https://cloudsmith.io/~docking/repos/docking-apt/setup/)
repository, and their x86_64 and aarch64 `.rpm` assets to the public
[`docking/docking-rpm`](https://cloudsmith.io/~docking/repos/docking-rpm/setup/)
repository. GitHub Actions authenticates as `github-docking` through OIDC; no API
key is needed. Uploads use `any-distro/any-version`; Cloudsmith includes these
packages in each distribution’s APT and RPM index respectively.

Both repositories are free of charge under the Cloudsmith
[OSS hosting policy](https://docs.cloudsmith.com/resources/open-source-hosting-policy),
which covers unlimited open-source repositories, requires the attribution already
present in the main README, and tracks open-source usage separately from any paid
allowance.

## Cloudsmith setup

- Keep the repository public, with **Broadcast** enabled. For free OSS hosting,
  retain the Cloudsmith attribution in the main README. Disable paid overage in
  workspace usage limits if required.
- Under **Repository → Settings → Access control**, grant service
  `github-docking` **Write** access to `docking-apt` and `docking-rpm`. Access
  control is per repository, so a format added later needs its own grant; the
  OIDC provider and claims below are workspace-wide and already cover it.
- Add an OpenID provider with issuer `https://token.actions.githubusercontent.com`,
  allow that service, and require these claims:

  ```json
  {
    "repository": "edumucelli/docking",
    "sub": "repo:edumucelli/docking:environment:cloudsmith"
  }
  ```

  If restricting the audience, use `https://github.com/edumucelli`.

See [Cloudsmith's OIDC setup](https://docs.cloudsmith.com/authentication/setup-cloudsmith-to-authenticate-with-oidc-in-github-actions)
and [OSS hosting policy](https://docs.cloudsmith.com/resources/open-source-hosting-policy).

## GitHub activation

The `cloudsmith` environment must allow only `master`. Set these repository
Actions variables:

| Variable | Value |
| --- | --- |
| `CLOUDSMITH_WORKSPACE` | `docking` |
| `CLOUDSMITH_REPOSITORY` | `docking-apt` |
| `CLOUDSMITH_SERVICE_SLUG` | `github-docking` |
| `CLOUDSMITH_ENABLED` | `false` during setup; `true` after CI passes |

`CLOUDSMITH_RPM_REPOSITORY` optionally overrides the RPM repository and defaults to
`docking-rpm`, so no variable is required for RPM. Publication is not gated
separately: the `Install RPM` matrix already proves the built package on Fedora and
openSUSE before any release is published, so there is nothing to stage and RPM goes
out with the same release as APT.

Keep the enable flag at repository scope: the calling job reads it before entering
the environment. These values and the signing fingerprint are public identifiers.

Before enabling, require the binary installation checks to pass on Ubuntu
22.04/24.04/26.04, Debian 12/13, Fedora 44/45, and openSUSE Tumbleweed/Leap 16.0
for both architectures. They verify runtime imports, X11 startup, and native Wayland startup under
headless Sway, including the live foreign-toplevel protocol. Full desktop
behavior still needs testing on intended compositors.

After activation, a successful CI release publishes automatically. Publication
is followed by signed APT installation and upgrade checks on the same matrix,
including release-asset checksum verification and runtime startup. The first
release for an architecture has no predecessor and reports a fresh-install-only check. The initial
release must include both versioned `.deb` assets; v2.13.7 has only amd64.

## RPM repository

The RPM pipeline mirrors APT, with one difference that drives the design:
**Cloudsmith GPG-signs RPM packages on upload, which regenerates the checksum.**
The stored file therefore never matches the built artifact, so two APT checks
cannot be reused:

- The publisher identifies an already-published RPM by
  `name-version-release.arch` instead of comparing `checksum_sha256`. Re-cutting
  the same version with different contents is not detectable, so it must be
  avoided by bumping `Release:` in `packaging/rpm/docking.spec` instead.
- `verify-rpm.sh` compares the sorted payload file digests
  (`rpm -q --queryformat '[%{FILENAMES}\t%{FILEDIGESTS}\n]'`) of the installed
  package against the GitHub release asset. Signing rewrites the signature header
  but not the payload, so this survives re-signing where a checksum cannot.

| Setting | Value |
| --- | --- |
| Repository | `docking/docking-rpm`, public, Broadcast enabled |
| Index | `https://dl.cloudsmith.io/public/docking/docking-rpm/rpm/any-distro/any-version/$basearch` |
| Public key | `https://dl.cloudsmith.io/public/docking/docking-rpm/gpg.key` |
| Signing fingerprint | `04C7240DAD480161C0DFC791859E104126138494` |

Signing keys are generated per repository, so this fingerprint is **not** the APT
key `811B48CD4A69170DD98F4F49185CED80A7947754`. Re-read it from the repository's
**Set Me Up → Red Hat** page or `cloudsmith repos gpg get docking/docking-rpm -F json`
if the key is ever regenerated, and update `packaging/cloudsmith/rpm-repo.sh` and
the README together. The key exists as soon as the repository does; no manual
signing setup is required.

Cloudsmith keeps the `$basearch` and `noarch` indexes separate, so both entries are
written by `rpm-repo.sh`. Docking is an architecture-specific package, so only the
first index carries it, but the noarch entry keeps future noarch content working.
dnf reads the file from `/etc/yum.repos.d`, while openSUSE ships only
`/etc/zypp/repos.d` and zypper ignores the `repo_gpgcheck` key that dnf uses. `verify-rpm.sh` installs with weak dependencies disabled so
a green run proves the spec's hard `Requires:` are complete.

The RPM is published as a single `any-distro/any-version` artifact for Fedora and
openSUSE alike, so the `Requires:` in the spec use RPM boolean dependencies to
accept either distribution's package names — for example
`(gtk3 or typelib-1_0-Gtk-3_0)`. Removing them breaks one distribution silently
until the verification matrix runs.

`packaging/rpm/build-wayland-vendors.sh` builds PyWayland fallbacks for each target
Python minor and CI passes them through `DOCKING_EXTRA_PYWAYLAND`. This is required:
the RPM is built on Ubuntu runners, whose Python never matches Fedora or openSUSE,
and both distributions package PyWayland older than the 0.4.18 protocol modules the
native Wayland backend imports.

The first RPM publication must use a stable release built with these packaging
fixes. Older release assets lack the corrected dependencies and native fallbacks;
retrying their publication cannot repair them. After the new release is published,
require its signed verification matrix to pass before announcing repository
installation. Direct GitHub RPM downloads remain available during this rollout.

## Retry a publication

Open **Actions → Publish Cloudsmith → Run workflow**, choose `master`, and enter
the exact published stable tag, such as `v2.13.8`.

The publisher validates both packages before uploading, skips packages already
present, and waits for both architectures to be indexed and downloadable. For a
partial upload, retry the same tag and original assets. Conflicting bytes require a
new version, a Debian revision, or an RPM `Release:` bump; never replace an
existing package.

Offline validation of a downloaded release pair:

```bash
python3 packaging/cloudsmith/publish.py --directory incoming --version 2.13.8 --validate-only
python3 packaging/cloudsmith/publish.py --format rpm --directory incoming --version 2.13.8 --validate-only
.venv/bin/python -m pytest tests/test_cloudsmith_publish.py -q
```

## Verify and announce

Use the repository's **Set Me Up / Debian** page to confirm these public settings
before publishing installation instructions:

| Setting | Value |
| --- | --- |
| APT URI | `https://dl.cloudsmith.io/public/docking/docking-apt/deb/<distro>` |
| Suite / component | OS codename / `main` |
| Public key | `https://dl.cloudsmith.io/public/docking/docking-apt/gpg.key` |
| Signing fingerprint | `811B48CD4A69170DD98F4F49185CED80A7947754` |

Clients select the distribution and codename from `/etc/os-release`, using
`UBUNTU_CODENAME` for Ubuntu derivatives and `/etc/debian_version` when Debian
omits its codename. Setup validates the supported base before writing a source. Examples include
`ubuntu` / `jammy` or `debian` / `trixie`. See the
[user installation instructions](../../README.md#debian-and-ubuntu-apt).

Re-run **Actions → Verify published APT packages** or **Verify published RPM
packages** on `master` with the latest stable tag to check a live repository
without uploading packages. Neither needs write access, so they are safe to run
against a repository that has not been enabled for publishing yet.

Keep the main README and website commands current when changing repository
settings. Retain direct release downloads as a fallback, and announce only
distribution/architecture combinations whose verification checks passed.

Retain previous packages for both architectures and monitor repository usage.
Signing-key changes require updating client instructions; the workflow never
rotates keys or deletes packages.
