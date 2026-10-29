# Windows visual checks

Run from the repository root as `python -m tools.windows.visual.<module>`.
Direct script paths such as `python tools/windows/visual/lab.py` also work.
Native checks require an interactive Windows desktop; `lab` and `vm_*` tools
control the VM from its host.

- `lab`: submit durable jobs to the interactive Windows lab agent.
- `vm_input`, `vm_pointer`, `vm_capture`, `vm_audio`: independent input and recording.
- `desktop_driver`: shared native desktop observations and input.
- `smoke`: run the complete dock, including an isolated wheel or native bundled launcher.
- `bundle_smoke`: verify and extract a portable ZIP, then check the isolated runtime.
- `installer_smoke`: install, upgrade, check startup and the installed desktop,
  then uninstall while preserving preferences and unknown files.
- `*_smoke`: feature checks through the running dock.
- `*_fixture`, `*_capture`, `*_probe`, `*_oracle`: owned providers and independent observations.
- `camera_source.cpp`: generated-frame camera provider built by `camera_fixture`.

```sh
python -m tools.windows.visual.lab --share /path/to/shared status
python -m tools.windows.visual.smoke --output artifacts/windows/smoke
python -m tools.windows.visual.runtime_pin_smoke --output artifacts/windows/runtime-pin
python -m tools.windows.visual.runtime_pin_smoke --multiple-windows --uncataloged --output artifacts/windows/multiple-windows
python -m tools.windows.visual.fullscreen_smoke --output artifacts/windows/fullscreen
python -m tools.windows.visual.keyboardlayout_smoke --search-input --output artifacts/windows/search-layouts
python -m tools.windows.visual.runtime_pin_smoke --explorer-drop --output artifacts/windows/explorer-pin
python -m tools.windows.visual.runtime_pin_smoke --explorer-drop --shortcut installed --output artifacts/windows/installed-shortcut
python -m tools.windows.visual.runtime_pin_smoke --explorer-drop --shortcut unlisted --output artifacts/windows/unlisted-shortcut
python -m tools.windows.visual.launch_provenance_smoke --output artifacts/windows/launch-provenance
python -m tools.windows.visual.application_export_smoke --output artifacts/windows/application-export
python -m tools.windows.visual.application_export_smoke --classic --output artifacts/windows/classic-export
python -m tools.windows.visual.application_export_smoke --cancel-export --output artifacts/windows/export-cancel
python -m tools.windows.visual.application_export_smoke --close-during-export --output artifacts/windows/export-close
```

The [Windows workflow](../../../.github/workflows/windows.yml) runs the installed
wheel smoke on a native Windows runner and uploads screenshots and logs as
Actions artifacts. Both the wheel and standalone desktop checks require the
actual dock window to use per-monitor-v2 DPI awareness and record its DPI.
The workflow runs `runtime_pin_smoke --multiple-windows --uncataloged` to check native
grouping, repeated cycling, individual window close and restored pin launch in a
fresh process. The fixture uses a unique executable name without installed
shortcuts and verifies that no catalog alias supplies its runtime identity.
The fullscreen check verifies native F11 transitions and starting
the dock during an existing fullscreen application, with pixel coverage,
foreground retention, restored native stacking, direct Alt+F4 closure and normal
cleanup of both fullscreen and restoration observers.
The Search input mode checks US/French/Dvorak physical typing and French dead
keys, live punctuation bindings, unavailable layouts, temporary conflicts and
shutdown during retry. It restores the original input preferences and loaded
layouts, and verifies that all tested shortcuts can be registered after exit.
It also builds the standalone ZIP and exercises its extracted
runtime and launcher. The installer check verifies per-user setup, installed-file
integrity, in-use rejection, upgrades, startup and removal; see
[Windows packaging](../../../packaging/windows/README.md).
Keep generated captures under `artifacts/`.
See [Windows support](../../../docs/WINDOWS.md) for lab setup, feature commands,
evidence and acceptance limits.
