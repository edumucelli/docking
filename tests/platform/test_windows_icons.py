"""A native icon arriving asynchronously replaces the cached generic icon."""

from docking.platform import icons
from docking.platform.backends.windows import icons as native_icons
from tests.platform.application_fakes import application


def test_ready_native_icon_replaces_fallback_and_notifies_subscribers(monkeypatch):
    caches = []

    class Cache:
        def __init__(self, on_ready):
            self.on_ready = on_ready
            self.image = None
            self.stopped = False
            caches.append(self)

        def get(self, _info, _size):
            return self.image

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(icons, "is_windows", lambda: True)
    monkeypatch.setattr(native_icons, "WindowsIconCache", Cache)
    loader = icons.IconLoader()
    fallback = object()
    monkeypatch.setattr(loader, "_try_load_icon_without_fallback", lambda **_kw: None)
    monkeypatch.setattr(loader, "_try_load_fallback_icon", lambda **_kw: fallback)
    changes = []
    unsubscribe = loader.subscribe(changes.append)
    app = application("Example.App")
    assert loader.load_desktop_icon(app, 2) is fallback

    cache = caches[0]
    cache.image = native_icons.NativeIcon(bytes((255, 0, 0, 128)) * 4, 2, 2)
    cache.on_ready(app.desktop_id)
    pixbuf = loader.load_desktop_icon(app, 2)
    assert pixbuf.get_width() == pixbuf.get_height() == 2
    assert bytes(pixbuf.get_pixels())[:4] == bytes((255, 0, 0, 128))
    assert changes == [app.desktop_id]
    unsubscribe()
    loader.stop()
    assert cache.stopped
