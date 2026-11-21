# Development tools

Run these tools from the repository root. Shared tools stay directly in `tools/`;
platform-specific tools live under the corresponding platform directory.

| Location | Purpose |
| --- | --- |
| [`../tests/visual/`](../tests/visual/README.md) | All renderer and native desktop visual suites, adapters, probes, fixtures and baselines. |
| `linux/` | Linux integration utilities and the pinned software-GPU VM controller. |
| `bsd/` | BSD VM/package setup, checksum pins and installed acceptance orchestration. |
| [Windows](windows/README.md) | Native CI, packaging, release validation, runtime setup and desktop lab. |
| [KDE Connect](kdeconnect/README.md) | Isolated daemon and private D-Bus tests. |
| `tools/` root | Shared translation, packaging, versioning and asset-generation tools. |

See [BSD package and desktop checks](../docs/DEVELOPMENT.md#bsd-package-and-desktop-checks)
for VM setup and the local X11 control.
