# KDE Connect runtime checks

This disposable Debian 13 image installs the real distribution KDE Connect
package and runs Qt offscreen. It does not require a Plasma desktop or change
the host's packages, configuration, or session bus.

From the repository root:

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
