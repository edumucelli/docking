import json
import signal
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest

from docking.applets import get_applet_catalog
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    applet_desktop_id,
    parse_applet_id,
)
from tools.linux.tours import recorder
from tools.linux.tours.recorder import (
    Box,
    Cue,
    bar_position,
    copy_profile,
    subtitle_events,
    write_subtitles,
)
from tools.linux.tours.scenes import (
    OVERVIEW_APPLETS,
    OVERVIEW_ITEMS,
    applet_chapters,
    music_menu,
    overview_items,
    verify_status,
)


def test_tour_chapters_cover_the_catalog():
    catalog = get_applet_catalog()
    chapters = applet_chapters()
    categories = {meta.category for meta in catalog.values()}
    assert [name for name, _ in chapters] == [
        category.value for category in APPLET_CATEGORY_ORDER if category in categories
    ]
    for category, chapter in chapters:
        assert all(meta.category.value == category for meta in chapter)
    ids = [meta.id for _, chapter in chapters for meta in chapter]
    assert sorted(ids) == sorted(catalog)
    assert set(OVERVIEW_APPLETS) <= catalog.keys()
    assert len(OVERVIEW_APPLETS) == len(set(OVERVIEW_APPLETS)) == 15


@pytest.mark.parametrize(
    ("previous", "expected"), [("Pause", "Play"), ("Play", "Pause")]
)
def test_music_menu_reopens_until_playback_state_changes(previous, expected):
    desktop = Mock()
    menus = iter(([previous, "Next"], [expected, "Next"]))

    def open_menu(*_args):
        desktop.menu_labels.return_value = next(menus)

    desktop.point.side_effect = open_menu

    music_menu(desktop, expected)

    assert [call.args for call in desktop.point.call_args_list] == [
        (applet_desktop_id("music"), 3),
        (applet_desktop_id("music"), 3),
    ]
    desktop.dismiss.assert_called_once_with(recenter=False)


def test_missing_gifski_fails_before_recording(tmp_path, monkeypatch, capsys):
    output = tmp_path / "exports"
    monkeypatch.setattr(
        "sys.argv", ["tours", "--tour", "preferences", "--output", str(output), "--gif"]
    )
    monkeypatch.setattr(
        recorder.shutil,
        "which",
        lambda executable: None if executable == "gifski" else executable,
    )

    with pytest.raises(SystemExit) as error:
        recorder.main()

    assert error.value.code == 2
    assert "Missing executable: gifski" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    ("gif", "failed"), [(False, False), (True, False), (True, True)]
)
def test_tour_publishes_after_requested_exports_finish(
    tmp_path, monkeypatch, gif, failed
):
    tours = ("applets", "preferences")
    for tour in tours:
        for suffix in ("mp4", "gif"):
            (tmp_path / f"{tour}.{suffix}").write_bytes(b"previous")
    monkeypatch.setattr(
        "sys.argv",
        [
            "tours",
            "--tour",
            "applets",
            "--tour",
            "preferences",
            "--available-controls",
            "--output",
            str(tmp_path),
            *(["--gif"] if gif else []),
        ],
    )
    lookup = Mock(side_effect=lambda executable: executable)
    monkeypatch.setattr(recorder.shutil, "which", lookup)
    monkeypatch.setattr(recorder, "SessionDirectory", lambda **_: nullcontext(tmp_path))
    monkeypatch.setattr(recorder, "private_environment", lambda *_: {"LANGUAGE": "de"})
    converted = []

    def run(command, **kwargs):
        assert kwargs["check"]
        assert command[0] == "xvfb-run"
        assert "LANGUAGE" not in kwargs["env"]
        staging = Path(command[command.index("--output") + 1])
        for tour in tours:
            (staging / f"{tour}.mp4").write_bytes(b"new video")

    def export_gif(source, destination):
        assert source.read_bytes() == b"new video"
        converted.append(source.stem)
        if failed and source.stem == "preferences":
            raise recorder.subprocess.CalledProcessError(1, ["gifski"])
        destination.write_bytes(b"new animation")

    monkeypatch.setattr(recorder.subprocess, "run", run)
    monkeypatch.setattr(recorder, "export_gif", export_gif)

    assert recorder.main() == int(failed)
    assert any(call.args == ("gifski",) for call in lookup.call_args_list) == gif
    assert converted == (list(tours) if gif else [])
    for tour in tours:
        assert (tmp_path / f"{tour}.mp4").read_bytes() == (
            b"previous" if failed else b"new video"
        )
        assert (tmp_path / f"{tour}.gif").read_bytes() == (
            b"new animation" if gif and not failed else b"previous"
        )


def test_gif_export_checks_decoder_status(tmp_path, monkeypatch):
    decoder = Mock(stdout=BytesIO())
    decoder.wait.return_value = 1
    launch = Mock(return_value=nullcontext(decoder))
    encode = Mock()
    monkeypatch.setattr(recorder.subprocess, "Popen", launch)
    monkeypatch.setattr(recorder.subprocess, "run", encode)

    with pytest.raises(recorder.subprocess.CalledProcessError) as error:
        recorder.export_gif(tmp_path / "overview.mp4", tmp_path / "overview.gif")

    assert error.value.cmd[-2:] == ["yuv4mpegpipe", "-"]
    assert encode.call_args.args[0][-1] == "-"
    assert encode.call_args.kwargs == {"stdin": decoder.stdout, "check": True}
    assert decoder.stdout.closed


def test_publication_failure_restores_all_previous_outputs(tmp_path, monkeypatch):
    run = tmp_path / "recording"
    run.mkdir()
    filenames = ["overview.mp4", "overview.gif", "applets.mp4"]
    for name in filenames:
        (run / name).write_bytes(b"new")
    (tmp_path / "overview.mp4").write_bytes(b"previous overview")
    (tmp_path / "applets.mp4").write_bytes(b"previous applets")

    replace = Path.replace

    def fail_last_replacement(source, destination):
        if source == run / "publish" / "applets.mp4":
            raise PermissionError("publication failed")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_last_replacement)
    with pytest.raises(PermissionError, match="publication failed"):
        recorder.publish_tours(run, tmp_path, filenames)

    assert (tmp_path / "overview.mp4").read_bytes() == b"previous overview"
    assert (tmp_path / "applets.mp4").read_bytes() == b"previous applets"
    assert not (tmp_path / "overview.gif").exists()
    assert all((run / name).read_bytes() == b"new" for name in filenames)


def test_publication_finishes_before_processing_ctrl_c(tmp_path, monkeypatch):
    run = tmp_path / "recording"
    run.mkdir()
    filenames = ["overview.mp4", "overview.gif"]
    for name in filenames:
        (run / name).write_bytes(b"new")
        (tmp_path / name).write_bytes(b"previous")
    replace = Path.replace

    def interrupt_after_replacement(source, destination):
        result = replace(source, destination)
        if source.name == filenames[0]:
            signal.raise_signal(signal.SIGINT)
        return result

    monkeypatch.setattr(Path, "replace", interrupt_after_replacement)
    with pytest.raises(KeyboardInterrupt):
        recorder.publish_tours(run, tmp_path, filenames)

    assert all((tmp_path / name).read_bytes() == b"new" for name in filenames)


def test_overview_opens_on_the_hero_shelf_with_both_messaging_applets():
    assert len(OVERVIEW_ITEMS) == len(set(OVERVIEW_ITEMS)) == 29
    assert OVERVIEW_ITEMS.count(None) == 1
    assert OVERVIEW_ITEMS[-2:] == tuple(
        map(applet_desktop_id, ("whatsapp", "telegram"))
    )
    assert OVERVIEW_ITEMS[-3] == applet_desktop_id("volume")
    assert "eom.desktop" not in OVERVIEW_ITEMS
    assert OVERVIEW_ITEMS[8:10] == tuple(
        map(applet_desktop_id, ("systemmonitor", "music"))
    )
    assert applet_desktop_id("thermals") not in OVERVIEW_ITEMS
    assert applet_desktop_id("news") not in OVERVIEW_ITEMS
    catalog = get_applet_catalog()
    for item in OVERVIEW_ITEMS:
        if item and (applet := parse_applet_id(item)):
            assert applet in catalog


def test_overview_keeps_only_installed_optional_launchers():
    folder = "file:///tmp/Tour%20files"

    items = overview_items(folder, {"terminator.desktop"})

    assert [item for item in items if item.endswith(".desktop")] == [
        "terminator.desktop",
        "caja.desktop",
    ]
    assert [
        item for item in items if item != folder and not item.endswith(".desktop")
    ] == [item for item in OVERVIEW_ITEMS if item and parse_applet_id(item)]
    assert items.count(folder) == 1


def test_search_chapter_enables_the_search_dialog(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    child = Mock(pid=1)
    child.poll.return_value = None
    monkeypatch.setattr(recorder, "process", lambda *args, **kwargs: nullcontext(child))

    with recorder.dock(
        Mock(),
        tmp_path,
        {"global_search_enabled": False},
        [applet_desktop_id("search")],
    ):
        config = json.loads((tmp_path / "docking" / "dock.json").read_text())
        assert config["global_search_enabled"] is True


def test_context_bar_avoids_windows_and_keeps_captions_in_one_event(tmp_path):
    window = Box(0, 0, 900, 500)
    bar = bar_position([window])
    assert not bar.overlaps(window)
    subtitles = tmp_path / "captions.ass"
    write_subtitles(
        subtitles,
        [Cue(1.2, 3.4, "Applets", "Show {all}\nand the menus.", bar)],
    )
    events = [
        line for line in subtitles.read_text().splitlines() if "Dialogue:" in line
    ]
    assert len(events) == 3
    assert all("0:00:01.20,0:00:03.40" in event for event in events)
    assert events[0].count(" b ") == 4
    assert events[-1].endswith("Show (all) and the menus.")


@pytest.mark.parametrize(("button", "x"), [(1, 16), (3, 20)])
def test_context_bar_highlights_the_clicked_mouse_button(button, x):
    cue = Cue(0, 1, "Clock", "Click", bar_position([]), button)
    selected = [line for line in subtitle_events(cue).splitlines() if "F5AD60" in line]
    assert len(selected) == 1
    assert f"m {x} 4" in selected[0]


@pytest.mark.parametrize(
    ("shortcut", "button"), [("Ctrl +", 3), ("Ctrl+Alt+Space", None)]
)
def test_context_bar_shows_keyboard_shortcuts(shortcut, button):
    cue = Cue(
        0,
        1,
        "Tour",
        "Demonstrate a shortcut.",
        bar_position([]),
        button,
        shortcut=shortcut,
    )
    events = subtitle_events(cue).splitlines()
    assert any(line.endswith(shortcut) for line in events)
    highlights = [line for line in events if "F5AD60" in line]
    if button == 3:
        assert len(highlights) == 1
        assert "m 20 4" in highlights[0]
    else:
        assert not highlights


def test_context_bar_tracks_clicks_and_the_scroll_wheel_at_input_time():
    cue = Cue(
        1,
        9,
        "Overview",
        "Mouse input",
        bar_position([]),
        mouse_actions=((2, 3), (2.5, 1), (5, 5), (8.5, 4)),
    )
    highlights = [
        line for line in subtitle_events(cue).splitlines() if "F5AD60" in line
    ]
    assert len(highlights) == 4
    for line, start, end, path in zip(
        highlights,
        ("02.00", "02.50", "05.00", "08.50"),
        ("02.50", "03.50", "06.00", "09.00"),
        ("m 20 4", "m 16 4", "m 15 8", "m 15 8"),
        strict=True,
    ):
        assert f"0:00:{start},0:00:{end}" in line
        assert path in line


def test_prepared_profile_is_copied_without_external_symlinks(tmp_path: Path):
    source, target = tmp_path / "source", tmp_path / "target"
    state = source / "state"
    state.mkdir(parents=True)
    sample = state / "sample.json"
    sample.write_text("{}")
    whatsapp = source / "data" / "docking" / "whatsapp"
    whatsapp.mkdir(parents=True)
    (whatsapp / "cookies.sqlite").write_bytes(b"demo cookies")
    try:
        (state / "external").symlink_to(tmp_path / "unrelated")
    except OSError:
        pytest.skip("Symlinks unavailable")
    copy_profile(source, target, {})
    assert (target / "state" / "sample.json").read_text() == "{}"
    assert not (target / "state" / "external").is_symlink()
    assert not (target / "data" / "docking" / "whatsapp").exists()


@pytest.mark.parametrize(
    "status",
    [
        "Weather\nUnavailable",
        "No paired devices; open KDE Connect to pair your phone",
        "No plants configured",
    ],
)
def test_unavailable_applet_state_fails_the_scene(status):
    with pytest.raises(RuntimeError, match="unavailable state"):
        verify_status([status])
    verify_status(["Paired: 1", "Connected: 0"])


@pytest.mark.parametrize(
    "headline",
    [
        "Building an offline-first application",
        "Error: Network unavailable",
        "Unavailable",
    ],
)
def test_hacker_news_headline_is_separate_from_status(headline):
    rows = ["Hacker News", headline, "32 points, 4 comments"]
    verify_status(rows, applet="hackernews")
    verify_status(["\n".join(rows)], applet="hackernews")
    with pytest.raises(RuntimeError, match="Error: Timed out"):
        verify_status([*rows, "Error: Timed out"], applet="hackernews")
