"""Offline Sounds scenarios registered by the Studio screenshot runner."""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QFileDialog

import sounds
from content.catalogue import Catalogue, CatalogueSource, PackState, PackStatus
from content.download import DownloadError, DownloadFailure, Progress
from content.manifest import Manifest, Pack
from studio import theme
from studio.context import Installation
from studio.pages import sounds_collections, sounds_model
from studio.pages.sounds import SoundsPage
from studio.shell import StudioWindow


def render_sounds(
    window: StudioWindow,
    root: Path,
    selected: Installation,
    capture: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
) -> None:
    page: SoundsPage = window.pages["sounds"]
    game = Path(selected.path)
    thonk, synth = sounds.bundled_sets()
    sound = game / "sound"
    sound.mkdir()
    for key, clip in thonk.assignments.items():
        (sound / f"{key}.{clip.format}").write_bytes(clip.data)
    settings = game / "config/system.conf"
    settings.parent.mkdir()
    settings.write_text("enable_sound = 1\nsound_volume = 25\nsound_backend = miniaudio\n")
    page.ctx.set_installation(selected)
    window.navigate("sounds")

    def idle() -> None:
        settle(lambda: not page.ctx.tasks.jobs)

    def shot(name: str) -> None:
        page.animation.setCurrentTime(theme.MOTION_MS)
        for _ in range(3):
            QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            QApplication.processEvents()
        capture(name)

    idle()
    shot("01-installed-and-sets")
    page.overview.saved.click()
    shot("21-saved-sets-empty")
    page.escape()
    page.cards["builtin:Synth"].use.click()
    shot("02-set-selected")
    page.overview.mix.click()
    page.mixer.rows["summon"].edit.click()
    shot("03-choose-sound")
    page.picker.silence.click()
    shot("04-mixer")
    page.bar.save.click()
    page.collections.name.name.setText("Late-night duels")
    shot("05-name-mix")
    page.collections.name.submit.click()
    idle()
    shot("06-saved-sets")
    damaged = root / "unreadable.edosounds"
    damaged.write_bytes(b"This is not a set.")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(damaged), "")):
        page.collections.library.import_button.click()
        idle()
    shot("07-rejected-import")
    page.clear_feedback()
    page.escape()

    with patch.object(sounds_model, "_game_running", return_value=True):
        page.bar.apply.click()
        idle()
    shot("08-close-edopro")

    real_apply = sounds.apply_set
    started, release = threading.Event(), threading.Event()

    def block(*args) -> None:
        started.set()
        if release.wait(5):
            real_apply(*args)

    with (
        patch.object(sounds_model, "_game_running", return_value=False),
        patch.object(sounds, "apply_set", side_effect=block),
    ):
        page.feedback.retry.click()
        try:
            settle(started.is_set)
            shot("09-applying")
        finally:
            release.set()
            idle()
    shot("10-applied-and-history")
    draw = sound / "draw.wav"
    draw.write_bytes(b"Changed outside the app")
    with patch.object(sounds_model, "_game_running", return_value=False):
        page.bar.undo.click()
        idle()
    shot("11-external-change")
    page.clear_feedback()

    # Populate a real card chant while leaving the demo transaction history in place.
    chants = sound / "summon"
    chants.mkdir()
    (chants / "46986414.wav").write_bytes(thonk.assignments["summon"].data)
    settings.write_text("enable_sound = 0\nsound_volume = 0\nsound_backend = miniaudio\n")
    page.refresh()
    idle()
    shot("12-muted-effects")
    page.overview.chants.click()
    shot("13-card-chants")
    page.escape()

    download_states(page, shot, idle, settle, synth)
    page.escape()
    page.ctx.set_installation(Installation())
    shot("19-no-installation")
    page.ctx.set_installation(Installation(selected.path))
    shot("20-installation-not-ready")
    page.ctx.set_installation(Installation(str(root / "Unavailable"), ready=True))
    idle()
    shot("22-installation-read-error")


def download_states(
    page: SoundsPage,
    shot: Callable[[str], None],
    idle: Callable[[], None],
    settle: Callable[[Callable[[], bool]], None],
    sample: sounds.SoundSet,
) -> None:
    pack = Pack(
        "tabletop",
        "sound-effects",
        "Tabletop effects",
        "Soft taps and short accents for a quieter duel.",
        "Studio demo",
        "CC0 1.0",
        "Synthetic catalogue entry for the screenshot.",
        "https://example.test",
        "1.0.0",
        "https://example.test/tabletop",
        2097152,
        "a" * 64,
        (),
        "5.5.0",
    )
    catalogue = Catalogue(Manifest(1, ()), CatalogueSource.BUNDLED, "Network unavailable")
    first = True

    async def refresh(*_, **__) -> Catalogue:
        return catalogue

    async def transfer(_cache, packet, _version, report, cancel) -> sounds_model.Downloadable:
        nonlocal first
        report(Progress(1048576, 2097152, 524288))
        if first:
            first = False
            while not cancel.is_set():
                await asyncio.sleep(0.01)
            raise DownloadError(DownloadFailure.CANCELLED, "Download cancelled.")
        return sounds_model.Downloadable(
            PackStatus(packet, PackState.DOWNLOADED, None), replace(sample, name=packet.title)
        )

    with (
        patch.object(sounds_collections, "fetch_catalogue", refresh),
        patch.object(sounds_collections, "get_pack", transfer),
    ):
        page.overview.downloads.click()
        idle()
        shot("14-catalogue-offline")
        catalogue = Catalogue(Manifest(1, ()), CatalogueSource.LIVE)
        page.collections.downloads.refresh.click()
        idle()
        shot("15-catalogue-empty")
        catalogue = Catalogue(Manifest(1, (pack,)), CatalogueSource.LIVE)
        page.collections.downloads.refresh.click()
        idle()
        card = page.collections.pack_cards[pack.id]
        card.download.click()
        try:
            settle(lambda: card.progress.percent.text() == "50%")
            shot("16-downloading")
        finally:
            card.progress.cancel.click()
            idle()
        shot("17-download-paused")
        card.download.click()
        idle()
        shot("18-downloaded")
