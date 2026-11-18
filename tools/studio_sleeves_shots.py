"""Offline proof of the sleeve desk. All game, artwork and cache files are disposable."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog

import extras
import main
from sleeves import ArtMissing, ArtUnavailable
from studio import theme
from studio.context import AppContext, Installation
from studio.shell import StudioWindow
from tools.sleeve_contact_sheet import stand_in
from tools.studio_screenshots import DemoSync, create_demo, settle


def prepare_overview(window: StudioWindow) -> None:
    page = window.pages["sleeves"]
    settle(lambda: page.snapshot is not None and not (page.loading or page.saving))
    grid = page.gallery.grid
    if not grid.isVisible():
        return

    def visible_prints_loaded() -> bool:
        grid.viewport().repaint()
        area = grid.viewport().rect()
        return all(
            item.key in grid.tiles.cache
            for row, item in enumerate(page.gallery.visible)
            if grid.visualRect(grid.model().index(row, 0)).intersects(area)
        )

    settle(visible_prints_loaded, timeout=20)


def render(out: Path, size: tuple[int, int], root: Path) -> list[Path]:
    app = QApplication.instance()
    theme.apply(app)
    cfg = create_demo(root)
    game = Path(cfg.edopro_path)
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        db.executemany(
            "UPDATE texts SET name = ? WHERE id = ?",
            [("Moonlit Lake", 1000), ("Twilight Lookout", 1001), ("Kingfisher at Dusk", 1002)],
        )
    textures = game / "textures"
    textures.mkdir()
    with ZipFile(extras.PACKS / "shared.zip") as archive:
        for member in extras.SLEEVES.values():
            (game / member).write_bytes(archive.read(member))
    sample = root / "Evening lake.png"
    stand_in().save(str(sample))
    broken = root / "Picture.png"
    broken.write_bytes(b"broken")
    ctx = AppContext(cfg, main.VERSION, DemoSync(), main._apply_gui_choices)
    window = StudioWindow(ctx, startup=False)
    install = Installation.inspect(str(game))
    ctx.set_installation(install)
    window.resize(*size)
    window.show()
    window.navigate("sleeves")
    page = window.current
    gallery = page.gallery
    out.mkdir(parents=True, exist_ok=True)
    files = []
    release = threading.Event()

    def loaded() -> None:
        prepare_overview(window)
        settle(lambda: not ctx.tasks.jobs)

    def shot(name: str) -> None:
        window.setFocus(Qt.FocusReason.MouseFocusReason)
        window.animation.setCurrentTime(theme.MOTION_MS)
        page.slide.setCurrentTime(theme.MOTION_MS)
        app.processEvents()
        target = out / f"{name}.png"
        if not window.grab().save(str(target)):
            raise OSError(f"Could not save {target}")
        files.append(target)

    def pick(row: int) -> None:
        index = gallery.grid.model().index(row, 0)
        gallery.grid.setCurrentIndex(index)
        gallery.grid.activated.emit(index)

    try:
        loaded()
        shot("01-collection")
        pick(2)
        page.same.click()
        loaded()
        shot("02-staged-pair")
        gallery.colour.setCurrentText("Purple")
        gallery.source.setCurrentText("Ready-made")
        loaded()
        shot("03-colour-filter")
        gallery.search.setText("unmatched")
        shot("04-empty-search")
        gallery.search.clear()
        gallery.colour.setCurrentIndex(0)
        gallery.source.setCurrentIndex(0)
        gallery.actions["design"].click()
        designer = page.editor
        designer.emblem.setCurrentText("Lotus")
        designer.frame.setCurrentText("Deco")
        designer.name.setText("Lotus sun")
        settle(lambda: designer.valid)
        shot("05-designer")
        designer.inks.buttons[2].click()
        designer.inks.sliders[0].setValue(22)
        settle(lambda: designer.valid)
        shot("05b-custom-inks")
        designer.save.click()
        loaded()
        gallery.actions["manage"].click()
        manager = page.editor
        manager.tags.setText("sun, engraved")
        manager.favourite.click()
        settle(lambda: manager.valid)
        shot("06-manage")
        manager.save.click()
        loaded()
        gallery.source.setCurrentText("Favourites")
        loaded()
        shot("07-favourites")
        gallery.actions["manage"].click()
        manager = page.editor
        with patch.object(window, "confirm", return_value=True):
            manager.remove.click()
        loaded()
        gallery.actions["manage"].click()
        manager = page.editor
        manager.group.setCurrentIndex(1)
        settle(lambda: manager.valid)
        shot("08-bin")
        manager.remove.click()
        loaded()
        gallery.source.setCurrentIndex(0)
        gallery.actions["card"].click()
        editor = page.editor
        settle(lambda: editor.index is not None)
        editor.search.setText("100")
        shot("09-card-search")
        editor.search.setText("unmatched")
        shot("09b-no-card-matches")
        editor.search.setText("100")
        outcome = "load"

        async def fetch(client: object, code: int) -> Path:
            if outcome == "missing":
                raise ArtMissing()
            if outcome == "network":
                raise ArtUnavailable()
            release.wait(5)
            return sample

        with patch("sleeves.CardArt.fetch", fetch):
            editor.results.itemClicked.emit(editor.results.item(0))
            shot("10-card-loading")
            release.set()
            settle(lambda: editor.valid)
            shot("11-card-framed")
            editor.mode.setCurrentText("Fill sleeve")
            editor.zoom.setValue(130)
            editor.move_crop(-0.2, 0)
            settle(lambda: editor.valid)
            shot("12-card-crop")
            outcome = "missing"
            editor.results.itemClicked.emit(editor.results.item(1))
            settle(lambda: not editor.loading)
            shot("13-card-missing")
            outcome = "network"
            editor.results.itemClicked.emit(editor.results.item(2))
            settle(lambda: not editor.loading)
            shot("14-card-retry")
        page.escape()
        gallery.actions["image"].click()
        editor = page.editor
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(broken), "")):
            editor.pick.click()
        settle(lambda: not editor.loading)
        shot("15-import-rejected")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(sample), "")):
            editor.pick.click()
        settle(lambda: editor.valid)
        editor.zoom.setValue(140)
        editor.move_crop(0.2, 0)
        settle(lambda: editor.valid)
        shot("16a-import-crop")
        editor.mode.setCurrentText("Fit whole image")
        editor.background.setCurrentIndex(2)
        settle(lambda: editor.valid)
        shot("16-import-fit")
        editor.save.click()
        loaded()
        page.same.click()
        release.clear()

        def running(path: Path) -> bool:
            release.wait(5)
            return True

        with patch("music.settings._game_running", running):
            page.apply.click()
            shot("17-writing")
            release.set()
            settle(lambda: not page.writing)
            shot("18-close-game")
        with patch("music.settings._game_running", return_value=False):
            page.retry.click()
            settle(lambda: not page.writing)
            loaded()
            shot("19-applied")
            gallery.source.setCurrentText("Recently used")
            loaded()
            shot("19b-recently-used")
            gallery.source.setCurrentIndex(0)
            (textures / "cover.png").write_bytes(sample.read_bytes())
            window.navigate("skins")
            window.navigate("sleeves")
            loaded()
            shot("20-changed-outside")
            page.apply.click()
            settle(lambda: not page.writing)
            loaded()
            with patch.object(ctx, "confirm", return_value=True):
                page.undo.click()
            settle(lambda: not page.writing)
            loaded()
            shot("21-undo")
        ctx.set_installation(Installation())
        loaded()
        shot("22-no-installation")
        ctx.set_installation(Installation(str(game)))
        loaded()
        shot("23-waiting-for-cards")
        with patch(
            "studio.pages.sleeves_work.inspect",
            side_effect=PermissionError("Check that you can open the collection folder."),
        ):
            page.refresh()
            settle(lambda: not page.loading)
            shot("24-collection-error")
    finally:
        release.set()
        settle(lambda: not ctx.tasks.jobs)
        window.close()
        app.processEvents()
    return files
