"""Native GameScope captures must be fresh, complete and include overlays."""

from types import SimpleNamespace

import cairo
import pytest

from tests.visual.backends.layer_shell.gamescope import capture, probe


def test_screen_buffer_capture_discards_stale_and_waits_for_complete_png(
    monkeypatch, tmp_path
):
    source = tmp_path / "gamescope.png"
    source.write_bytes(b"old screenshot")
    target = tmp_path / "native.png"
    (tmp_path / "outer-display").write_text(":99")
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setattr(capture, "SCREENSHOT", source)
    monkeypatch.setattr(capture.time, "sleep", lambda _delay: None)
    requests = []

    def request(command, **kwargs):
        assert not source.exists()
        requests.append(command)
        cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4).write_to_png(str(source))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(capture.subprocess, "run", request)
    load = capture.GdkPixbuf.Pixbuf.new_from_file
    reads = []

    def read(path):
        reads.append(path)
        if len(reads) == 1:
            raise capture.GLib.Error("PNG still being written")
        return load(path)

    monkeypatch.setattr(capture.GdkPixbuf.Pixbuf, "new_from_file", read)
    capture.capture(target)
    assert requests[0][-3:] == ["-set", "GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT", "4"]
    assert len(reads) == 2
    assert target.read_bytes().startswith(b"\x89PNG")
    assert not source.exists()


@pytest.mark.parametrize("visible", [False, True])
def test_overlay_probe_requires_native_pixels_and_records_input_limit(
    monkeypatch, tmp_path, visible
):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    config = SimpleNamespace(position="bottom", additional_distance_from_edge=0)

    async def capture(_phase):
        width, height = 160, 160
        edge, gap = config.position, config.additional_distance_from_edge
        x0 = 6 + gap if edge == "left" else 106 - gap if edge == "right" else 56
        y0 = 6 + gap if edge == "top" else 106 - gap if edge == "bottom" else 56
        pixels = bytearray(width * height * 3)
        if visible:
            for y in range(y0, y0 + 48):
                for x in range(x0, x0 + 48):
                    pixels[(y * width + x) * 3 : (y * width + x) * 3 + 3] = (
                        b"\x00\x00\xff"
                    )
        return SimpleNamespace(
            get_width=lambda: width,
            get_height=lambda: height,
            get_pixels=lambda: bytes(pixels),
            get_rowstride=lambda: width * 3,
            get_n_channels=lambda: 3,
        )

    window = SimpleNamespace(
        config=config, placement=SimpleNamespace(reposition=lambda: None)
    )
    overlay = probe.OverlayProbe(
        window, SimpleNamespace(name="wayland-layer-shell"), "bottom"
    )
    overlay.capture = capture

    async def settle(_seconds):
        pass

    overlay.settle = settle
    task = overlay.run()
    with pytest.raises(StopIteration if visible else AssertionError):
        task.send(None)
    report = tmp_path / "overlay-result.json"
    assert report.exists() is visible
    if visible:
        import json

        assert json.loads(report.read_text())["input_supported"] is False
