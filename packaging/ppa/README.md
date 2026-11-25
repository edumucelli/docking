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

Rerun the failed **Publish Launchpad PPA source packages** job. It skips
versions Launchpad has already accepted. For a packaging fix to an accepted
version, pass a higher PPA revision to `build.sh`, for example
`./packaging/ppa/build.sh resolute 2`. See the
[Launchpad guide for manual source uploads](https://ubuntu.com/docs/launchpad/user/how-to/packaging/ppa-package-upload/).

## Verify installation

After publication, follow the
[PPA installation instructions](../../docs/INSTALLATION.md#launchpad-ppa) on the
target Ubuntu release. Check `apt-cache policy docking` to confirm that the
candidate comes from the PPA. Run the [desktop smoke checks](../deb/runtime-smoke.sh)
to verify X11 and headless Sway.
