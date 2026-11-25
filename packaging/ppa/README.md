# Publish Docking to Launchpad

The [Docking PPA](https://launchpad.net/~docking/+archive/ubuntu/docking)
targets Ubuntu 26.04 LTS (`resolute`) on amd64 for its first release. The source
package uses the Git tag matching `pyproject.toml` and the current Debian
metadata in `packaging/deb/debian/`. The 26.04 build uses Ubuntu's PyWayland
package and generates Docking's Wayland protocol bindings from the system XMLs.

## Prepare the source package

Install the tools on a Debian or Ubuntu build machine.

```bash
sudo apt install curl git dpkg-dev debhelper dh-python pybuild-plugin-pyproject gnupg dput-ng
git fetch --tags
./packaging/ppa/build.sh --unsigned resolute
```

The files under `artifacts/ppa/unsigned/` include the `.dsc`, the original source
tarball, the Debian packaging tarball, the `.buildinfo`, and the `.changes`
manifest. The source version for Docking 2.35.0 and PPA revision 1 is
`2.35.0-1~ppa1~resolute1`. Use `dpkg-source -x` on the `.dsc` to inspect the
source or build it in a clean Ubuntu 26.04 environment.

For local checks targeting Ubuntu 22.04 (`jammy`) or Ubuntu 24.04 (`noble`),
pass that series in place of `resolute`. Source preparation fetches pinned
PyWayland code and Wayland protocol XMLs for these series. Launchpad builds
them from the source package with the target Ubuntu release's libraries.

## Sign and upload

Register the uploader's public OpenPGP key with Launchpad.
[Import an OpenPGP key](https://ubuntu.com/docs/launchpad/user/how-to/import-openpgp-key/)

Set `DEBEMAIL` to the uploader's Launchpad email and `DEB_SIGN_KEYID` to the
key's full fingerprint. Build again to write the signed files directly to
`artifacts/ppa/`, then upload the specific signed manifest. Adjust the artifact
version for the release you are uploading.

```bash
DEBEMAIL=hello@docking.cc DEB_SIGN_KEYID=YOUR_OPENPGP_FINGERPRINT ./packaging/ppa/build.sh resolute
changes=artifacts/ppa/docking_2.35.0-1~ppa1~resolute1_source.changes
gpg --verify "$changes"
./packaging/ppa/upload.sh docking/docking "$changes"
```

For a packaging fix after Launchpad accepts an upload, increase the PPA
revision with `./packaging/ppa/build.sh resolute 2`.
[Launchpad upload guide](https://ubuntu.com/docs/launchpad/user/how-to/packaging/ppa-package-upload/)

## Release CI

The `publish-launchpad` job uploads source packages for Jammy, Noble, and
Resolute from each published GitHub release tag. Set up the `launchpad` GitHub
Actions environment with `LAUNCHPAD_SIGNING_KEY` containing the ASCII-armored
private CI key and `LAUNCHPAD_SIGNING_PASSPHRASE` for a passphrase-protected key.
Register the public CI key on the `docking` Launchpad account and set the
repository variable `LAUNCHPAD_ENABLED` to `true` to enable publication.

If an upload fails, rerun the failed job. It skips versions Launchpad already
accepted. Launchpad builds and publishes the binaries after the uploads.

## Verify installation

After Launchpad publishes the amd64 build, use a fresh Ubuntu 26.04 VM with
the `universe` component enabled. The same Docking release from Cloudsmith
sorts above the PPA version, so use a VM with only the Ubuntu repositories
and the PPA enabled.

```bash
sudo add-apt-repository ppa:docking/docking
sudo apt update
apt-cache policy docking
sudo apt install docking
```

Check that the APT candidate comes from the PPA, then launch Docking on
X11 and Wayland. Use [the desktop smoke checks](../deb/runtime-smoke.sh) to
verify X11 and headless Sway.
