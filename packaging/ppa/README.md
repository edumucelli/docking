# Docking on Launchpad

Release CI uploads signed Docking source packages to `ppa:docking/docking`.
Launchpad builds amd64 and arm64 packages for Ubuntu 22.04 (`jammy`), 24.04
(`noble`), and 26.04 (`resolute`). See the
[PPA package status](https://launchpad.net/~docking/+archive/ubuntu/docking/+packages).

## Build locally

```bash
sudo apt install curl git dpkg-dev debhelper dh-python pybuild-plugin-pyproject gnupg
git fetch --tags
./packaging/ppa/build.sh --unsigned resolute
```

Use `jammy` or `noble` to target those series. Source files are staged under
`artifacts/ppa/unsigned/`. Use `dpkg-source -x` on the `.dsc` to unpack the
source for inspection.

## Retry an upload

Rerun the failed **Publish Launchpad PPA source packages** job in the original
release run. It skips versions Launchpad has already accepted.

If that run is unavailable or the accepted package needs a fix, use a checkout
with the version you intend to publish. Install `dput-ng` and use a signing
key registered on Launchpad. Use the original PPA revision for a missing source
and increase it for a fix to an accepted version. This example replaces
revision 1 for Resolute.

```bash
DEBEMAIL=YOUR_LAUNCHPAD_EMAIL DEB_SIGN_KEYID=YOUR_KEY_FINGERPRINT \
  ./packaging/ppa/build.sh resolute 2
./packaging/ppa/upload.sh docking/docking path/to/printed_source.changes
```

Replace the path with the signed `_source.changes` file printed by the build. See the
[Launchpad guide for manual source uploads](https://ubuntu.com/docs/launchpad/user/how-to/packaging/ppa-package-upload/).

## Verify installation

After publication, follow the
[PPA installation instructions](../../docs/INSTALLATION.md#launchpad-ppa) on the
target Ubuntu release. Run the [desktop smoke checks](../deb/runtime-smoke.sh)
to verify X11 and headless Sway.
