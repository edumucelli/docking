"""Companion entry point restricted to the generated TLS acceptance server."""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TLSFixture:
    """One loopback server for both isolated service origins and their workers."""

    def __init__(self, output: Path, directory: Path) -> None:
        self.output, self.directory = output, directory
        self.modes = {"whatsapp": "ready", "telegram": "ready"}
        self._server = None
        self._thread = None

    def start(self) -> None:
        from tools.windows.webapp_fixture import _HTML, _WORKER

        certificate, key = (
            self.directory / "fixture-cert.pem",
            self.directory / "fixture-key.pem",
        )
        subprocess.run(
            [
                str(Path(sys.executable).with_name("openssl.exe")),
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
                "-days",
                "1",
                "-subj",
                "/CN=Docking generated TLS fixture",
                "-addext",
                "subjectAltName=DNS:web.whatsapp.com,DNS:web.telegram.org",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.environ["DOCKING_BROWSER_FIXTURE_OUTPUT"] = str(self.output)
        os.environ["DOCKING_BROWSER_FIXTURE_CERT"] = str(certificate)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                host = self.headers.get("Host", "").split(":")[0].casefold()
                if host not in {"web.whatsapp.com", "web.telegram.org"}:
                    self.send_error(404)
                    return
                service = "whatsapp" if host == "web.whatsapp.com" else "telegram"
                if fixture.modes[service] == "login":
                    landmark = (
                        '<canvas aria-label="Generated login placeholder" '
                        'width="150" height="150"></canvas>'
                        '<div id="auth-pages"></div>'
                    )
                elif service == "whatsapp":
                    landmark = '<div id="pane-side">Generated connected state</div>'
                else:
                    landmark = (
                        '<div id="column-left"><div class="chatlist">'
                        "Generated connected state</div></div>"
                    )
                html = _HTML.replace("LANDMARK", landmark).replace(
                    "Native WebView2", "Native Qt WebEngine"
                )
                if service == "telegram":
                    html = html.replace("WhatsApp", "Telegram").replace(
                        "web.whatsapp.com", "web.telegram.org"
                    )
                worker = self.path == "/fixture-worker.js"
                body = (_WORKER if worker else html).encode()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/javascript" if worker else "text/html; charset=utf-8",
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Service-Worker-Allowed", "/")
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 443), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)
        key.unlink()
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(5)


def main() -> None:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        '--no-proxy-server --host-resolver-rules="MAP web.whatsapp.com 127.0.0.1,'
        'MAP web.telegram.org 127.0.0.1"'
    )

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
