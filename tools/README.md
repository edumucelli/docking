# Development tools

Run these tools from the repository root. Shared tools stay directly in `tools/`;
platform-specific tools live under the corresponding platform directory.

| Location | Purpose |
| --- | --- |
| [`../tests/visual/`](../tests/visual/README.md) | All renderer and native desktop visual suites, adapters, probes, fixtures and baselines. |
| `linux/` | Linux integration utilities and the pinned software-GPU VM controller. |
| [`linux/tours/`](linux/tours/) | Record the Overview, Applets, Preferences, and Search tours as MP4s from a private X11 desktop. Add `--gif` for GIFs alongside the MP4s. |
| `bsd/` | BSD VM/package setup, checksum pins and installed acceptance orchestration. |
| [Windows](windows/README.md) | Native CI, packaging, release validation, runtime setup and desktop lab. |
| [KDE Connect](kdeconnect/README.md) | Isolated daemon and private D-Bus tests. |
| `tools/` root | Shared translation, packaging, versioning and asset-generation tools. |

See [BSD package and desktop checks](../docs/DEVELOPMENT.md#bsd-package-and-desktop-checks)
for VM setup and the local X11 control.

## Record tours

Run `python -m tools.linux.tours --tour overview --gif` from the checkout to
write `overview.mp4` and `overview.gif` in `artifacts/tours/`. Use `--wallpaper`
with a desktop image, or select `applets`, `preferences`, or `search` with
`--tour`. The full Applets tour uses a prepared demo profile with `--profile`.
The README's Watch links use the MP4s in `website/tours/`.

The recorder needs Xvfb, xauth, D-Bus, Marco, FFmpeg, and the usual Docking
runtime dependencies. For the Overview, install Caja, the X11 keyboard tools,
VLC with D-Bus support, and `pulseaudio` with `pactl`. The recorder plays two
generated demo tracks on its own virtual audio output. Other application
launchers appear on the Overview shelf when installed.
Install [Gifski 1.32 or later](https://gif.ski/) to use `--gif`.
