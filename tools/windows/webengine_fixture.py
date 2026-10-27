"""Companion entry point restricted to the generated TLS acceptance server."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    '--no-proxy-server --host-resolver-rules="MAP web.whatsapp.com 127.0.0.1,'
    'MAP web.telegram.org 127.0.0.1"'
)


def main() -> None:
    output = Path(os.environ["DOCKING_BROWSER_FIXTURE_OUTPUT"])
    with (output / "child-stderr.log").open("ab", buffering=0) as errors:
        os.dup2(errors.fileno(), 2)

    from PySide6.QtCore import QPoint
    from PySide6.QtNetwork import QSslCertificate
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

    from docking.applets.webapp.windows_companion.runtime import Runtime
    from docking.applets.webapp.windows_companion.runtime import main as run

    class Requests(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, request) -> None:  # noqa: N802 - Qt virtual method.
            if request.requestUrl().scheme() in {
                "http",
                "https",
            } and request.requestUrl().host() not in {
                "web.whatsapp.com",
                "web.telegram.org",
            }:
                request.block(True)

    class Fixture(Runtime):
        def _profile_builder(self):
            builder = super()._profile_builder()
            builder.setAdditionalTrustedCertificates(
                QSslCertificate.fromData(
                    Path(os.environ["DOCKING_BROWSER_FIXTURE_CERT"]).read_bytes()
                )
            )
            return builder

        def _build(self) -> None:
            super()._build()
            self._requests = Requests(self.profile)
            self.profile.setUrlRequestInterceptor(self._requests)
            self.emit("fixture", pid=os.getpid(), gi_imported="gi" in sys.modules)

        def command(self, packet) -> None:
            if packet.get("op") == "fixture-origin":
                origin = self.view.mapToGlobal(QPoint(0, 0))
                self.emit(
                    "fixture_origin",
                    x=origin.x(),
                    y=origin.y(),
                    scale=self.view.devicePixelRatioF(),
                )
                return
            if packet.get("op") == "fixture-hang":
                self.emit("fixture_hang")
                time.sleep(60)
                return
            if packet.get("op") != "probe":
                super().command(packet)
                return
            identifier = packet["id"]

            def completed(raw):
                result = json.loads(raw) if isinstance(raw, str) and raw else None
                self.emit("probe", id=identifier, result=result)

            self.page.runJavaScript(
                "JSON.stringify(eval(" + json.dumps(packet["source"]) + "))", completed
            )

    run(Fixture)


if __name__ == "__main__":
    main()
