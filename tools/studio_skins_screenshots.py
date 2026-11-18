"""Offline Skins views for the Studio screenshot gallery."""

import shutil
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from functools import cache
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFileDialog

import extras
import themes
from content import catalogue
from content.cache import file_sha256
from content.download import DownloadError, DownloadFailure, Progress
from content.manifest import Manifest, Pack, Preview
from studio.context import Installation
from studio.pages.skins import SkinsPage
from studio.pages.skins_model import preset_id
from studio.shell import StudioWindow
from studio.tasks import WorkerContext
from themes.transaction import Store


@cache
def sample_pack(name: str) -> Pack:
    archive = extras.PACKS / (name.lower() + ".zip")
    return Pack(
        name.lower(),
        # The shared fonts-and-sounds pack is published as a sound-effects pack,
        # and extras.apply_appearance only accepts it under that kind.
        "sound-effects" if name == "Shared" else "skin",
        name,
        {
            "Abyss": "Deep ocean blue with cool teal highlights on the field.",
            "Moon": "A lunar landscape framed in charcoal and violet.",
            "Nebula": "Blue starlight and a violet duel field.",
            "Ristretto": "Warm copper light and ember-coloured details.",
        }.get(name, "Shared typefaces for the game."),
        "OLED Deck EDOPro project",
        "Redistribution permissions unverified",
        "Preview captured from the OLED Deck EDOPro installation.",
        "https://github.com/cntrl-alt-lenny/edopro-hd-cards",
        "1.0.0",
        "https://example.invalid/skin",
        archive.stat().st_size,
        file_sha256(archive),
        (Preview("https://example.invalid/preview", "b" * 64),),
        "5.5.0",
    )


def render_skins(
    window: StudioWindow, shot: Callable[[str], None], settle: Callable[[Callable[[], bool]], None]
) -> None:
    page: SkinsPage = window.pages["skins"]
    gallery, view = page.gallery, page.view
    ctx = window.ctx
    installation = ctx.installation

    def loaded() -> bool:
        return page.data is not None and gallery.refresh.isEnabled() and page.job is None

    def gallery_ready() -> None:
        page.motion.setCurrentTime(page.motion.duration())
        settle(loaded)
        settle(
            lambda: all(
                tile.path in gallery.grid.tiles.cache
                for i, tile in enumerate(gallery.grid.tiles.items)
                if gallery.grid.visualRect(gallery.grid.model().index(i)).intersects(
                    gallery.grid.viewport().rect()
                )
            )
        )

    def frame(name: str) -> None:
        page.motion.setCurrentTime(page.motion.duration())
        QApplication.processEvents()
        settle(
            lambda: (
                view.preview.completed is not None and view.preview.completed == view.preview.wanted
            )
        )
        shot("skins-" + name)

    def choose(name: str) -> None:
        page.open_look(next(look for look in page.data.looks if look.name == name))

    gallery_ready()
    shot("skins-gallery")
    gallery.filters.buttons["Light"].click()
    gallery_ready()
    shot("skins-light")
    gallery.search.setText("Missing look")
    shot("skins-no-results")
    gallery.search.clear()
    gallery.filters.buttons["Mine"].click()
    shot("skins-empty-library")
    gallery.create.click()
    frame("maker-quick")
    page.maker.name.setText("Harbour lights")
    page.maker.swatches["accent"].click()
    page.maker.mixer.channels[0].setValue(230)
    frame("maker-colour")
    page.maker.mixer.done.click()
    page.maker.fine.click()
    view.preview.scenes.buttons["Duel field"].click()
    view.preview.turn.click()
    QApplication.processEvents()
    view.scroll.ensureWidgetVisible(page.maker.swatches["positive"])
    frame("maker-overrides")
    view.scroll.verticalScrollBar().setValue(view.scroll.verticalScrollBar().maximum())
    frame("maker-wallpapers")
    with patch.object(
        QFileDialog,
        "getOpenFileName",
        return_value=(str(Path(extras.ASSETS).parent / "window/studio-vortex.jpg"), ""),
    ):
        page.maker.photo.click()
        settle(lambda: not page.job and page.maker.theme.image is not None)
        frame("maker-photo")
    page.maker.styles["aurora"].click()
    page.maker.save_btn.click()
    settle(lambda: page.mode == "detail" and page.look.name == "Harbour lights")
    frame("saved-theme")
    view.inspector.tools["Rename"].click()
    frame("rename")
    view.cancel_name.click()
    page.escape()
    gallery_ready()
    shot("skins-my-library")
    gallery.filters.buttons["All looks"].click()
    choose("Deep Current")
    frame("preview-menu")
    view.preview.scenes.buttons["Deck editor"].click()
    frame("preview-deck")
    view.preview.scenes.buttons["Duel field"].click()
    view.preview.turn.click()
    frame("preview-duel")
    with patch("music.settings._game_running", return_value=True):
        view.inspector.apply.click()
        settle(lambda: page.retry_button.isVisible() and page.job is None)
        frame("close-game")
    release = Event()

    def closed(_: Path) -> bool:
        release.wait(5)
        return False

    with patch("music.settings._game_running", side_effect=closed):
        page.retry_button.click()
        frame("applying")
        release.set()
        settle(lambda: page.look.active and loaded())
        frame("applied")
        page.escape()
        gallery_ready()
        shot("skins-active-gallery")
        choose("Deep Current")
        view.inspector.back.click()
        settle(lambda: not page.look.active and loaded())
        frame("previous-look")

    page.escape()
    window.navigate("cards")
    look = themes.PRESETS[1]
    with (
        patch.object(Store, "finish", side_effect=OSError("Simulated interruption")),
        suppress(OSError),
    ):
        themes.install_theme(
            Path(installation.path), preset_id(look), replace(look, wallpaper="inherit")
        )
    with patch("music.settings._game_running", return_value=False):
        window.navigate("skins")
        settle(lambda: loaded() and "Finished installing" in page.message.text())
        gallery_ready()
        shot("skins-recovered")
    ctx.set_installation(Installation())
    gallery_ready()
    shot("skins-no-installation")
    ctx.set_installation(Installation(installation.path))
    gallery_ready()
    shot("skins-awaiting-game")
    ctx.set_installation(installation)
    gallery_ready()
    empty = catalogue.Catalogue(
        Manifest(1, ()), catalogue.CatalogueSource.BUNDLED, "Could not reach the pack catalogue."
    )
    with patch("content.catalogue.fetch_catalogue", return_value=empty):
        gallery.filters.buttons["Downloadable"].click()
        settle(lambda: loaded() and page.data.catalogue == empty)
        shot("skins-offline")

    packs = (*map(sample_pack, extras.SKINS), sample_pack("Shared"))
    demo = catalogue.Catalogue(Manifest(1, packs), catalogue.CatalogueSource.LIVE)
    with patch("content.catalogue.fetch_catalogue", return_value=demo):
        gallery.refresh.click()
        settle(lambda: loaded() and page.data.catalogue == demo)
        cache = page.store.cache
        for name, version in (("Moon", "0.9.0"), ("Nebula", "1.0.0"), ("Shared", "1.0.0")):
            pack = sample_pack(name)
            folder = cache.root / ("pack-" + pack.id)
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(
                extras.PACKS / (pack.id + ".zip"), folder / (version + "--" + pack.sha256 + ".zip")
            )
        page.reload()
        gallery_ready()
        shot("skins-pack-gallery")
        choose("Abyss")
        frame("pack-detail")
        view.preview.scenes.buttons["Duel field"].click()
        view.inspector.highlight.click()
        frame("pack-highlight")

        async def simulate(skin: Pack, shared: Pack, worker: WorkerContext) -> None:
            worker.progress((skin.title, Progress(4200000, 10000000, 512000)))
            worker.cancel.wait(6)
            raise DownloadError(DownloadFailure.CANCELLED, "Download cancelled.")

        with patch.object(page.store, "download", simulate):
            view.inspector.apply.click()
            settle(lambda: view.progress.percent.text() == "42%")
            frame("pack-download")
            view.progress.cancel.click()
            settle(lambda: loaded() and page.retry_button.text() == "Resume")
            frame("pack-cancelled")
        with patch.object(
            page.store,
            "download",
            side_effect=DownloadError(DownloadFailure.INTEGRITY, "Simulated damaged download."),
        ):
            page.retry_button.click()
            settle(lambda: loaded() and "failed verification" in page.message.text())
            frame("pack-verification-error")
        page.escape()
        choose("Nebula")
        frame("pack-ready")
        with patch("music.settings._game_running", return_value=False):
            view.inspector.apply.click()
            settle(lambda: loaded() and page.look.active)
            frame("pack-applied")
            view.inspector.restore.click()
            settle(lambda: loaded() and not page.data.restore_pack)
            frame("pack-restored")
        page.escape()
    gallery.filters.buttons["All looks"].click()
    gallery_ready()
