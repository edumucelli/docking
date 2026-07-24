"""Tests for the Drag Share applet."""

from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

import pytest

import docking.applets.dragshare.applet as dragshare_applet_mod
from docking.applets.dragshare.applet import DragshareApplet
from docking.applets.dragshare.render import render_icon
from docking.applets.dragshare.state import (
    DragshareStatus,
    UploadError,
    UploadResult,
    file_path_from_uri,
    first_uploadable_file,
    upload_file,
)
from docking.core.config import Config


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class _ImmediateThread:
    def __init__(self, *, target, args=(), daemon=False) -> None:
        self._target = target
        self._args = args
        self.daemon = daemon

    def start(self) -> None:
        self._target(*self._args)


class TestState:
    def test_file_path_from_uri_accepts_local_file_uri(self, tmp_path):
        file_path = tmp_path / "report 1.txt"
        file_path.write_text("hello", encoding="utf-8")

        assert file_path_from_uri(file_path.as_uri()) == file_path

    def test_file_path_from_uri_rejects_non_file_uri(self):
        assert file_path_from_uri("https://example.test/file.txt") is None

    def test_file_path_from_uri_accepts_plain_path(self, tmp_path):
        file_path = tmp_path / "report.txt"

        assert file_path_from_uri(str(file_path)) == file_path

    def test_first_uploadable_file_ignores_dirs_and_remote_uris(self, tmp_path):
        file_path = tmp_path / "share.txt"
        file_path.write_text("payload", encoding="utf-8")

        assert (
            first_uploadable_file(
                ["https://example.test", tmp_path.as_uri(), file_path.as_uri()]
            )
            == file_path
        )

    def test_upload_file_posts_multipart_and_returns_url(self, tmp_path):
        file_path = tmp_path / 'my "file".txt'
        file_path.write_text("hello", encoding="utf-8")
        seen = {}

        def opener(request, timeout):
            seen["timeout"] = timeout
            seen["method"] = request.get_method()
            seen["content_type"] = request.headers["Content-type"]
            seen["body"] = request.data
            return _Response(
                b'{"status":"success","data":{"url":"https://tmpfiles.org/abc.txt"}}'
            )

        result = upload_file(file_path, endpoint="https://upload.test", opener=opener)

        assert result == UploadResult(
            url="https://tmpfiles.org/abc.txt",
            file_name='my "file".txt',
        )
        assert seen["method"] == "POST"
        assert "multipart/form-data" in seen["content_type"]
        assert b'name="file"' in seen["body"]
        assert b"hello" in seen["body"]

    def test_upload_file_rejects_invalid_response(self, tmp_path):
        file_path = tmp_path / "share.txt"
        file_path.write_text("payload", encoding="utf-8")

        with pytest.raises(UploadError):
            upload_file(file_path, opener=lambda _request, timeout: _Response(b"nope"))

    def test_upload_file_rejects_response_without_url(self, tmp_path):
        file_path = tmp_path / "share.txt"
        file_path.write_text("payload", encoding="utf-8")

        with pytest.raises(UploadError, match="invalid URL"):
            upload_file(
                file_path,
                opener=lambda _request, timeout: _Response(
                    b'{"status":"success","data":{}}'
                ),
            )

    def test_upload_file_reports_http_error(self, tmp_path):
        file_path = tmp_path / "share.txt"
        file_path.write_text("payload", encoding="utf-8")

        def opener(_request, timeout):
            _ = timeout
            raise urllib.error.HTTPError(
                url="https://tmpfiles.org",
                code=413,
                msg="too large",
                hdrs=None,
                fp=io.BytesIO(b"too large"),
            )

        with pytest.raises(UploadError, match="too large"):
            upload_file(file_path, opener=opener)


class TestRender:
    def test_render_icon_for_each_state(self):
        for status in DragshareStatus:
            assert render_icon(size=48, status=status) is not None


class TestApplet:
    def test_drop_uploads_and_copies_result(self, monkeypatch, tmp_path):
        file_path = tmp_path / "share.txt"
        file_path.write_text("payload", encoding="utf-8")
        copied = []
        saved = []

        monkeypatch.setattr(
            dragshare_applet_mod,
            "upload_file",
            lambda path: UploadResult(
                url="https://tmpfiles.org/share.txt", file_name=path.name
            ),
        )
        monkeypatch.setattr(
            dragshare_applet_mod.GLib,
            "idle_add",
            lambda callback, *args: callback(*args),
        )
        monkeypatch.setattr(
            dragshare_applet_mod.threading,
            "Thread",
            lambda **kwargs: _ImmediateThread(**kwargs),
        )
        monkeypatch.setattr(
            DragshareApplet,
            "_copy_to_clipboard",
            lambda _self, text: copied.append(text),
        )
        applet = DragshareApplet(
            icon_size=48,
            config=SimpleNamespace(
                applet_prefs={},
                save=lambda: saved.append(True),
            ),
        )

        consumed = applet.on_drop_uris([file_path.as_uri()])

        assert consumed is True
        assert copied == ["https://tmpfiles.org/share.txt"]
        assert applet._last_url == "https://tmpfiles.org/share.txt"
        assert saved == [True]

    def test_drop_without_local_file_sets_error(self, tmp_path):
        applet = DragshareApplet(icon_size=48, config=Config())

        consumed = applet.on_drop_uris(["https://example.test/file.txt"])

        assert consumed is True
        assert applet._status is DragshareStatus.ERROR
