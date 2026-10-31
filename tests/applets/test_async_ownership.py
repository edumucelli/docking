"""A queued reply must still belong to the active applet or dialog."""

import threading
from queue import Queue
from unittest.mock import Mock

import pytest
from gi.repository import Gdk, GLib, Gtk

from docking.applets.camshield import applet as camera_mod
from docking.applets.camshield.state import CamshieldState
from docking.applets.dragshare import applet as share_mod
from docking.applets.dragshare.state import UploadResult
from docking.applets.quote import applet as quote_mod
from docking.applets.quote.state import QuoteEntry
from docking.applets.urlshortener import applet as shortener_mod
from docking.core.config import Config


@pytest.fixture
def posted_results(monkeypatch):
    """Run real workers while keeping their UI delivery under test control."""
    results = Queue()
    threads = []
    thread_factory = threading.Thread

    def start_thread(*args, **kwargs):
        thread = thread_factory(*args, **kwargs)
        threads.append(thread)
        return thread

    def idle_add(callback, *args):
        results.put((callback, args))
        return 1

    monkeypatch.setattr(threading, "Thread", start_thread)
    monkeypatch.setattr(GLib, "idle_add", idle_add)
    yield results
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()


def _deliver(result):
    callback, args = result
    return callback(*args)


def test_selecting_quote_source_starts_its_fetch_before_old_reply(
    monkeypatch, posted_results
):
    monkeypatch.setattr(
        quote_mod, "fetch_quotes", lambda *, source, limit: [QuoteEntry(text=source)]
    )
    applet = quote_mod.QuoteApplet(48, Config())
    applet._fetch_async(show_first=False)
    old_reply = posted_results.get(timeout=2)

    applet._set_source("qdb")
    _deliver(old_reply)

    assert applet._loading, "The old reply must not finish the new source's request"
    _deliver(posted_results.get(timeout=2))
    applet.on_clicked()
    assert applet._current == QuoteEntry(text="qdb")


def test_stopped_upload_cannot_replace_the_users_clipboard(
    tmp_path, monkeypatch, posted_results
):
    result = UploadResult(url="https://example.test/upload", file_name="notes.txt")
    monkeypatch.setattr(share_mod, "upload_file", lambda _path: result)
    config = Config()
    applet = share_mod.DragshareApplet(48, config)
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clipboard.set_text("user clipboard", -1)
    applet._start_upload(tmp_path / "notes.txt")
    reply = posted_results.get(timeout=2)
    applet.stop()

    _deliver(reply)

    assert clipboard.wait_for_text() == "user clipboard"
    assert "dragshare" not in config.applet_prefs


def test_camera_refresh_queued_before_stop_cannot_restart_pulse_timer(
    monkeypatch, posted_results
):
    from docking.applets.camshield.backend import CamshieldBackend
    from docking.applets.worker import BackgroundWorker

    backend = Mock(spec=CamshieldBackend)
    backend.get_state.return_value = CamshieldState(True, True)
    monkeypatch.setattr(camera_mod, "create_backend", lambda: backend)
    monkeypatch.setattr(
        camera_mod,
        "BackgroundWorker",
        lambda **kwargs: BackgroundWorker(
            **kwargs, idle_add=GLib.idle_add, thread_factory=threading.Thread
        ),
    )
    applet = camera_mod.CamshieldApplet(48, Config())
    queued_result = posted_results.get(timeout=2)
    applet.start(lambda: None)
    queued_refresh = posted_results.get(timeout=2)
    applet.stop()

    try:
        _deliver(queued_result)
        _deliver(queued_refresh)
        assert applet._pulse_timer_id == 0
        backend.get_state.assert_called_once()
    finally:
        applet.stop()


def test_closed_shortener_dialog_reply_cannot_finish_new_dialog_request(
    monkeypatch, posted_results
):
    monkeypatch.setattr(shortener_mod, "shorten_url", lambda *, url: url + "/short")
    applet = shortener_mod.UrlShortenerApplet(48, Config())
    try:
        applet.on_clicked()
        applet._url_entry.set_text("https://example.test/old")
        applet._shorten_btn.clicked()
        old_reply = posted_results.get(timeout=2)
        applet._dialog.response(Gtk.ResponseType.CLOSE)
        applet.on_clicked()
        applet._url_entry.set_text("https://example.test/new")
        applet._shorten_btn.clicked()
        new_reply = posted_results.get(timeout=2)

        _deliver(old_reply)

        assert not applet._shorten_btn.get_sensitive()
        assert applet._last_result == ""
        _deliver(new_reply)
        assert applet._last_url == "https://example.test/new"
        assert applet._last_result == "https://example.test/new/short"
    finally:
        applet.stop()


def test_enter_requests_keep_the_newest_shortened_url(monkeypatch, posted_results):
    monkeypatch.setattr(shortener_mod, "shorten_url", lambda *, url: url + "/short")
    applet = shortener_mod.UrlShortenerApplet(48, Config())
    try:
        applet.on_clicked()
        applet._url_entry.set_text("https://example.test/old")
        applet._url_entry.emit("activate")
        old_reply = posted_results.get(timeout=2)
        applet._url_entry.set_text("https://example.test/new")
        applet._url_entry.emit("activate")
        new_reply = posted_results.get(timeout=2)

        _deliver(new_reply)
        _deliver(old_reply)

        assert applet._last_url == "https://example.test/new"
        assert applet._last_result == "https://example.test/new/short"
    finally:
        applet.stop()


def test_switching_back_to_quote_source_keeps_its_latest_request(
    monkeypatch, posted_results
):
    sequence = []

    def fetch_quotes(*, source, limit):
        sequence.append(source)
        return [QuoteEntry(text=str(len(sequence)))]

    monkeypatch.setattr(quote_mod, "fetch_quotes", fetch_quotes)
    applet = quote_mod.QuoteApplet(48, Config())
    original_source = applet._source
    applet._fetch_async(show_first=False)
    first_reply = posted_results.get(timeout=2)
    applet._set_source("qdb")
    middle_reply = posted_results.get(timeout=2)
    applet._set_source(original_source)
    latest_reply = posted_results.get(timeout=2)

    _deliver(latest_reply)
    _deliver(first_reply)
    _deliver(middle_reply)
    applet.on_clicked()

    assert applet._current == QuoteEntry(text="3")


def test_repeated_drops_do_not_replace_the_upload_in_progress(
    tmp_path, monkeypatch, posted_results
):
    monkeypatch.setattr(
        share_mod,
        "upload_file",
        lambda path: UploadResult(
            url="https://example.test/" + path.name, file_name=path.name
        ),
    )
    paths = [tmp_path / name for name in ("first.txt", "second.txt", "third.txt")]
    for path in paths:
        path.write_text("payload")
    applet = share_mod.DragshareApplet(48, Config())
    try:
        applet.on_drop_uris([paths[0].as_uri()])
        original_thread = applet._upload_thread
        reply = posted_results.get(timeout=2)

        applet.on_drop_uris([paths[1].as_uri()])
        applet.on_drop_uris([paths[2].as_uri()])

        assert applet._upload_thread is original_thread
        assert applet._file_name == "first.txt"
        _deliver(reply)
        assert applet._last_url == "https://example.test/first.txt"
    finally:
        applet.stop()
