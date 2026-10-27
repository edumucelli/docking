# Windows visual checks

Run from the repository root as `python -m tools.windows.visual.<module>`.
Direct script paths such as `python tools/windows/visual/lab.py` also work.
Native checks require an interactive Windows desktop; `lab` and `vm_*` tools
control the VM from its host.

- `lab`: submit durable jobs to the interactive Windows lab agent.
- `vm_input`, `vm_pointer`, `vm_capture`, `vm_audio`: independent input and recording.
- `desktop_driver`: shared native desktop observations and input.
- `smoke`: run the complete dock, including an isolated installed wheel.
- `*_smoke`: feature checks through the running dock.
- `*_fixture`, `*_capture`, `*_probe`, `*_oracle`: owned providers and independent observations.
- `camera_source.cpp`: generated-frame camera provider built by `camera_fixture`.

```sh
python -m tools.windows.visual.lab --share /path/to/shared status
python -m tools.windows.visual.smoke --output artifacts/windows/smoke
```

The [Windows workflow](../../../.github/workflows/windows.yml) runs the installed
wheel smoke on a native Windows runner and uploads screenshots and logs as
Actions artifacts. Keep generated captures under `artifacts/`.
See [Windows support](../../../docs/WINDOWS.md) for lab setup, feature commands,
evidence and acceptance limits.
