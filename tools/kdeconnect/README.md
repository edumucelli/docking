# KDE Connect runtime checks

This disposable Debian 13 image installs the real distribution KDE Connect
package and runs Qt offscreen. It does not require a Plasma desktop or change
the host's packages, configuration, or session bus.

See the [KDE Connect applet](../../docs/APPLETS.md#kde-connect) for desktop and
phone setup. A source checkout can run the unit and private-bus tests directly.

```sh
.venv/bin/python -m pytest tests/applets/test_kdeconnect.py tests/integration/test_kdeconnect_bus.py
```

From the repository root, run the container checks with:

```sh
docker build -t docking-kdeconnect-test:debian13 tools/kdeconnect
docker run --rm --network none -v "$PWD:/src:ro" -e PYTHONPATH=/src \
  docking-kdeconnect-test:debian13 python3 tools/kdeconnect/runtime_smoke.py
docker run --rm --init --network none -v "$PWD:/src:ro" -e PYTHONPATH=/src \
  docking-kdeconnect-test:debian13 xvfb-run -a python3 -m pytest -p no:cacheprovider \
  tests/applets/test_kdeconnect.py tests/integration/test_kdeconnect_bus.py
```

The real-daemon check starts a private session bus and fresh KDE Connect profile,
checks D-Bus discovery and the paired-device query, then checks owner loss and
subscription teardown. It was verified with Debian's KDE Connect
`25.04.2-1+deb13u1`. The second command runs paired-phone fixture tests over real
private D-Bus transport, including battery changes, ring/share calls,
reachability, and teardown.

No physical phone is connected by these checks. Attempts to pair two headless
desktop daemons in the container environment did not produce reliable pairing
or end-to-end file delivery. Those operations must still be verified with a real
paired phone; a successful share method call alone does not confirm receipt.

The D-Bus contract follows KDE Connect's upstream
[daemon](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/core/daemon.h),
[device](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/core/device.h),
[battery](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/battery/batteryplugin.h),
[find-my-phone](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/findmyphone/findmyphoneplugin.h),
and [share](https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/share/shareplugin.h)
interfaces. The client supports the older `isTrusted` device property as well
as `isPaired`.
