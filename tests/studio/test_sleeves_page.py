import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog, QLabel
from pytestqt.qtbot import QtBot

from sleeves import PALETTES, PATTERNS, ArtMissing, ArtUnavailable, CardArt
from studio.context import Installation
from studio.pages import sleeves_work
from studio.pages.sleeves import SleevesPage
from studio.pages.sleeves_collection import collection, crop_box, filtered
from studio.shell import StudioWindow


@pytest.fixture
def desk(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[StudioWindow, SleevesPage]]:
    monkeypatch.setattr("music.settings._game_running", lambda game: False)
    window = make_window()
    window.resize(1280, 800)
    window.nav["sleeves"].click()
    page = window.current
    monkeypatch.setattr(page, "confirm", lambda *_: True)
    ready(qtbot, page)
    yield window, page
    if page.editor:
        page.escape()
    window.navigate("skins")
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs, timeout=12000)


def ready(bot: QtBot, page: SleevesPage) -> None:
    bot.waitUntil(
        lambda: page.snapshot is not None and not (page.loading or page.writing or page.saving),
        timeout=10000,
    )


def pick(page: SleevesPage, qtbot: QtBot, query: str, source: str = "All sleeves") -> None:
    gallery = page.gallery
    gallery.search.setText(query)
    gallery.source.setCurrentText(source)
    index = gallery.grid.model().index(0, 0)
    assert index.isValid()
    gallery.grid.setCurrentIndex(index)
    gallery.grid.setFocus()
    qtbot.keyClick(gallery.grid, Qt.Key.Key_Return)
    assert page.target in page.pending


def test_complete_collection_filter_and_crop() -> None:
    prints = collection()
    assert len({c.key for c in prints}) == len(PATTERNS) * len(PALETTES)
    from sleeves.catalog import PALETTE_FAMILIES

    assert len(filtered(prints, "sol invictus", "Ready-made", "Green", {})) == sum(
        family == "Green" for family in PALETTE_FAMILIES.values()
    )
    assert all(c.family == PALETTE_FAMILIES[c.palette] for c in prints)
    assert not filtered(prints, "", "Saved sleeves", "All colors", {})
    for size in ((622, 622), (1920, 240), (240, 900), (1, 1)):
        for centre in ((0, 0), (0.5, 0.5), (1, 1)):
            x, y, w, h = crop_box(size, centre, 2)
            assert 0 <= x <= size[0] - w and 0 <= y <= size[1] - h
            assert w / h == pytest.approx(708 / 1016)


def test_expanded_pattern_collection_search(desk: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from studio.pages import sleeves_gallery

    gallery = desk[1].gallery
    for number in range(64):
        key = f"expanded-{number}"
        monkeypatch.setitem(PATTERNS, key, replace(PATTERNS["sun"], name=f"Expanded {number}"))
    prints = {item.key: item for item in collection()}
    monkeypatch.setattr(sleeves_gallery, "prints", lambda: prints)
    gallery.set_items([])
    assert gallery.grid.model().rowCount() == len(PATTERNS) * len(PALETTES)
    gallery.search.setText("expanded 63")
    assert len(gallery.visible) == len(PALETTES)
    assert len(gallery.grid.tiles.pending) <= 4


def test_slots_apply_undo_recent_and_conflicts(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    textures = Path(page.install.path) / "textures"
    textures.mkdir()
    old = page.library.image_path(page.snapshot.items[0].entry.id).read_bytes()
    mine, opponent = textures / "cover.png", textures / "cover2.png"
    mine.write_bytes(old)
    assert len(page.gallery.visible) == len(page.snapshot.items) + len(PATTERNS) * len(PALETTES)
    pick(page, qtbot, "Orrery", "Ready-made")
    page.same.click()
    assert page.pending["opponent"] == page.pending["mine"]
    page.banner.slots["opponent"].click()
    pick(page, qtbot, "silk fans")
    assert mine.read_bytes() == old and not opponent.exists()
    page.apply.click()
    ready(qtbot, page)
    assert page.undo.isEnabled()
    assert mine.read_bytes() != old and opponent.read_bytes() != mine.read_bytes()
    assert all(s.status.matches for s in page.snapshot.slots.values())
    page.gallery.search.clear()
    page.gallery.source.setCurrentText("Recently used")
    assert len(page.gallery.visible) == 2
    monkeypatch.setattr(page, "confirm", lambda *_: True)
    page.undo.click()
    ready(qtbot, page)
    assert mine.read_bytes() == old and not opponent.exists()
    assert not page.undo.isEnabled()
    pick(page, qtbot, "Orrery", "Ready-made")
    page.apply.click()
    ready(qtbot, page)
    installed = opponent.read_bytes()
    opponent.write_bytes(old)
    page.activation_changed(Qt.ApplicationState.ApplicationActive)
    ready(qtbot, page)
    assert page.apply.text() == "Apply saved sleeves again"
    assert "Changed" in page.banner.slots["opponent"].state.text()
    page.undo.click()
    qtbot.waitUntil(lambda: not page.writing)
    assert opponent.read_bytes() == old and page.retry.isVisible()
    page.apply.click()
    ready(qtbot, page)
    assert opponent.read_bytes() == installed


def test_busy_close_game_and_retry(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    pick(page, qtbot, "Vortex")
    gate = threading.Event()

    def running(game: Path) -> bool:
        assert threading.current_thread() is not threading.main_thread()
        gate.wait(4)
        return True

    monkeypatch.setattr("music.settings._game_running", running)
    assert page.apply.text() == "Apply staged pair" and "Vortex" in page.status.text()
    page.apply.click()
    assert page.apply.text() == "Applying sleeves…" and not page.apply.isEnabled()
    assert page.gallery.grid.isEnabled() and page.banner.slots["mine"].isEnabled()
    assert not window.navigate("music") and not window.change.isEnabled()
    assert not page.hidden() and not page.request_close()
    assert "change folders when this finishes" in page.status.text()
    gate.set()
    qtbot.waitUntil(lambda: not page.writing)
    assert page.block.isVisible() and not page.apply.isVisible()
    assert "mine" in page.pending
    assert not (Path(page.install.path) / "textures/cover.png").exists()
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    page.block.retry.click()
    ready(qtbot, page)
    assert page.snapshot.slots["mine"].status.matches
    assert page.apply.text() == "Applied" and not page.apply.isEnabled()
    assert window.change.isEnabled()


def test_designer_collection_edit_bin_restore(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    page.gallery.actions["design"].click()
    design = page.editor
    design.randomise.click()
    design.pattern.setCurrentIndex(design.pattern.findData("rose"))
    design.palette.setCurrentText("Glacier")
    design.inks.buttons[2].click()
    design.inks.sliders[0].setValue(15)
    assert design.palette.currentIndex() == -1
    design.emblem.setCurrentText("Star")
    design.frame.setCurrentText("Deco")
    design.sliders["lobes"][0].setValue(11)
    design.name.setText("Winter rose")
    qtbot.waitUntil(lambda: design.valid)
    design.save.click()
    ready(qtbot, page)
    item = page.pending["mine"]
    assert item.entry.recipe.params["lobes"] == 11
    assert item.entry.recipe.palette != PALETTES["Glacier"]
    assert item.entry.recipe.emblem.shape == "star"
    page.gallery.actions["manage"].click()
    manage = page.editor
    manage.name.setText("Frost")
    manage.tags.setText("winter, gift")
    manage.favourite.click()
    qtbot.waitUntil(lambda: manage.valid)
    manage.save.click()
    ready(qtbot, page)
    page.gallery.search.setText("gift")
    page.gallery.source.setCurrentText("Favorites")
    assert [c.name for c in page.gallery.visible] == ["Frost"]
    page.gallery.surprise.click()
    monkeypatch.setattr(page, "confirm", lambda *_: True)
    page.gallery.actions["manage"].click()
    page.editor.remove.click()
    ready(qtbot, page)
    assert not page.gallery.visible and not page.pending
    page.gallery.actions["manage"].click()
    page.editor.group.setCurrentIndex(1)
    page.editor.remove.click()
    ready(qtbot, page)
    restored = page.gallery.visible[0].entry
    assert restored.id == item.entry.id and restored.tags == ("winter", "gift")
    assert restored.favourite and restored.recipe == item.entry.recipe


def test_card_fetch_is_explicit_missing_retry_and_crop(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, page = desk
    src = tmp_path / "art.png"
    img = QImage(160, 110, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.blue)
    img.save(str(src))
    calls = []
    outcomes = [ArtMissing(), ArtUnavailable(), src]

    async def fetch(self: CardArt, code: int) -> Path:
        assert threading.current_thread() is not threading.main_thread()
        calls.append(code)
        value = outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(CardArt, "fetch", fetch)
    page.gallery.actions["card"].click()
    editor = page.editor
    qtbot.waitUntil(lambda: editor.index is not None)
    editor.search.setText("No card by this name")
    assert not editor.results.count() and "No matching cards" in editor.preview.caption
    from PySide6.QtWidgets import QScrollArea

    assert not editor.findChildren(QScrollArea)
    editor.search.setText("100")
    assert not calls
    editor.results.setCurrentRow(0)
    qtbot.keyClick(editor.results, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: "No artwork" in editor.hint.text())
    assert not editor.retry.isVisible() and not editor.save.isEnabled()
    editor.results.setCurrentRow(1)
    qtbot.keyClick(editor.results, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: editor.retry.isVisible())
    editor.retry.click()
    qtbot.waitUntil(lambda: editor.valid)
    assert calls == [1000, 1001, 1001]
    editor.mode.setCurrentText("Fill sleeve")
    editor.zoom.setValue(150)
    previous = editor.layout_choice().crop
    qtbot.keyClick(editor.preview, Qt.Key.Key_Left)
    assert editor.layout_choice().crop != previous
    before_drag = editor.layout_choice().crop
    qtbot.waitUntil(lambda: page.slide.currentTime() == page.slide.duration())
    point = editor.preview.rect().center()
    qtbot.mousePress(editor.preview, Qt.MouseButton.LeftButton, pos=point)
    qtbot.mouseMove(editor.preview, pos=point + QPoint(15, 4))
    qtbot.mouseRelease(editor.preview, Qt.MouseButton.LeftButton, pos=point + QPoint(15, 4))
    assert editor.layout_choice().crop != before_drag
    qtbot.waitUntil(lambda: editor.valid)
    editor.save.click()
    ready(qtbot, page)
    assert page.pending["mine"].entry.passcode == 1001
    assert page.pending["mine"].entry.layout.mode == "fill"


def test_image_import_rejection_fit_and_colour_filter(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, page = desk
    page.banner.slots["opponent"].click()
    page.gallery.actions["image"].click()
    editor = page.editor
    path = tmp_path / "A blue sleeve.png"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    path.write_bytes(b"bad")
    editor.pick.click()
    qtbot.waitUntil(lambda: not editor.loading)
    assert "Choose a still" in editor.hint.text() and not editor.save.isEnabled()
    img = QImage(120, 120, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.blue)
    img.save(str(path))
    editor.pick.click()
    qtbot.waitUntil(lambda: editor.valid)
    editor.mode.setCurrentText("Fit whole image")
    editor.background.setCurrentIndex(1)
    qtbot.waitUntil(lambda: editor.valid)
    editor.save.click()
    ready(qtbot, page)
    entry = page.pending["opponent"].entry
    assert entry.name == path.stem and entry.layout.mode == "fit"
    assert entry.source == "imported"
    page.gallery.source.setCurrentText("Your image")
    page.gallery.colour.setCurrentText("Blue")
    qtbot.waitUntil(lambda: len(page.gallery.visible) == 1)


def test_absent_unready_and_read_failure(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    game = page.install
    pick(page, qtbot, "Vortex")
    window.ctx.set_installation(Installation())
    ready(qtbot, page)
    assert not page.pending and not page.apply.isEnabled()
    assert page.retry.text() == "Choose folder" and not page.gallery.actions["card"].isEnabled()
    window.ctx.set_installation(Installation(game.path))
    ready(qtbot, page)
    assert page.retry.text() == "Check again"
    pick(page, qtbot, "Vortex")
    page.retry.click()
    qtbot.waitUntil(lambda: page.install.ready and not page.loading)
    assert page.pending["mine"].name == "Vortex" and page.apply.isEnabled()
    with monkeypatch.context() as patch:

        def fail(*args: object) -> None:
            raise PermissionError("Folder is read-only.")

        patch.setattr(sleeves_work, "inspect", fail)
        window.navigate("skins")
        window.navigate("sleeves")
        qtbot.waitUntil(lambda: page.retry.isVisible())
        assert "read-only" in page.details.toolTip() and page.details.isVisible()
        assert not page.apply.isEnabled()
    page.retry.click()
    ready(qtbot, page)
    page.gallery.actions["design"].click()
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert page.editor is None


@pytest.mark.parametrize("size", [(960, 600), (1280, 800), (1600, 1000), (3840, 2160)])
def test_slot_and_management_controls_fit(desk: tuple, qtbot: QtBot, size: tuple[int, int]) -> None:
    from PySide6.QtCore import QPoint, QRect

    window, page = desk
    window.resize(*size)
    for widget in (page.undo, page.apply):
        assert widget.isVisible() and window.rect().contains(
            QRect(widget.mapTo(window, QPoint()), widget.size())
        )
    page.gallery.actions["manage"].click()
    editor = page.editor
    qtbot.waitUntil(lambda: editor.valid and page.slide.currentTime() == page.slide.duration())
    assert window.width() == size[0] and window.height() == size[1]
    assert not page.undo.isVisible() and not page.apply.isVisible()
    for widget in (editor.group, editor.name, editor.save):
        assert widget.isVisible() and window.rect().contains(
            QRect(widget.mapTo(window, QPoint()), widget.size())
        )
    assert not page.banner.isVisible()


def test_leaving_card_fetch_ignores_late_result(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    release = threading.Event()
    entered = threading.Event()

    async def delayed(self: CardArt, code: int) -> Path:
        entered.set()
        release.wait(5)
        raise ArtUnavailable()

    monkeypatch.setattr(CardArt, "fetch", delayed)
    page.gallery.actions["card"].click()
    editor = page.editor
    qtbot.waitUntil(lambda: editor.index is not None)
    editor.results.setCurrentRow(0)
    qtbot.keyClick(editor.results, Qt.Key.Key_Return)
    qtbot.waitUntil(entered.is_set)
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    window.ctx.set_installation(Installation())
    release.set()
    ready(qtbot, page)
    qtbot.waitUntil(lambda: not page.ctx.tasks.jobs)
    assert page.editor is None and not page.pending and not page.install.path


def test_fast_scroll_filter_and_surprise_paint_the_selected_print(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QPixmap

    import sleeves
    from studio import theme as t

    _, page = desk
    gallery, grid = page.gallery, page.gallery.grid
    draw = sleeves.render
    requests = []

    def record(recipe: sleeves.Recipe, *, thumbnail: bool = False) -> QImage:
        requests.append((threading.get_ident(), thumbnail))
        return draw(recipe, thumbnail=thumbnail)

    monkeypatch.setattr(sleeves, "render", record)
    # Change generations while readers from the old viewport can still be running.
    for row in (grid.model().rowCount() - 15, 6, grid.model().rowCount() - 1):
        grid.scrollTo(grid.model().index(row))
    gallery.colour.setCurrentText("Purple")
    gallery.search.setText("fans")
    gallery.search.clear()
    monkeypatch.setattr("studio.pages.sleeves_gallery.random.randrange", lambda count: count - 1)
    gallery.surprise.click()
    assert grid.hasFocus() and page.pending["mine"] == gallery.visible[-1]

    chosen = page.pending["mine"]
    qtbot.waitUntil(lambda: gallery.measuring is None)
    selected = grid.currentIndex()
    assert selected.isValid() and gallery.visible[selected.row()] == chosen

    def painted() -> bool:
        pix = selected.data(Qt.ItemDataRole.DecorationRole)
        return (
            isinstance(pix, QPixmap)
            and not pix.isNull()
            and grid.viewport().rect().contains(grid.visualRect(selected))
        )

    qtbot.waitUntil(painted)
    assert requests and all(thread != threading.get_ident() and small for thread, small in requests)
    assert len(grid.tiles.pending) <= 4
    rect = grid.visualRect(selected)
    assert grid.viewport().rect().contains(rect)
    box = rect.adjusted(t.XS + t.SM, t.XS + t.MD, -t.XS - t.SM, -t.XS - t.HUGE)
    pix = selected.data(Qt.ItemDataRole.DecorationRole)
    target = QRect(QPoint(), pix.size().scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio))
    target.moveCenter(box.center())
    painted_tile = grid.viewport().grab(target).toImage()
    expected = pix.toImage().scaled(
        target.size(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    # Compare actual painted artwork, including the interior where a placeholder icon would sit.
    samples = [
        (x, y)
        for x in range(3, expected.width() - 3, 7)
        for y in range(3, expected.height() - 3, 7)
    ]
    assert (
        sum(
            abs(a - b)
            for x, y in samples
            for a, b in zip(
                painted_tile.pixelColor(x, y).getRgb()[:3],
                expected.pixelColor(x, y).getRgb()[:3],
                strict=True,
            )
        )
        / (len(samples) * 3)
        < 8
    )
    qtbot.keyClick(grid, Qt.Key.Key_Up)
    above = grid.currentIndex()
    assert above.row() < selected.row() - 1
    qtbot.keyClick(grid, Qt.Key.Key_Right)
    assert grid.currentIndex().row() == above.row() + 1
    qtbot.keyClick(grid, Qt.Key.Key_Return)
    assert page.pending["mine"] == gallery.visible[above.row() + 1]


def test_compact_designer_reveals_the_ink_being_edited(desk: tuple, qtbot: QtBot) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QScrollArea

    window, page = desk
    window.resize(960, 600)
    page.gallery.actions["design"].click()
    inks = page.editor.inks
    inks.buttons[2].click()
    slider = inks.sliders[0]
    viewport = page.editor.panel.findChild(QScrollArea).viewport()
    qtbot.waitUntil(
        lambda: (
            slider.hasFocus()
            and viewport.rect().contains(
                slider.mapTo(viewport, QPoint(slider.width() // 2, slider.height() // 2))
            )
        )
    )
    window.resize(2560, 1440)
    assert page.editor.panel.width() <= 616


def test_updated_pack_keeps_slot_identity_and_remeasures_colour(
    desk: tuple, qtbot: QtBot, tmp_path: Path
) -> None:
    from dataclasses import replace

    from content.media import Item, Origin

    _, page = desk
    path = tmp_path / "Ink.png"
    image = QImage(32, 48, QImage.Format.Format_RGB32)
    origin = Origin(
        "ink", "1.0.0", "a" * 64, "Studio", "Demo", "Studio ink", "https://example.invalid/ink"
    )
    item = Item("images/Ink.png", "Single ink", "sleeve")
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(path))
    page.library.replace_pack(origin, [(item, path)])
    page.refresh()
    ready(qtbot, page)
    page.gallery.source.setCurrentText("Pack sleeve")
    page.gallery.colour.setCurrentText("Blue")
    qtbot.waitUntil(lambda: len(page.gallery.visible) == 1)
    key = page.gallery.visible[0].key
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path))
    page.library.replace_pack(replace(origin, version="2.0.0", sha256="b" * 64), [(item, path)])
    page.refresh()
    ready(qtbot, page)
    qtbot.waitUntil(lambda: page.gallery.measured.get(key) == "Red")
    assert not page.gallery.visible
    page.gallery.colour.setCurrentText("Red")
    assert [c.key for c in page.gallery.visible] == [key]


def test_crop_uses_loaded_image(
    desk: tuple, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from sleeves import imaging

    _, page = desk
    picture = tmp_path / "flower.png"
    image = QImage(300, 400, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(picture))
    page.gallery.actions["image"].click()
    editor = page.editor
    editor.load(lambda: picture, "Flower")
    qtbot.waitUntil(lambda: editor.valid)
    decoder = Mock(wraps=imaging.decode)
    monkeypatch.setattr(imaging, "decode", decoder)
    editor.mode.setCurrentText("Fill sleeve")
    editor.zoom.setValue(150)
    qtbot.waitUntil(lambda: editor.valid)
    decoder.assert_not_called()


def test_unsaved_slot_choices_guard_navigation(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    pick(page, qtbot, "Vortex")
    monkeypatch.setattr(page, "confirm", lambda *_: False)
    assert not window.navigate("cards")
    page.escape()
    assert page.pending
    monkeypatch.setattr(page, "confirm", lambda *_: True)
    assert window.navigate("cards") and not page.pending


def test_download_art_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sleeves import cards
    from tools.sleeve_contact_sheet import stand_in

    cache = cards.CardArt(tmp_path)
    cache._dir.mkdir()
    first, second = (cache._dir / (str(i) + ".jpg") for i in (1, 2))
    for file in (first, second):
        assert stand_in().save(str(file))
    monkeypatch.setattr(cards, "ART_CACHE_BYTES", first.stat().st_size, raising=False)
    assert cache._fetch(2) == second
    assert not first.exists() and second.is_file()


def test_metadata_save_preserves_other_library_edits(desk: tuple, qtbot: QtBot) -> None:
    from sleeves import Library

    _, page = desk
    pick(page, qtbot, "Vortex")
    page.gallery.actions["manage"].click()
    editor = page.editor
    key = editor.choices[editor.listing.currentIndex()].entry.id
    Library(page.library.root).edit(key, tags=["kept"])
    editor.name.setText("Renamed")
    qtbot.waitUntil(lambda: editor.valid)
    editor.save.click()
    ready(qtbot, page)
    entry = next(e for e in page.library.entries() if e.id == key)
    assert entry.name == "Renamed" and entry.tags == ("kept",)


def test_oversized_sleeve_photo_rejected_before_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sleeves import imaging

    picture = tmp_path / "large.png"
    picture.write_bytes(b"12345")
    monkeypatch.setattr(imaging, "MAX_BYTES", 4)

    def unexpected(*args: object) -> None:
        pytest.fail("Oversized source opened for a payload read")

    monkeypatch.setattr(Path, "open", unexpected)
    with pytest.raises(ValueError, match="smaller"):
        imaging.read_source(picture)


def test_editor_only_offers_to_save_its_draft(desk: tuple, qtbot: QtBot) -> None:
    _, page = desk
    pick(page, qtbot, "Vortex")
    page.gallery.actions["design"].click()
    qtbot.waitUntil(lambda: page.editor.valid)
    assert page.editor.save.isVisible() and not page.apply.isVisible()
    page.editor.back_button.click()
    assert page.apply.isVisible() and page.pending


def test_first_visit_fits_compact_slot_headings(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.resize(960, 600)
    window.navigate("sleeves")
    page = window.current
    ready(qtbot, page)
    label = page.banner.slots["opponent"].heading
    assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
    prompt = next(
        w for w in page.banner.findChildren(QLabel) if w.text().startswith("Choose a sleeve")
    )
    assert prompt.height() >= prompt.heightForWidth(prompt.width())


def test_crop_uses_final_pointer_position(qtbot: QtBot) -> None:
    from studio.pages.sleeves_widgets import Preview

    widget = Preview()
    qtbot.addWidget(widget)
    widget.set_image(QImage(100, 140, QImage.Format.Format_RGB32))
    widget.set_draggable(True)
    widget.show()
    widget.repaint()
    start = widget.rect().center()
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    with qtbot.waitSignal(widget.moved, timeout=400):
        qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=start + QPoint(20, 0))


def test_combinable_facets_counts_and_range_first_order(desk: tuple) -> None:
    from sleeves.catalog import PALETTE_FAMILIES

    gallery = desk[1].gallery
    gallery.source.setCurrentText("Ready-made")
    families = {spec.family for spec in PATTERNS.values()}
    assert {item.motif for item in gallery.visible[: len(families)]} == families
    assert {item.recipe.pattern for item in gallery.visible[: len(PATTERNS)]} == set(PATTERNS)
    group = "Ancient motifs"
    gallery.family.setCurrentText(group)
    gallery.colour.setCurrentText("Gold")
    patterns = {key for key, spec in PATTERNS.items() if spec.family == group}
    gold = {key for key, family in PALETTE_FAMILIES.items() if family == "Gold"}
    assert {(c.recipe.pattern, c.palette) for c in gallery.visible} == {
        (pattern, palette) for pattern in patterns for palette in gold
    }
    assert gallery.family.currentData() == gallery.colour.currentData() == len(patterns) * len(gold)
    assert gallery.family.itemData(0) == len(PATTERNS) * len(gold)
    assert gallery.colour.itemData(0) == len(patterns) * len(PALETTES)
    slot = gallery.visible[0]
    gallery.search.setText(f"{slot.name} {slot.palette}")
    assert gallery.visible == [slot]


@pytest.mark.parametrize(("team", "target"), [("Saltire", "mine"), ("Relay", "opponent")])
def test_related_prints_and_matching_pair(
    desk: tuple, qtbot: QtBot, team: str, target: str
) -> None:
    from dataclasses import replace

    from studio.pages.sleeves_collection import saved

    _, page = desk
    page.select_slot(target)
    pick(page, qtbot, "Glacier", "Ready-made")
    pair_action = next(
        a for a in page.similar.menu().actions() if a.text() == f"Match {team} home and away"
    )
    pair_action.trigger()
    assert {c.palette for c in page.pending.values()} == {team + " home", team + " away"}
    gallery = page.gallery
    pick(page, qtbot, team + " home", "Ready-made")
    selected = page.pending[target]
    page.by_pattern.trigger()
    assert {c.palette for c in gallery.visible} == set(PALETTES) - {selected.palette}
    assert all(c.recipe.pattern == selected.recipe.pattern for c in gallery.visible)
    page.by_palette.trigger()
    assert {c.recipe.pattern for c in gallery.visible} == set(PATTERNS) - {selected.recipe.pattern}
    assert all(c.palette == selected.palette for c in gallery.visible)
    # A matching away jersey preserves the player's frame and seed, too.
    selected = saved(page.library.add_design("Home", replace(selected.recipe, seed=17)))
    page.stage(selected)
    page.pair.click()
    partner = page.pending["opponent" if target == "mine" else "mine"]
    assert partner.recipe == replace(selected.recipe, palette=PALETTES[team + " away"])
    assert page.pending[target] == selected
    page.apply.click()
    ready(qtbot, page)
    assert all(s.status.matches for s in page.snapshot.slots.values())


def test_designer_visual_groups_keyboard_and_back(desk: tuple, qtbot: QtBot) -> None:
    from sleeves.catalog import PALETTE_FAMILIES

    window, page = desk
    window.resize(960, 600)
    page.gallery.actions["design"].click()
    editor = page.editor
    editor.pattern.showPopup()
    picker = editor.picker
    assert set(picker.keys) == set(PATTERNS)
    picker.group.setCurrentText("Ancient motifs")
    assert set(picker.keys) == {key for key, p in PATTERNS.items() if p.family == "Ancient motifs"}
    idx = picker.grid.model().index(0, 0)
    picker.grid.setCurrentIndex(idx)
    qtbot.waitUntil(lambda: idx.data(Qt.ItemDataRole.DecorationRole) is not None)
    pattern = picker.keys[0]
    qtbot.keyClick(picker.grid, Qt.Key.Key_Return)
    assert picker.isHidden() and editor.pattern.currentData() == pattern
    editor.palette.showPopup()
    assert set(picker.keys) == set(PALETTES)
    picker.group.setCurrentText("Blue")
    assert set(picker.keys) == {name for name, group in PALETTE_FAMILIES.items() if group == "Blue"}
    recipe = editor.recipe()
    assert editor.save.isVisible() and window.rect().contains(editor.save.mapTo(window, QPoint()))
    assert page.escape()
    assert picker.isHidden() and editor.recipe() == recipe


def test_designer_can_edit_every_catalogue_palette(desk: tuple, qtbot: QtBot) -> None:
    from sleeves.designer import inks

    _, page = desk
    page.gallery.actions["design"].click()
    editor = page.editor
    for name, pigments in PALETTES.items():
        editor.palette.setCurrentText(name)
        assert editor.recipe().palette == pigments
        assert [b.toolTip().lower() for b in editor.inks.buttons] == [
            color.name() for color in inks(pigments)
        ]
    editor.palette.setCurrentText("Saltire home")
    qtbot.waitUntil(lambda: editor.valid)
    editor.save.click()
    ready(qtbot, page)
    selected = page.pending["mine"]
    assert selected.palette == "Saltire home" and page.pair.isVisible()
    page.gallery.actions["design"].click()
    editor = page.editor
    editor.inks.buttons[-1].click()
    slider = editor.inks.sliders[-1]
    slider.setValue((slider.value() + 90) % 256)
    assert editor.palette.currentIndex() == -1
    assert len(editor.recipe().palette) == len(editor.inks.buttons)
