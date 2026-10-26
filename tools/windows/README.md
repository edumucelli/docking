# Windows development tools

Run these tools from the repository root as `python -m tools.windows.<module>`.
Direct script paths such as `python tools/windows/lab.py` also work. The native
checks require Windows; `lab` and the `vm_*` tools control the VM from its host.

- `lab`: submit durable jobs to the interactive Windows lab agent.
- `vm_input`, `vm_pointer`, `vm_capture`, `vm_audio`: independent VM input and recording.
- `desktop_driver`: shared native desktop observations and input.
- `*_smoke`: feature checks through the running dock.
- `*_fixture`, `*_capture`, `*_probe`, `*_oracle`: owned providers and independent observations used by those checks.
- `camera_source.cpp`: generated-frame camera provider built by `camera_fixture`.

For setup, commands, evidence and acceptance limits, see
[Windows support](../../docs/WINDOWS.md).

```sh
python -m tools.windows.lab --help
python -m tools.windows.lab --share /path/to/shared status
```
