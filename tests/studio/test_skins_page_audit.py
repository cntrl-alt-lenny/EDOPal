from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtGui import QPixmap
from pytestqt.qtbot import QtBot

import themes
from studio.pages.skins_model import preset_id
from studio.shell import StudioWindow


def test_hidden_maker_stops_both_timers(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.navigate("skins")
    page = window.current
    qtbot.waitUntil(lambda: page.data is not None)
    page.gallery.create.click()
    page.maker.fine.setChecked(True)
    page.maker.set_theme(page.maker.theme)
    assert page.view.preview.timer.isActive()
    assert window.navigate("cards")
    assert not page.view.preview.timer.isActive() and not page.maker.samples.isActive()


def test_thumbnail_eviction_uses_image_bytes(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.navigate("skins")
    page = window.current
    qtbot.waitUntil(lambda: page.data is not None)
    cache = page.gallery.grid.tiles.cache
    cache.budget = 8 * 1024
    cache.clear()
    for key in ("a", "b", "c"):
        cache[Path(key)] = QPixmap(32, 32)
    assert list(cache) == [Path("b"), Path("c")]


def test_unreadable_history_and_reserved_theme_id_are_isolated(tmp_path: Path) -> None:
    lib = themes.ThemeLibrary(tmp_path)
    preset = themes.PRESETS[0]
    uid = preset_id(preset)
    lib.save(preset, uid)
    other = lib.save(themes.PRESETS[1])
    removed = tmp_path / "removed"
    removed.mkdir()
    (removed / ("f" * 32 + ".json")).write_bytes(b"invalid")
    issues = []
    assert list(lib.entries(issues, reserved=[uid])) == [other]
    assert not lib.removed_entries(issues)
    assert [issue.filename for issue in issues] == [uid + ".json", "removed/" + "f" * 32 + ".json"]
    lib.save(themes.PRESETS[2], uid)
    with pytest.raises(ValueError, match="changed"):
        lib.set_aside(issues[0])
    assert lib.load(uid) == themes.PRESETS[2]
