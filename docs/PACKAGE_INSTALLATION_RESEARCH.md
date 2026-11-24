# Docking package installation research

This brief records the decisions and checks before implementation. References
to the previous installer describe the checkout used for the investigation.
[Research baseline](https://github.com/edumucelli/docking/tree/8727a1ee3d3934ebc1ca6d2e6f286e64d20b0c19)

## Website and installation guide

Route the main **Install Docking** buttons to the website's installation
section, where visitors can choose their platform. Keep a clearly labeled
**GitHub Releases** link alongside the standalone downloads. The current hero
and lower **Download** buttons both send visitors directly to GitHub.
[Website hero](../website/index.html#L269), [lower website call to action](../website/index.html#L1646)

Lead with **Install with APT** for supported Debian and Ubuntu systems. Place
**Download standalone .deb** beside it with a link back to APT setup. Adding the
repository remains an explicit action. The standalone package installs Docking and can
resolve dependencies through APT when installed as a local file. Its maintainer
scripts configure desktop integration, and the package leaves repository enrollment to
the user. State that APT setup is required once on each machine, including machines
with Docking already installed from a `.deb`. [Website install
section](../website/index.html#L1368), [installation
guide](INSTALLATION.md#debian-and-ubuntu-apt), [Debian post-install
script](../packaging/deb/debian/postinst), [Ubuntu local package
guide](https://documentation.ubuntu.com/project/contributors/bug-fix/install-built-packages/)

Link the APT action to the first paragraph inside the expandable setup panel.
The HTML fragment navigation algorithm reveals a closed ancestor panel when a
descendant is targeted. Give the selected route a clear label on mobile and show
the supported releases before the command. The current website architecture
selector can keep handling the standalone download suffix. After installation,
tell users to open Docking from their application menu. [HTML standard](https://html.spec.whatwg.org/multipage/interaction.html#ancestor-revealing-algorithm), [website architecture selector](../website/app.js), [running Docking](../README.md#running)

The supplied Jammy incident showed why that wording matters. The installed `2.10.0-1`
was the only version in `apt-cache policy docking`, listed under
`/var/lib/dpkg/status`. The user had verified the key, and the Docking keyring and
`.sources` files were still absent. A temporary APT configuration with the signed
Jammy index found `2.34.0-1`, and a dry run selected Docking for upgrade. The GitHub
CLI signing error concerned a separate repository. These version numbers describe the
incident snapshot. APT consults cached package lists from configured sources when
selecting the candidate. [Jammy APT
manual](https://manpages.ubuntu.com/manpages/jammy/man8/apt.8.html), [APT cache
manual](https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html)

The agreed setup is one copyable block that verifies the key, adds the repository, and
installs or upgrades Docking. Follow setup with **Update Docking** and a targeted
command. Normal system-wide APT upgrades also consider Docking once enrolled. Ubuntu's
default unattended upgrades allow official origins, so use “updates through APT” as
the promise. [APT package
commands](https://manpages.debian.org/bookworm/apt/apt-get.8.en.html), [Ubuntu
unattended
upgrades](https://ubuntu.com/server/docs/how-to/software/automatic-updates/)

```sh
sudo apt update && sudo apt install docking
```

`apt install docking` requests an installation or upgrade of Docking and can change
dependencies as needed. `apt-get upgrade` is a system upgrade command. The incident's
`apt-get upgrade docking` also proposed 60 unrelated upgrades. In diagnostics,
`apt-get -s install docking` shows the proposed transaction without applying it. [APT
package commands and
simulation](https://manpages.debian.org/bookworm/apt/apt-get.8.en.html)

An isolated local resolver check reproduced the difference with real APT, a temporary
status database and cache, and a synthetic file repository. With no configured source,
the installed copy was the only candidate. With the source configured, `apt -s install
docking` chose Docking and `apt-get -s upgrade docking` also chose an unrelated
update. An empty status database selected a fresh install. This exercised candidate
selection and left the host package state untouched. Signed network retrieval and
desktop startup are covered by the release checks. [APT simulation
options](https://manpages.debian.org/bookworm/apt/apt-get.8.en.html), [Docking's
release verifier](../packaging/cloudsmith/verify-apt.sh)

If APT reports Docking is already the newest version, ask for `apt-cache policy
docking` and the Docking lines from `sudo apt update`. A version table containing only
`/var/lib/dpkg/status` has no advertised repository version of Docking in the current
cache. Check `/etc/apt/sources.list.d/docking.sources`, confirm the supported base and
architecture, and look for a successful Docking metadata refresh. A `dl.cloudsmith.io`
row shows cached Cloudsmith metadata, so check for refresh errors even if a row is
present. If the feed lists a newer version and `Candidate` is still the installed
version, inspect APT preferences. If the candidate is newer and the transaction is
held back, inspect `apt-mark showhold` and dependency diagnostics. [APT cache
manual](https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html), [APT
preferences
manual](https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html), [APT hold
manual](https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html)

## Signed APT enrollment

Keep the current `docking.sources` deb822 stanza and scoped `Signed-By` trust. APT
authenticates signed repository metadata and follows its hashes through the package
index to the `.deb`. `Signed-By` limits the keys that can authenticate that
repository. A locally managed armored `.asc` file belongs in `/etc/apt/keyrings`, with
read access for `_apt`. `/usr/share/keyrings` is the conventional path for a keyring
owned by a package. Jammy's APT 2.4 also supports embedding an armored public key in a
deb822 stanza. Embedding removes one on-disk file and ties key updates to the source
file. The existing external key file makes an operator-managed key replacement
simpler. [Jammy keyring
manual](https://manpages.ubuntu.com/manpages/jammy/man8/apt-key.8.html), [APT source
syntax](https://manpages.debian.org/bookworm/apt/sources.list.5.en.html), [APT archive
authentication](https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html)

The existing allowlist covers Ubuntu `jammy`, `noble`, `resolute` and Debian
`bookworm`, `trixie`. Use the distribution's `ID` and `VERSION_CODENAME`, or
`UBUNTU_CODENAME` for derivatives that supply an Ubuntu base. Linux Mint documents
Jammy for 21.x and Noble for 22.x. Retain the `/etc/debian_version` fallback for
Debian images missing a codename. The `os-release` specification defines `ID_LIKE` as
a family relationship, which alone provides no archive suite. Reject unknown bases and
distinguish base detection from the distributions exercised in CI. Use `dpkg
--print-architecture` to reject unsupported native architectures before privileged
repository writes. `Architectures: amd64 arm64` in the stanza controls index
acquisition. The current installer has no native-architecture preflight. [Docking
source selector at the research baseline](https://github.com/edumucelli/docking/blob/8727a1ee3d3934ebc1ca6d2e6f286e64d20b0c19/packaging/deb/apt-source.sh), [published APT verification
matrix](../.github/workflows/verify-apt.yml), [Linux Mint release
bases](https://linuxmint.com/download_all.php), [os-release
specification](https://freedesktop.org/software/systemd/man/latest/os-release.html),
[dpkg architecture query](https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html),
[APT source options](https://manpages.debian.org/bookworm/apt/sources.list.5.en.html)

### One copy-and-paste block

A single visible block can make enrollment complete in one action while explaining
that it adds Docking's repository. Use a subshell for cleanup and error handling.
Check the base and native architecture first. Install any missing prerequisites from
configured sources, create a private temporary directory with `mktemp -d`, and
download the key into it as the invoking user. Reuse the suite rules in the
existing source selector and exercise the published commands in the existing
documentation tests. The documented APT and RPM commands
currently share the fixed path `/tmp/docking-cloudsmith.asc`. The APT block copies
that file before checking base support. A private temporary directory also keeps the
verified bytes together with the installation step. [Current APT and RPM
instructions](INSTALLATION.md#debian-and-ubuntu-apt), [Docking's website
setup](../website/index.html#L1394), [APT source selector at the research baseline](https://github.com/edumucelli/docking/blob/8727a1ee3d3934ebc1ca6d2e6f286e64d20b0c19/packaging/deb/apt-source.sh), [documented setup tests](../tests/test_deb_apt_source.py)

Use `gpg --batch --show-keys --with-colons` and require exactly the expected
primary-key fingerprint `811B48CD4A69170DD98F4F49185CED80A7947754` for today's
single-key setup. Check GnuPG's exit status and read the `fpr` record associated with
each `pub` record. The current CI verifier reads the first fingerprint, and a keyring
with an extra primary key would pass that check if the expected key appeared first.
APT accepts all keys in a referenced keyring when the source supplies no fingerprint
filter. Install the verified bytes and validated source only after these checks,
refresh signed metadata, and then invoke APT with its normal install confirmation. A
scripted flow can use the `apt-get` interface and check that the refreshed Docking
feed offers the candidate. [GnuPG machine-readable
output](https://www.gnupg.org/documentation/manuals/gnupg/GPG-Input-and-Output.html),
[GnuPG record format](https://github.com/gpg/gnupg/blob/master/doc/DETAILS), [APT
source options](https://manpages.debian.org/bookworm/apt/sources.list.5.en.html), [APT
update error mode](https://manpages.debian.org/bookworm/apt/apt-get.8.en.html),
[Docking's release verifier](../packaging/cloudsmith/verify-apt.sh)

Reuse the standard source and keyring when existing files match the verified
setup. If a different source already configures the Docking repository, or the
standard filenames contain customized configuration, show the affected paths
and explain how to resolve the conflict. Check both legacy `.list` entries and
deb822 `.sources` entries. Rerunning setup after a download or refresh failure
should reuse the expected files and continue. An intentional key replacement
needs the documented rollover procedure. [APT source files and options](https://manpages.debian.org/bookworm/apt/sources.list.5.en.html)

### Refresh failure and retries

Proceed after an unrelated repository fails to refresh when Docking's signed
source refreshes successfully and APT can still resolve the installation. A
strict refresh can target the Docking `.sources` file using `Dir::Etc::sourcelist`
and `Dir::Etc::sourceparts=-`. `APT::Get::List-Cleanup=0` preserves cached distro
indexes for ordinary dependency resolution. `APT::Update::Error-Mode=any` stops on
transient failures from the selected source. Keep these options on the refresh
invocation. Check the candidate against the newly refreshed index, then let APT
apply existing preferences and holds during installation. If cache state or APT
configuration prevents prerequisite installation or dependency resolution, show
the failing command and explain the repair. If prerequisite tools are already
available, skip their installation and the accompanying global list refresh.
[APT update options](https://manpages.debian.org/bookworm/apt/apt-get.8.en.html), [APT directory configuration](https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html)

A separate local APT fixture confirmed that a strict global refresh failed on an
unavailable third source. The Docking-only refresh succeeded, preserved the
cached distribution index, and a normal install simulation resolved Docking and
its dependency. The fixture used locally trusted synthetic indexes. The live
signature checks below exercised the actual trust chain separately.

### Key rollover

Publish fingerprints through Docking's own documentation. For an initial manual
rollover, publish the replacement key and setup instructions ahead of the switch,
direct users to verify the replacement fingerprint, and document how to update
existing installations. For managed rollover, an explicitly installed
`docking-archive-keyring` package could deliver both trusted public keys while the old
key still signs the repository. That requires migrating sources to reference the
packaged keyring and distributing the transition package through the currently trusted
feed before switching signers. Keep a recovery path for clients that miss the overlap.
Cloudsmith documents that regeneration keeps the old public key, removes the old
private key, and signs subsequent indexes with the new key. Existing package
signatures stay as published. Plan client trust before triggering it. [Debian archive
key maintenance](https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html),
[Debian third-party
repositories](https://wiki.debian.org/DebianRepository/UseThirdParty), [Cloudsmith
signing key
operations](https://docs.cloudsmith.com/supply-chain-security/signing-keys)

## Other enrollment options

Docker documents repository setup for Ubuntu and keeps manual `.deb` downloads
as a distinct choice. Visual Studio Code uses a `.deb` install prompt to offer
repository setup. Docking's explicit setup follows the first model and leaves
the standalone package suitable for managed machines and offline distribution.
[Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/), [Visual Studio Code Linux installation](https://code.visualstudio.com/docs/setup/linux)

| Option | Effect for Docking |
| --- | --- |
| Cloudsmith setup script | Cloudsmith supplies automatic and explicit distro/codename setup modes, plus a configuration endpoint. The automatic command executes fetched code as root. A visible Docking block preserves the project's tested suite checks, full-fingerprint check, and existing deb822 configuration. [Cloudsmith Debian setup](https://docs.cloudsmith.com/formats/debian-repository#setup), [Cloudsmith script signing changes](https://cloudsmith.com/changelog/debian-setup-script-improvements) |
| Separate repository setup `.deb` | A distinct **Set up Docking APT updates** artifact could own a keyring and source under dpkg and make enrollment a first-class choice. Its initial download needs authenticated distribution, and a single generic artifact would need reliable base selection or separate suite-specific builds. It adds packaging and lifecycle work beyond the website and guide fix. [Debian third-party keyring package guidance](https://wiki.debian.org/DebianRepository/UseThirdParty), [Ubuntu third-party repository setup](https://ubuntu.com/server/docs/explanation/software/third-party-repository-usage/) |
| Launchpad PPA | `add-apt-repository ppa:OWNER/NAME` gives Ubuntu users a short enrollment command. Launchpad builds and signs the archive from source uploads for enabled series and architectures. Docking has source-build and upload helpers, while current publication uploads the release binaries and verifies them against Cloudsmith. A public PPA would add a build, signing, validation, and migration path. Debian users would continue to need a supported Debian feed. [PPA reference](https://ubuntu.com/docs/launchpad/user/reference/packaging/ppas/ppa/), [Ubuntu PPA install guide](https://documentation.ubuntu.com/project/contributors/bug-fix/install-built-packages/), [Docking PPA tooling](../packaging/ppa/README.md), [Cloudsmith publication](../packaging/README.md#package-repositories-cloudsmith) |

## RPM documentation

Mirror the repository and standalone choices for RPM. The verified-publication
condition is already met for Docking 2.34.0. The GitHub Actions run shows successful
RPM publication, all eight signed RPM verification jobs, and all ten signed APT
verification jobs. Lead with repository setup for supported Fedora and openSUSE
systems and remove the waiting-for-publication notice from the website, installation
guide, and packaging guide. [2.34.0 verification
run](https://github.com/edumucelli/docking/actions/runs/44849062646), [RPM publication
job](https://github.com/edumucelli/docking/actions/runs/44849062646/job/136689905842),
[website RPM section](../website/index.html#L1450), [RPM installation
guide](INSTALLATION.md#fedora-and-opensuse-rpm), [packaging
guide](../packaging/README.md#package-repositories-cloudsmith)

The documented RPM matrix covers Fedora 44/45 and openSUSE Tumbleweed/Leap 16.0 on
x86_64 and aarch64. Its key and signature checks are specific to RPM, and its setup
currently reuses the APT temporary pathname. Give RPM its own key file or a private
temporary directory if its setup is revised. The release verifier uses targeted `dnf
upgrade docking` and `zypper update docking` from an older repository-installed RPM.
Validate the Zypper command against an existing standalone RPM because its resolver
applies vendor protection. [RPM verification
matrix](../.github/workflows/verify-rpm.yml), [RPM verification
script](../packaging/cloudsmith/verify-rpm.sh), [DNF5 upgrade
command](https://dnf5.readthedocs.io/en/latest/commands/upgrade.8.html), [Zypper
manual](https://manpages.opensuse.org/Tumbleweed/zypper/zypper.8.en.html), [openSUSE
vendor protection](https://opensuse.github.io/libzypp/pg_zypp-solv-vendorchange.html)

## Live repository check

On 24 November 2026, the published keys matched the fingerprints in Docking's
guide. The signed Jammy and Trixie `InRelease` files authenticated the
`Packages.gz` hashes for amd64 and arm64. All four indexes offered `2.34.0-1`.
The RPM key verified the x86_64, aarch64, and noarch `repomd.xml` signatures and
primary index hashes. Both native RPM indexes listed `2.34.0-1`. The check used a
temporary download directory and verified metadata without installing packages.
[Jammy metadata](https://dl.cloudsmith.io/public/docking/docking-apt/deb/ubuntu/dists/jammy/InRelease), [Trixie metadata](https://dl.cloudsmith.io/public/docking/docking-apt/deb/debian/dists/trixie/InRelease), [Cloudsmith RPM package list](https://api.cloudsmith.io/v1/packages/docking/docking-rpm/?page_size=2)

## Verification for implementation

- The current APT source and asset tests passed locally, 43 checks. The source tests
  execute the setup block extracted from the website and installation guide, with
  mocked `sudo`, in sh, Bash, and Zsh. If the block changes, exercise the exact
  published block and add cases for unsupported architecture, a failed download, an
  incorrect fingerprint, an extra primary key, an unrelated source refresh failure,
  a Docking refresh failure, and retries with matching or conflicting sources. Check
  that unsupported or unverified inputs leave the key and source intact. [APT
  source tests](../tests/test_deb_apt_source.py), [APT asset
  tests](../tests/test_apt_verification_assets.py)
- The current `npm --prefix website test` passed. Update the website locale strings
  and run it again after changing the installation copy. Use the browser suite to
  verify the main install links, APT-first placement, standalone links, repository
  jump link, and command block in desktop and mobile layouts across generated
  language pages. [Website development
  guide](DEVELOPMENT.md#website), [website browser tests](../website/test/browser.mjs)
- In clean Jammy, exercise a fresh install, an older standalone `.deb` with no source,
  and a later upgrade using the already configured source. Verify candidate origin
  before installation and exercise Bookworm/Trixie and arm64 through the release
  matrix. The existing post-publication suite checks the candidate, matches the
  downloaded `.deb` bytes against the GitHub artifact, installs it, and upgrades from
  the previous release when available. Include a test that starts with an unconfigured
  standalone installation because the release verifier writes the source before
  installing the old `.deb`. [APT release
  matrix](../.github/workflows/verify-apt.yml), [APT release
  verifier](../packaging/cloudsmith/verify-apt.sh)
- On Fedora and openSUSE, start with the previous standalone `.rpm` and run the
  recommended repository setup and targeted update. Check that the installed
  version advances and the native tools report the Docking repository. Exercise
  the openSUSE vendor decision with the actual release pair before documenting any
  override. [RPM release verifier](../packaging/cloudsmith/verify-rpm.sh)
