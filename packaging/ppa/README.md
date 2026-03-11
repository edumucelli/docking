# PPA Packaging

Launchpad PPA uploads for Docking reuse the Debian metadata under
`packaging/deb/debian/`. This directory contains only the workflow scripts for
building and uploading signed source packages.

## Prerequisites

- A Launchpad account with a PPA already created
- A GPG key attached to that Launchpad account
- Ubuntu packaging tools:

```bash
sudo apt install devscripts debhelper dh-python dput-ng
```

## Build a source package

```bash
./packaging/ppa/build.sh noble
./packaging/ppa/build.sh jammy 2
```

Version format:

```text
<project-version>-<debian-revision>~ppa<ppa-revision>~<series>1
```

Example:

```text
0.1.40-1~ppa1~noble1
```

Artifacts are copied to:

```text
artifacts/ppa/
```

## Upload to Launchpad

```bash
./packaging/ppa/upload.sh <launchpad-id>/<ppa-name>
```

Example:

```bash
./packaging/ppa/upload.sh edumucelli/docking
```

Or upload a specific `.changes` file:

```bash
./packaging/ppa/upload.sh edumucelli/docking \
  artifacts/ppa/docking_0.1.40-1~ppa1~noble1_source.changes
```
