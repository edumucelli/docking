# KDE output protocol fixtures

Derived from KDE's [plasma-wayland-protocols](https://invent.kde.org/libraries/plasma-wayland-protocols/-/tree/master/src/protocols)
`kde-output-device-v2.xml` and `kde-output-management-v2.xml`.

The lab binds output-device version 2 and output-management/configuration version 1.
These fixtures retain their wire signatures and opcode order, remove descriptions
and later-version messages, and exclude the newer output-registry/mode-list APIs.
Copyright notices and the MIT-CMU license are preserved.

`kwin_outputs.py` generates PyWayland bindings into the disposable run directory.
It reads actual output names, modes, positions and floating-point scales; scale
changes must receive the compositor's `applied` event. These are lab fixtures,
not Docking's production protocol bindings.
