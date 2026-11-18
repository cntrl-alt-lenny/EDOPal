import asyncio
import hashlib
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

import extras
import sleeves
import themes
from content import catalogue, download
from content.manifest import Manifest, Pack
from music import settings
from studio.context import Installation
from studio.pages.skins import SkinsPage
from studio.pages.skins_model import Look, filter_looks, preset_id, surprise
from studio.shell import StudioWindow
from themes import PRESETS
from themes.transaction import Store
from tools.studio_skins_screenshots import sample_pack


@pytest.fixture
def page(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SkinsPage]:
    window = make_window()
    monkeypatch.setattr(settings, "_game_running", lambda _: False)
    monkeypatch.setattr(window.pages["skins"], "confirm", lambda *_: True)
    window.nav["skins"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.data is not None)
    yield page
    qtbot.waitUntil(lambda: not page.job)
    window.navigate("cards")
    qtbot.waitUntil(lambda: not page.ctx.tasks.jobs)


def select(page: SkinsPage, qtbot: QtBot, name: str) -> None:
    page.gallery.search.setText(name)
    grid = page.gallery.grid
    idx = grid.model().index(0)
    grid.setCurrentIndex(idx)
    grid.setFocus()
    qtbot.keyClick(grid, Qt.Key.Key_Return)
    assert page.look.name == name


def saved(page: SkinsPage, qtbot: QtBot, name: str) -> None:
    qtbot.waitUntil(lambda: page.mode == "detail" and page.look.name == name and not page.job)


def test_model_filters_and_palette_only_surprise() -> None:
    looks = [Look(preset_id(p), p, "Preset") for p in themes.PRESETS]
    light = filter_looks(looks, "Light", "")
    dark = filter_looks(looks, "Dark", "")
    assert light and dark and len(light) + len(dark) == len(PRESETS)
    assert filter_looks(light, "All", "  pOrCeLaIn ")[0].name == "Porcelain Blue"
    original = replace(themes.PRESETS[0], text="#123456", depth=99)
    changed = surprise(original)
    assert original.accent != changed.accent
    assert changed == replace(
        original, background=changed.background, surface=changed.surface, accent=changed.accent
    )


def test_deleted_library_copy_keeps_history_in_another_game(
    page: SkinsPage, qtbot: QtBot, tmp_path: Path
) -> None:
    first = page.ctx.installation
    theme = replace(themes.PRESETS[0], name="Portable ocean", wallpaper="inherit")
    key = page.store.library.save(theme)
    themes.install_theme(page.game, key, theme)
    other = tmp_path / "Second game"
    other.mkdir()
    page.ctx.set_installation(Installation(str(other), True))
    qtbot.waitUntil(lambda: page.data is not None)
    select(page, qtbot, theme.name)
    assert not page.look.active
    page.view.inspector.tools["Delete"].click()
    qtbot.waitUntil(lambda: key not in page.store.library.entries() and not page.job)
    page.ctx.set_installation(first)
    qtbot.waitUntil(lambda: page.data is not None)
    select(page, qtbot, theme.name)
    assert page.look.active and page.look.group == "Installed"
    page.view.inspector.remove.click()
    qtbot.waitUntil(lambda: page.mode == "gallery" and not page.job)
    assert all(look.key != key for look in page.data.looks)


def test_invalid_catalogue_keeps_local_looks(
    page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        catalogue, "fetch_catalogue", AsyncMock(side_effect=ValueError("Invalid catalog URL"))
    )
    page.gallery.refresh.click()
    qtbot.waitUntil(
        lambda: (
            "Offline catalog" in page.gallery.info.text()
            and "Invalid catalog URL" in page.gallery.info.toolTip()
        )
    )
    assert page.gallery.grid.model().rowCount() == len(PRESETS)


def test_gallery_keyboard_empty_and_game_changes(page: SkinsPage, qtbot: QtBot) -> None:
    assert page.gallery.grid.model().rowCount() == len(PRESETS)
    page.gallery.filters.buttons["My themes"].click()
    assert page.gallery.empty.isVisible()
    page.gallery.filters.buttons["Light"].click()
    select(page, qtbot, "Porcelain Blue")
    page.view.preview.scenes.buttons["Duel field"].click()
    page.view.preview.turn.click()
    qtbot.waitUntil(
        lambda: page.view.preview.completed is not None and page.view.preview.completed.opponent
    )
    assert page.view.preview.completed.scene == "duel"
    qtbot.keyClick(page.view.back, Qt.Key.Key_Escape)
    assert page.mode == "gallery"
    game = page.game
    page.ctx.set_installation(Installation())
    qtbot.waitUntil(lambda: page.data is not None)
    assert "Choose your EDOPro folder" in page.install_hint.text()
    select(page, qtbot, "Porcelain Blue")
    assert not page.view.inspector.apply.isEnabled()
    page.ctx.set_installation(Installation(str(game)))
    qtbot.waitUntil(lambda: page.data is not None)
    assert "Finish setting up EDOPro" in page.install_hint.text()
    select(page, qtbot, "Porcelain Blue")
    assert not page.view.inspector.apply.isEnabled()


def test_maker_edit_and_share(
    page: SkinsPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    select(page, qtbot, "Deep Current")
    ui = page.maker
    page.view.inspector.edit.click()
    ui.surprise.click()
    assert page.dirty
    monkeypatch.setattr(page, "confirm", lambda *_: False)
    assert not page.ctx.navigate("music")
    page.view.back.click()
    assert page.mode == "maker" and "UNSAVED" in page.view.kicker.text()
    ui.fine.click()
    ui.swatches["danger"].click()
    ui.advanced_mixer.hex.setText("#ED8888")
    qtbot.keyClick(ui.advanced_mixer.hex, Qt.Key.Key_Return)
    assert ui.theme.danger == "#ed8888"
    ui.resets["danger"].click()
    assert ui.theme.danger is None
    ui.ranges["depth"].setValue(73)
    ui.ranges["roundness"].setValue(2)
    ui.font.click()
    ui.highlight.click()
    ui.styles["geometry"].click()
    ui.inherit.click()
    picture = page.game / "pics/1001.jpg"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(picture), ""))
    ui.photo.click()
    qtbot.waitUntil(lambda: ui.theme.wallpaper == "image")
    ui.name.setText("My/photo")
    ui.save_btn.click()
    assert page.mode == "maker" and "name" in page.message.text()
    ui.name.setText("My photo")
    ui.save_btn.click()
    saved(page, qtbot, "My photo")
    assert page.message.text() == "Theme saved in My themes."
    original = page.look
    assert original.theme.image and original.theme.font == "inter" and original.theme.depth == 73
    assert original.theme.roundness == 2 and not original.theme.highlight_turn
    inspector = page.view.inspector
    inspector.tools["Rename"].click()
    page.view.form_name.setText("Copper")
    page.view.submit.click()
    saved(page, qtbot, "Copper")
    assert page.look.key == original.key
    inspector.tools["Duplicate"].click()
    page.view.submit.click()
    saved(page, qtbot, "Copper copy")
    assert page.look.key != original.key
    inspector.edit.click()
    ui.copy_btn.click()
    page.view.form_name.setText("Platinum")
    page.view.submit.click()
    saved(page, qtbot, "Platinum")
    photo_theme = page.look
    share = tmp_path / "portable.edotheme"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(share), ""))
    inspector.tools["Export"].click()
    qtbot.waitUntil(lambda: share.is_file() and not page.job)
    page.view.back.click()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(share), ""))
    page.gallery.import_file.click()
    saved(page, qtbot, "Platinum")
    imported = page.look
    assert imported.key != photo_theme.key and imported.theme == photo_theme.theme
    monkeypatch.setattr(page, "confirm", lambda *_: True)
    inspector.tools["Delete"].click()
    qtbot.waitUntil(lambda: page.retry_button.text() == "Undo delete" and page.mode == "gallery")
    assert imported.key not in page.store.library.entries()
    page.retry_button.click()
    saved(page, qtbot, "Platinum")
    assert page.look.key == imported.key


def test_install_retry_sleeves_and_three_recoveries(
    page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = page.game
    conf = game / "config/system.conf"
    conf.parent.mkdir()
    conf.write_bytes(b"skin = Old\nvolume = 9\n")
    lib = sleeves.Library(page.store.root / "sleeves")
    entry = lib.add_image("My sleeve", game / "pics/1001.jpg")
    sleeves.apply(lib, game, page.store.config, mine=entry.id)
    select(page, qtbot, "Deep Current")
    info = page.view.inspector
    assert not info.back.isVisible() and not info.remove.isVisible() and not info.undo.isVisible()
    monkeypatch.setattr(settings, "_game_running", lambda _: True)
    info.apply.click()
    qtbot.waitUntil(lambda: "Close EDOPro to continue" in page.message.text() and page.job is None)
    assert extras.current_skin(str(game)) == "Old"

    main_thread = get_ident()
    release = Event()

    def check(_: Path) -> bool:
        assert get_ident() != main_thread
        assert release.wait(4)
        return False

    monkeypatch.setattr(settings, "_game_running", check)
    page.retry_button.click()
    assert page.ctx.busy and not page.window().change.isEnabled()
    assert not page.ctx.navigate("music") and not page.request_close()
    release.set()
    qtbot.waitUntil(lambda: page.look.active, timeout=10000)
    assert all(btn.isVisible() for btn in (info.back, info.remove, info.undo))
    assert sleeves.status(lib, game)["mine"].matches
    assert "sleeves carry over" in page.message.text()
    info.back.click()
    qtbot.waitUntil(lambda: not page.look.active)
    assert extras.current_skin(str(game)) == "Old"
    assert not info.back.isVisible() and info.undo.isVisible()
    info.undo.click()
    qtbot.waitUntil(lambda: not page.look.state.folder)
    assert not info.undo.isVisible()
    info.apply.click()
    qtbot.waitUntil(lambda: page.look.active)
    info.remove.click()
    qtbot.waitUntil(lambda: not page.look.state.folder)
    assert extras.current_skin(str(game)) == "Old" and "volume = 9" in conf.read_text()
    assert sleeves.status(lib, game)["mine"].matches


def test_recover_on_entry(page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    game = page.game
    assert page.ctx.navigate("music")
    key = preset_id(themes.PRESETS[0])
    with monkeypatch.context() as patch:
        patch.setattr(Store, "finish", Mock(side_effect=OSError("interrupted")))
        with pytest.raises(OSError, match="interrupted"):
            themes.install_theme(game, key, replace(themes.PRESETS[0], wallpaper="inherit"))
    assert themes.install_state(game, key).recovery_pending
    monkeypatch.setattr(settings, "_game_running", lambda _: True)
    assert page.ctx.navigate("skins")
    qtbot.waitUntil(lambda: "Close EDOPro to continue" in page.message.text())
    assert themes.install_state(game, key).recovery_pending
    monkeypatch.setattr(settings, "_game_running", lambda _: False)
    page.retry_button.click()
    qtbot.waitUntil(lambda: "Finished installing" in page.message.text())
    assert (
        not themes.install_state(game, key).recovery_pending
        and themes.install_state(game, key).active
    )


def test_import_and_collection_errors_stay_recoverable(
    page: SkinsPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = tmp_path / "broken.edotheme"
    invalid.write_bytes(b"oops")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(invalid), ""))
    page.gallery.import_file.click()
    qtbot.waitUntil(lambda: not page.job and page.message_bar.isVisible())
    assert not page.store.library.entries()
    with monkeypatch.context() as patch:
        patch.setattr(
            page.store.library, "entries", Mock(side_effect=OSError("Storage unavailable"))
        )
        page.reload()
        qtbot.waitUntil(
            lambda: "Storage unavailable" in page.details.toolTip() and page.details.isVisible()
        )
    page.retry_button.click()
    qtbot.waitUntil(lambda: page.gallery.refresh.isEnabled())
    assert len(page.data.looks) == len(PRESETS)


def test_pack_download_cancel_resume_apply_highlight_restore(
    page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def archive(*files: tuple[str, bytes]) -> bytes:
        target = BytesIO()
        with ZipFile(target, "w") as z:
            for name, content in files:
                z.writestr(name, content)
        return target.getvalue()

    path = "skin/Monokai Moon/skin.xml"
    with ZipFile(extras.PACKS / "moon.zip") as z:
        skin_cover = "skin/Monokai Moon/textures/cover.png"
        lib = sleeves.Library(page.store.root / "sleeves")
        original, chosen = lib.entries()[:2]
        skin_bytes = archive(
            (path, z.read(path)), (skin_cover, lib.image_path(original.id).read_bytes())
        )
        sleeves.apply(lib, page.game, page.store.config, mine=chosen.id)
    # The engine only accepts a shared pack that carries both Inter fonts.
    shared_bytes = archive(
        ("fonts/Inter-Medium.ttf", b"sample font"),
        ("fonts/InterEDO-Regular.ttf", b"sample font"),
    )

    def pack(title: str, body: bytes) -> Pack:
        return replace(
            sample_pack(title), sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body)
        )

    skin, shared = pack("Moon", skin_bytes), pack("Shared", shared_bytes)
    source = catalogue.Catalogue(Manifest(1, (skin, shared)), catalogue.CatalogueSource.LIVE)
    monkeypatch.setattr(catalogue, "fetch_catalogue", AsyncMock(return_value=source))
    hold = True

    async def stream(
        session: object,
        transfer: download._Transfer,
        partial: Path,
        offset: int,
        progress: download.ProgressCallback,
        cancel: Event,
        timeout: float,
    ) -> int:
        body = skin_bytes if transfer.sha256 == skin.sha256 else shared_bytes
        progress(download.Progress(len(body) // 2, len(body), 1024))
        while hold:
            await asyncio.sleep(0.01)
        partial.write_bytes(body)
        return len(body)

    monkeypatch.setattr(download, "create_http_session", lambda: nullcontext())
    monkeypatch.setattr(download, "_stream", stream)
    page.gallery.filters.buttons["Downloadable"].click()
    qtbot.waitUntil(lambda: page.gallery.grid.model().rowCount() == 1)
    select(page, qtbot, "Moon")
    info = page.view.inspector
    assert "License" in info.description.text()
    assert info.apply.text() == "Download skin"
    info.highlight.click()
    info.apply.click()
    qtbot.waitUntil(lambda: page.view.progress.percent.text() != "")
    assert extras.current_skin(str(page.game)) == ""
    page.view.progress.cancel.click()
    qtbot.waitUntil(lambda: page.retry_button.text() == "Resume" and not page.job)
    hold = False
    page.retry_button.click()
    qtbot.waitUntil(lambda: page.look.availability == "Downloaded")
    assert info.highlight.isChecked()
    info.apply.click()
    qtbot.waitUntil(lambda: page.look.active)
    assert extras.current_skin(str(page.game)) == "Monokai Moon"
    assert (page.game / path).exists() and info.restore.isVisible()
    assert (page.game / skin_cover).read_bytes() == lib.image_path(chosen.id).read_bytes()
    assert sleeves.status(lib, page.game)["mine"].matches
    assert page.data.highlights["Moon"]
    info.highlight.click()
    info.apply.click()
    qtbot.waitUntil(lambda: not page.data.highlights["Moon"])
    page.store.cache.clear()
    info.restore.click()
    qtbot.waitUntil(lambda: not page.look.active and not page.data.restore_pack)
    assert extras.current_skin(str(page.game)) == ""


@pytest.mark.parametrize(
    ("dependency", "state"), [(None, "Shared fonts unavailable"), ("99.0.0", "Needs a newer app")]
)
def test_pack_dependency_gates_action(
    page: SkinsPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    dependency: str | None,
    state: str,
) -> None:
    packs = (sample_pack("Ristretto"),)
    if dependency:
        packs += (replace(sample_pack("Shared"), min_app_version=dependency),)
    source = catalogue.Catalogue(Manifest(1, packs), catalogue.CatalogueSource.CACHED, "Offline")
    monkeypatch.setattr(catalogue, "fetch_catalogue", AsyncMock(return_value=source))
    page.gallery.filters.buttons["Downloadable"].click()
    qtbot.waitUntil(lambda: page.gallery.grid.model().rowCount() == 1)
    assert "offline" in page.gallery.info.text().lower()
    select(page, qtbot, "Ristretto")
    assert state in page.view.inspector.hint.text()
    assert not page.view.inspector.apply.isEnabled()
    page.view.inspector.edit.click()
    assert page.mode == "maker" and page.maker.name.text() == "Ristretto remix"


def test_dirty_maker_survives_every_exit_and_async_folder_change(
    page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = page.window()
    page.gallery.create.click()
    page.maker.name.setText("Keep this draft")
    confirm = Mock(return_value=False)
    monkeypatch.setattr(page, "confirm", confirm)
    for leave in (
        page.view.back.click,
        lambda: qtbot.keyClick(page.maker.name, Qt.Key.Key_Escape),
        window.nav["sleeves"].click,
        window.change.click,
        window.close,
        lambda: page.ctx.set_installation(Installation()),
    ):
        before = confirm.call_count
        leave()
        assert confirm.call_count == before + 1
        assert window.isVisible() and window.current is page and not window.picker.isVisible()
        assert page.mode == "maker" and page.maker.name.text() == "Keep this draft"
    confirm.return_value = True
    page.view.back.click()
    assert page.mode == "gallery"


@pytest.mark.parametrize("size", [(960, 600), (1280, 800), (1600, 1000), (2560, 1440)])
def test_fine_tuning_fits_beside_preview(
    page: SkinsPage, qtbot: QtBot, size: tuple[int, int]
) -> None:
    from PySide6.QtCore import QPoint, QRect

    window, view = page.window(), page.view
    window.resize(*size)
    page.gallery.create.click()
    page.maker.fine.click()
    page.maker.swatches["opponent_lp"].click()
    qtbot.waitUntil(
        lambda: (
            view.preview.completed is not None
            and page.motion.currentTime() == page.motion.duration()
        )
    )
    assert window.size().toTuple() == size
    canvas = view.preview.canvas
    assert canvas.width() >= 300 and canvas.height() >= 210
    assert not canvas.pix.isNull()
    view.scroll.ensureWidgetVisible(page.maker.resets["opponent_lp"])
    button = page.maker.resets["opponent_lp"]
    assert (
        view.scroll.viewport()
        .rect()
        .contains(QRect(button.mapTo(view.scroll.viewport(), QPoint()), button.size()))
    )
    assert view.scroll.horizontalScrollBar().maximum() == 0
    assert not view.scroll.geometry().intersects(view.preview.geometry())


def test_retry_only_reapplies_sleeves_after_successful_theme_change(
    page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    select(page, qtbot, "Deep Current")
    lib = sleeves.Library(page.store.root / "sleeves")
    entry = lib.add_image("Lake sleeve", page.game / "pics/1001.jpg")
    sleeves.apply(lib, page.game, page.store.config, mine=entry.id)
    original = themes.install_theme
    install = Mock(wraps=original)
    monkeypatch.setattr(themes, "install_theme", install)
    with monkeypatch.context() as patch:
        patch.setattr(sleeves, "reapply", Mock(side_effect=OSError("Sleeve file in use")))
        page.view.inspector.apply.click()
        qtbot.waitUntil(lambda: page.look.active and "Sleeve file in use" in page.message.text())
    page.retry_button.click()
    qtbot.waitUntil(lambda: "back in game" in page.message.text())
    assert sleeves.status(lib, page.game)["mine"].matches
    assert install.call_count == 1


def test_two_pages_share_effective_sleeves_and_active_look(page: SkinsPage, qtbot: QtBot) -> None:
    window = page.window()
    window.navigate("sleeves")
    desk = window.current
    qtbot.waitUntil(lambda: desk.snapshot is not None and not desk.loading)
    desk.gallery.search.setText("Vortex")
    desk.gallery.choose(desk.gallery.grid.model().index(0))
    desk.same.click()
    desk.apply.click()
    qtbot.waitUntil(lambda: not desk.writing and not desk.loading)
    window.navigate("skins")
    qtbot.waitUntil(lambda: "Opponent · Vortex" in page.gallery.info.text())
    select(page, qtbot, "Deep Current")
    page.view.inspector.apply.click()
    qtbot.waitUntil(lambda: page.look.active)
    window.navigate("sleeves")
    qtbot.waitUntil(lambda: "Deep Current" in desk.status.text())
    assert all(slot.state.text() == "Matches game" for slot in desk.banner.slots.values())
    desk.gallery.search.setText("1998 OCG")
    desk.banner.slots["opponent"].click()
    desk.gallery.choose(desk.gallery.grid.model().index(0))
    desk.apply.click()
    qtbot.waitUntil(lambda: not desk.writing and not desk.loading)
    assert desk.snapshot.slots["opponent"].choice.name == "1998 OCG"
    assert desk.snapshot.slots["mine"].choice.name == "Vortex"
    window.navigate("skins")
    qtbot.waitUntil(lambda: "Deep Current" in page.gallery.info.text())
    assert "Your sleeve · Vortex" in page.gallery.info.text()
    assert "Opponent · 1998 OCG" in page.gallery.info.text()


def test_missing_game_and_unreadable_sleeves_leave_the_gallery_usable(
    page: SkinsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lib = sleeves.Library(page.store.root / "sleeves")
    lib.entries()
    monkeypatch.setattr(sleeves, "status", Mock(side_effect=ValueError("Unreadable library")))
    page.reload()
    qtbot.waitUntil(lambda: "Could not read assigned sleeves" in page.gallery.info.text())
    assert len(page.data.looks) == len(PRESETS)
    missing = tmp_path / "Disconnected game"
    page.ctx.set_installation(Installation(str(missing), True))
    qtbot.waitUntil(lambda: page.data is not None)
    select(page, qtbot, "Deep Current")
    page.view.inspector.apply.click()
    qtbot.waitUntil(lambda: page.retry_button.isVisible() and not page.job)
    assert "no longer ready" in page.message.text() and not missing.exists()


def test_unreadable_entries_leave_valid_themes_visible(page: SkinsPage, qtbot: QtBot) -> None:
    lib = page.store.library
    key = lib.save(themes.PRESETS[0])
    invalid = lib.directory / (key + " (incoming).json")
    invalid.write_bytes(b"invalid")
    assert key in lib.entries()
    page.reload()
    qtbot.waitUntil(lambda: invalid.name in page.message.text())
    assert key in {v.key for v in page.data.looks}
    assert page.retry_button.text() == "Set aside file"
    page.retry_button.click()
    qtbot.waitUntil(lambda: not invalid.exists() and not page.job)
    assert any(f.read_bytes() == b"invalid" for f in (lib.directory / "quarantine").iterdir())


def test_removed_recipe_cannot_duplicate_an_installed_preset(page: SkinsPage, qtbot: QtBot) -> None:
    preset = page.data.looks[0]
    themes.install_theme(page.game, preset.key, preset.theme)
    page.store.library.save(preset.theme, preset.key)
    page.store.library.delete(preset.key)
    page.reload()
    qtbot.waitUntil(page.gallery.refresh.isEnabled)
    assert sum(look.key == preset.key for look in page.data.looks) == 1
    assert any(issue.filename == f"removed/{preset.key}.json" for issue in page.data.issues)


def test_appearance_pages_start_light(
    make_window: Callable[..., StudioWindow],
    qtbot: QtBot,
) -> None:
    from PySide6.QtWidgets import QWidget

    window = make_window()
    for section in ("skins", "sleeves"):
        page = window.pages[section]
        assert len(page.findChildren(QWidget)) < 10


@pytest.mark.parametrize("section", ("skins", "sleeves"))
def test_filter_reuses_thumbnail(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, section: str
) -> None:
    window = make_window()
    page = window.pages[section]
    window.navigate(section)
    gallery = page.gallery
    grid = gallery.grid
    qtbot.waitUntil(lambda: bool(grid.tiles.cache), timeout=8000)
    key, pix = next(iter(grid.tiles.cache.items()))
    row = next(i for i, v in enumerate(grid.tiles.items) if v.path == key)
    gallery.search.setText(grid.tiles.items[row].title)
    assert grid.tiles.cache[key].cacheKey() == pix.cacheKey()
    assert grid.model().index(0, 0).data(Qt.ItemDataRole.DecorationRole) is not None
    window.navigate("cards")


def test_gallery_glossary(page: SkinsPage) -> None:
    assert page.gallery.create.text() == "Create theme"
    assert page.gallery.search.placeholderText() == "Search skins and themes"
    assert "My themes" in page.gallery.filters.buttons


def test_pack_budget_precedes_network(page: SkinsPage, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.tasks import WorkerContext

    pack = replace(sample_pack("Moon"), size_bytes=2**30)
    transfer = AsyncMock()
    monkeypatch.setattr(download, "download_pack", transfer)
    with pytest.raises(ValueError):
        asyncio.run(
            page.store.download(pack, sample_pack("Shared"), WorkerContext(Event(), lambda _: None))
        )
    transfer.assert_not_awaited()


def test_stale_theme_editor_preserves_other_instance(page: SkinsPage, qtbot: QtBot) -> None:
    lib = page.store.library
    key = lib.save(replace(themes.PRESETS[0], name="Shared theme"))
    page.reload(select=key)
    qtbot.waitUntil(lambda: page.mode == "detail" and page.look.key == key)
    page.view.inspector.edit.click()
    other = themes.ThemeLibrary(lib.directory)
    published = replace(other.load(key), name="Saved elsewhere")
    other.save(published, key)
    page.maker.name.setText("My draft")
    page.maker.save_btn.click()
    qtbot.waitUntil(lambda: not page.job)
    assert lib.load(key) == published
    assert page.mode == "maker" and page.maker.name.text() == "My draft"
