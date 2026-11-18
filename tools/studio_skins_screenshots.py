"""Offline Skins views for the Studio screenshot gallery."""

import asyncio
import hashlib
import json
import shutil
from collections.abc import Callable
from contextlib import nullcontext, suppress
from dataclasses import replace
from functools import cache
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import patch
from zipfile import ZipFile

from PySide6.QtWidgets import QApplication, QFileDialog

import extras
import sleeves
import themes
from content import catalogue, download, libraries
from content.cache import PackCache, file_sha256
from content.catalogue import PackStatus
from content.download import DownloadError, DownloadFailure, Progress
from content.manifest import Manifest, Pack, Preview
from content.media import Changes
from studio.context import Installation
from studio.pages.skins import SkinsPage
from studio.pages.skins_model import preset_id
from studio.pages.sleeves import SleevesPage
from studio.shell import StudioWindow
from studio.tasks import WorkerContext
from themes.transaction import Store
from tools.sleeve_contact_sheet import stand_in


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


def library_archive(kind: str) -> tuple[Pack, bytes]:
    """A small original illustration with the same manifest and ZIP layout as a release."""
    data = BytesIO()
    member = "images/Dusk.png"
    item = (
        {"file": member, "title": "Evening lake"}
        if kind == "wallpaper"
        else {"file": member, "name": "Evening lake", "tags": ["landscape", "lake"]}
    )
    artwork = sleeves.png_bytes(stand_in())
    with ZipFile(data, "w") as archive:
        archive.writestr("pack.json", json.dumps({"items": [item]}))
        archive.writestr(member, artwork)
    raw = data.getvalue()
    pack = replace(
        sample_pack("Moon"),
        id="lakeside-" + kind,
        kind=kind,
        title="Lakeside",
        description="A mountain lake at sunset.",
        author="Sample illustration team",
        license="Demo artwork",
        attribution="Evening lake · original illustration for this sample pack.",
        source_url="https://github.com/cntrl-alt-lenny/edopro-hd-cards",
        previews=(
            Preview(
                "https://example.invalid/evening-lake.png",
                hashlib.sha256(artwork).hexdigest(),
                len(artwork),
            ),
        ),
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    return pack, raw


def capture_library(
    page: SkinsPage | SleevesPage,
    shot: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
) -> None:
    browser = page.packs
    prefix = browser.kind + "-catalogue-"

    def pack_shot(state: str) -> None:
        settle(lambda: browser.preview_job is None and not browser.art.pixmap().isNull())
        shot(prefix + state)

    empty = catalogue.Catalogue(Manifest(1, ()), catalogue.CatalogueSource.LIVE)
    with patch("content.catalogue.fetch_catalogue", return_value=empty):
        page.gallery.more.click()
        settle(lambda: not browser.loading)
        shot(prefix + "empty")
    offline = catalogue.Catalogue(
        empty.manifest,
        catalogue.CatalogueSource.BUNDLED,
        "Could not reach the catalog. You can still use your local collection.",
    )
    with patch("content.catalogue.fetch_catalogue", return_value=offline):
        browser.refresh.click()
        settle(lambda: not browser.loading)
        shot(prefix + "offline")
    pack, body = library_archive(browser.kind)
    newer = replace(pack, id="new-edition", title="Lakeside II", min_app_version="99.0.0")
    for edition in (pack, newer):
        folder = browser.cache.root / ("preview-" + edition.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (edition.previews[0].sha256 + ".image")).write_bytes(
            sleeves.png_bytes(stand_in())
        )
    cat = catalogue.Catalogue(Manifest(1, (pack, newer)), catalogue.CatalogueSource.LIVE)
    with patch("content.catalogue.fetch_catalogue", return_value=cat):
        browser.refresh.click()
        settle(lambda: not browser.loading)
    browser.menu.setCurrentRow(1)
    pack_shot("incompatible")
    browser.menu.setCurrentRow(0)
    pack_shot("available")
    release = Event()

    async def stream(
        session: object,
        transfer: object,
        partial: Path,
        offset: int,
        progress: download.ProgressCallback,
        cancel: Event,
        timeout: float,
    ) -> int:
        if partial.name.endswith(".image.part"):
            artwork = sleeves.png_bytes(stand_in())
            partial.write_bytes(artwork)
            return len(artwork)
        progress(Progress(len(body) // 2, len(body), 102400))
        while not release.is_set():
            await asyncio.sleep(0.01)
        partial.write_bytes(body)
        return len(body)

    with (
        patch("content.download._stream", stream),
        patch("content.download.create_http_session", return_value=nullcontext()),
    ):
        browser.action.click()
        settle(lambda: browser.progress.value() in (49, 50))
        pack_shot("downloading")
        browser.cancel.click()
        settle(lambda: not browser.loading and browser.action.text() == "Resume download")
        pack_shot("cancelled")
        with patch(
            "content.libraries.make_available", side_effect=ValueError("Unreadable pack image.")
        ):
            browser.action.click()
            settle(lambda: not browser.loading and browser.action.text() == "Retry")
            pack_shot("invalid")

        # Pause the real bridge at its published phase boundaries so short local operations
        # remain visible long enough to capture.
        install = libraries.make_available
        gates = {state: Event() for state in ("verifying", "installing")}

        async def phased(
            cache: PackCache,
            status: PackStatus,
            *,
            app_version: str,
            cancel: Event,
            progress: download.ProgressCallback,
            phase: Callable[[str], None],
        ) -> Changes:
            def report(value: str) -> None:
                phase(value)
                if value in gates and not gates[value].wait(15):
                    raise TimeoutError("Screenshot phase did not advance")

            return await install(
                cache,
                status,
                app_version=app_version,
                cancel=cancel,
                progress=progress,
                phase=report,
            )

        release.set()
        with patch("content.libraries.make_available", phased):
            browser.action.click()
            for state, gate in gates.items():
                settle(lambda state=state: browser.feedback.text() == state.capitalize() + "…")
                pack_shot(state)
                gate.set()
        settle(lambda: not browser.job and not browser.loading and browser.remove.isVisible())
        pack_shot("installed")
        update = catalogue.Catalogue(
            Manifest(1, (replace(pack, version="1.1.0"), newer)), cat.source
        )
        with patch("content.catalogue.fetch_catalogue", return_value=update):
            browser.refresh.click()
            settle(lambda: not browser.loading)
            pack_shot("update")
        remove = libraries.remove_pack
        removed = Event()

        def paused_remove(cache: PackCache, pack_id: str) -> Changes:
            if not removed.wait(15):
                raise TimeoutError("Screenshot removal did not advance")
            return remove(cache, pack_id)

        with (
            patch.object(page, "confirm", return_value=True),
            patch("content.libraries.remove_pack", paused_remove),
        ):
            browser.remove.click()
            pack_shot("removing")
            removed.set()
            settle(lambda: not browser.job and not browser.loading)
            pack_shot("removed")
        folder = browser.cache.root / ("pack-" + pack.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{pack.version}--{pack.sha256}.zip").write_bytes(body)
        with patch("content.catalogue.fetch_catalogue", return_value=cat):
            browser.refresh.click()
            settle(lambda: browser.action.text() == "Add to library")
            pack_shot("cached")
        browser.action.click()
        settle(lambda: not browser.job and not browser.loading)
    browser.action.click()


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
                and not gallery.grid.tiles.cache[tile.path].isNull()
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
    folder = page.store.library.directory
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("a" * 32 + " (incoming).json")).write_bytes(b"incomplete")
    page.reload()
    settle(lambda: page.retry_button.text() == "Set aside file")
    gallery_ready()
    shot("skins-library-issue")
    page.retry_button.click()
    settle(lambda: loaded() and not page.data.issues)
    capture_library(page, shot, settle)
    settle(lambda: page.mode == "maker" and bool(page.maker.theme.image))
    QApplication.processEvents()
    view.scroll.ensureWidgetVisible(page.maker.credit)
    frame("downloaded-wallpaper")
    with patch("studio.pages.skins_feedback.AppearancePage.confirm", return_value=True):
        page.escape()
    gallery.filters.buttons["Light"].click()
    gallery_ready()
    shot("skins-light")
    gallery.search.setText("Missing theme")
    shot("skins-no-results")
    gallery.search.clear()
    gallery.filters.buttons["My themes"].click()
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
    gallery.filters.buttons["All"].click()
    choose("Deep Current")
    frame("preview-menu")
    view.preview.scenes.buttons["Deck editor"].click()
    frame("preview-deck")
    view.preview.scenes.buttons["Duel field"].click()
    view.preview.turn.click()
    frame("preview-duel")
    with patch("music.settings._game_running", return_value=True):
        view.inspector.apply.click()
        settle(lambda: page.block.isVisible() and page.job is None)
        shot("skins-close-game")
    release = Event()

    def closed(_: Path) -> bool:
        release.wait(5)
        return False

    with patch("music.settings._game_running", side_effect=closed):
        page.block.retry.click()
        frame("applying")
        release.set()
        settle(lambda: page.look.active and loaded())
        frame("applied")
        page.escape()
        gallery_ready()
        shot("skins-active-gallery")
        window.navigate("sleeves")
        desk = window.pages["sleeves"]
        settle(lambda: desk.snapshot is not None and not desk.loading)
        for slot, name in (("mine", "Vortex"), ("opponent", "1998 OCG")):
            desk.banner.slots[slot].click()
            desk.gallery.search.setText(name)
            desk.gallery.choose(desk.gallery.grid.model().index(0))
        desk.apply.click()
        settle(lambda: not desk.writing and not desk.loading)
        desk.gallery.search.clear()
        window.navigate("skins")
        gallery_ready()
        shot("skins-with-assigned-sleeves")
        from tools.studio_sleeves_shots import prepare_overview

        window.navigate("sleeves")
        prepare_overview(window)
        shot("sleeves-with-active-theme")
        window.navigate("skins")
        gallery_ready()
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
        Manifest(1, ()), catalogue.CatalogueSource.BUNDLED, "Could not reach the pack catalog."
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
            raise DownloadError(DownloadFailure.CANCELLED, "Download canceled.")

        with patch.object(page.store, "download", simulate):
            view.inspector.apply.click()
            settle(lambda: view.progress.percent.text() == "42%")
            frame("pack-download")
            view.progress.cancel.click()
            settle(lambda: loaded() and view.inspector.apply.text() == "Resume download")
            frame("pack-cancelled")
        with patch.object(
            page.store,
            "download",
            side_effect=DownloadError(DownloadFailure.INTEGRITY, "Simulated damaged download."),
        ):
            view.inspector.apply.click()
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
    gallery.filters.buttons["All"].click()
    gallery_ready()


def render_page(
    window: StudioWindow,
    selected: Installation,
    shot: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
    *,
    overview: str = "",
) -> None:
    window.navigate("skins")
    render_skins(window, shot, settle)
    if overview:
        shot(overview)


if __name__ == "__main__":
    from tools.studio_screenshots import PAGE_RENDERERS, main_cli

    PAGE_RENDERERS["skins"] = render_page
    main_cli()
