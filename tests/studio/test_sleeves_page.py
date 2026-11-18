import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog
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
    assert len({c.key for c in prints}) == len(PATTERNS) * len(PALETTES) == 384
    assert len(filtered(prints, "sol invictus", "Ready-made", "Green", {})) == 4
    assert not filtered(prints, "", "Saved sleeves", "All colours", {})
    for size in ((622, 622), (1920, 240), (240, 900), (1, 1)):
        for centre in ((0, 0), (0.5, 0.5), (1, 1)):
            x, y, w, h = crop_box(size, centre, 2)
            assert 0 <= x <= size[0] - w and 0 <= y <= size[1] - h
            assert w / h == pytest.approx(708 / 1016)


def test_slots_apply_undo_recent_and_conflicts(
    desk: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = desk
    textures = Path(page.install.path) / "textures"
    textures.mkdir()
    old = page.library.image_path(page.snapshot.items[0].entry.id).read_bytes()
    mine, opponent = textures / "cover.png", textures / "cover2.png"
    mine.write_bytes(old)
    assert len(page.gallery.visible) == 386
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
    monkeypatch.setattr(window, "confirm", lambda *_: True)
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
    assert page.apply.text() == "Reapply"
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
    page.apply.click()
    assert not window.navigate("music") and not window.change.isEnabled()
    assert not page.hidden() and not page.request_close()
    assert "paused" in page.status.text()
    gate.set()
    qtbot.waitUntil(lambda: not page.writing)
    assert "Close EDOPro" in page.status.text() and page.retry.isVisible()
    assert not (Path(page.install.path) / "textures/cover.png").exists()
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    page.retry.click()
    ready(qtbot, page)
    assert page.snapshot.slots["mine"].status.matches
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
    page.gallery.source.setCurrentText("Favourites")
    assert [c.name for c in page.gallery.visible] == ["Frost"]
    page.gallery.surprise.click()
    monkeypatch.setattr(window, "confirm", lambda *_: True)
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
    editor.search.setText("Studio card 1")
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
    point = editor.preview.rect().center()
    from PySide6.QtCore import QPoint

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
        assert "read-only" in page.status.text()
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
    page.gallery.actions["manage"].click()
    editor = page.editor
    qtbot.waitUntil(lambda: editor.valid and page.slide.currentTime() == page.slide.duration())
    assert window.width() == size[0] and window.height() == size[1]
    for widget in (
        editor.group,
        editor.name,
        editor.save,
        page.undo,
        page.apply,
        *page.banner.slots.values(),
    ):
        assert window.rect().contains(QRect(widget.mapTo(window, QPoint()), widget.size()))
    for button in page.banner.slots.values():
        assert button.heading.width() >= button.heading.fontMetrics().horizontalAdvance(
            button.heading.text()
        )


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
