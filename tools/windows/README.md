# Windows development tools

Run these tools from the repository root. Native checks require Windows.

- `ci`: native runtime preflight, Windows/core tests and GTK behavior checks.
- `webview2_setup`: install the verified Microsoft WebView2 loader.
- `bundle`: assemble a relocatable application from a wheel and its native runtime;
  see [Windows packaging](../../packaging/windows/README.md).
- `installer`: build per-user setup from a verified portable ZIP using NSIS.
- `release`: stage versioned Windows packages from accepted CI artifacts.
- [`visual/`](visual/README.md): desktop acceptance checks, VM controls, fixtures,
  captures and independent observations, matching the Linux and BSD tool layout.

For setup, commands, evidence and acceptance limits, see
[Windows support](../../docs/WINDOWS.md).

```sh
python -m tools.windows.visual.lab --help
python -m tools.windows.visual.lab --share /path/to/shared status
python -m tools.windows.ci --stage preflight
python -m tools.windows.ci --stage tests
python -m tools.windows.ci --stage gtk
```

The [Windows workflow](../../.github/workflows/windows.yml) runs these checks
with MSYS2 UCRT64, builds the wheel, portable ZIP and per-user installer, then
exercises each installation. Reports, screenshots and packages are uploaded as
Actions artifacts; generated Windows captures do not belong in `images/`.
