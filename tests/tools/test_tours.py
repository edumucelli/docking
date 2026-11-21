from pathlib import Path

import pytest

from docking.applets import get_applet_catalog
from docking.applets.identity import applet_desktop_id, parse_applet_id
from tools.linux.tours.recorder import (
    Box,
    Cue,
    bar_position,
    copy_profile,
    write_subtitles,
)
from tools.linux.tours.scenes import (
    OVERVIEW_APPLETS,
    OVERVIEW_ITEMS,
    applet_chapters,
    verify_status,
)


def test_tour_chapters_cover_the_catalog():
    catalog = get_applet_catalog()
    chapters = applet_chapters()
    assert len({category for category, _ in chapters}) == len(chapters)
    for category, chapter in chapters:
        assert all(meta.category.value == category for meta in chapter)
    ids = [meta.id for _, chapter in chapters for meta in chapter]
    assert sorted(ids) == sorted(catalog)
    assert set(OVERVIEW_APPLETS) <= catalog.keys()
    assert len(OVERVIEW_APPLETS) == len(set(OVERVIEW_APPLETS)) == 11


def test_overview_opens_on_the_hero_shelf_with_both_messaging_applets():
    assert len(OVERVIEW_ITEMS) == len(set(OVERVIEW_ITEMS)) == 30
    assert OVERVIEW_ITEMS.count(None) == 1
    assert OVERVIEW_ITEMS[-2:] == tuple(
        map(applet_desktop_id, ("whatsapp", "telegram"))
    )
    assert applet_desktop_id("volume") not in OVERVIEW_ITEMS
    catalog = get_applet_catalog()
    for item in OVERVIEW_ITEMS:
        if item and (applet := parse_applet_id(item)):
            assert applet in catalog


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
