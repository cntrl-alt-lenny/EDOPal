"""Detailed offline Music gallery for the shared Studio screenshot command."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import wave
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch
from zipfile import ZipFile

import miniaudio
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFileDialog

import main
import music_import
import music_library
from content.cache import PackCache
from content.catalogue import Catalogue, CatalogueSource
from content.download import DownloadError, DownloadFailure, Progress, ProgressCallback
from content.libraries import make_available
from content.manifest import Manifest, Pack
from content.media import Changes
from music_player import PreviewPlayer
from studio import theme
from studio.context import AppContext, Installation
from studio.pages.music import MusicPage
from studio.shell import StudioWindow
from studio.widgets import Button, Panel, ProgressBlock
from tools.studio_screenshots import DemoSync, create_demo, settle


def recording(path: Path, tone: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 1, 8000, 0, "NONE", "not compressed"))
        out.writeframes(bytes([128, 128 + tone]) * 8000 * 12)
    return path


def seed_music(game: Path, library: Path) -> None:
    for i, (scene, title) in enumerate(
        (
            ("menu", "Welcome to Domino"),
            ("deck", "Card atelier"),
            ("duel", "Midnight challenge"),
            ("advantage", "Turning the tide"),
            ("disadvantage", "Under pressure"),
            ("win", "Victory lap"),
        ),
        1,
    ):
        recording(game / "sound/BGM" / scene / f"{title}.wav", i)
    for i, (key, scene, title) in enumerate(
        (
            ("forbidden", "duel", "Temple of the sun"),
            ("forbidden", "deck", "Into the archives"),
            ("forbidden", "menu", "Moonlit courtyard"),
            ("forbidden", "win", "Return to Memphis"),
            ("roses", "duel", "Lancastrian rose"),
            ("wc2011", "menu", "City lights"),
            ("tf5", "duel", "Satellite rendezvous"),
            ("masterduel", "menu", "Overture"),
        ),
        10,
    ):
        recording(library / key / scene / f"{title}.wav", i)
    # 84 distinct lossless recordings across 19 of the catalogue's games.
    titles = (
        "After the gates",
        "Arena lights",
        "Blue horizon",
        "Crossroads",
        "Dawn patrol",
        "Evening match",
        "Final round",
        "Last train",
        "North tower",
        "Quiet streets",
        "River crossing",
        "Wild cards",
    )
    seeded = ("forbidden", "roses", "wc2011", "tf5", "masterduel")
    keys = [
        *seeded,
        *(
            key
            for key in music_library.catalog(root=library)
            if key not in (*seeded, "local", "dm3")
        ),
    ][:19]
    tone = 42
    for key in keys:
        existing = len(list((library / key).rglob("*.wav")))
        for title in titles[: (12 if key == "forbidden" else 4) - existing]:
            recording(library / key / "duel" / f"{title}.wav", tone)
            tone += 1
    conf = game / "config/system.conf"
    conf.parent.mkdir()
    conf.write_text("enable_music = 0\n")
    playlist = []
    for key, scene, title in (
        ("forbidden", "duel", "Temple of the sun"),
        ("roses", "duel", "Lancastrian rose"),
        ("wc2011", "menu", "City lights"),
        ("tf5", "duel", "Satellite rendezvous"),
        ("masterduel", "menu", "Overture"),
    ):
        rows = music_library.tracks(game, scene, root=library)
        playlist.append(next(row for row in rows if (row.game, row.title) == (key, title)))
    music_library.set_playlist(game, "duel", playlist, root=library)


def pause_progress(operation: Callable, at: int, release: Event) -> Callable:
    def run(*args, progress: Callable, **kwargs):
        def report(n: int, total: int) -> None:
            progress(n, total)
            if n == at:
                release.wait(6)

        return operation(*args, progress=report, **kwargs)

    return run


def demo_pack(root: Path, kind: str) -> tuple[Pack, Path]:
    path = root / f"{kind}.zip"
    if kind == "music":
        member = "dm3/Night.wav"
        media = recording(root / "Pack source/Night.wav", 117)
        item = {"file": member, "scenes": ["deck", "duel"], "title": "Night in the city"}
    else:
        member = "dm3/cover.png"
        media = root / "Alternate cover.png"
        image = QImage(64, 96, QImage.Format.Format_RGB32)
        image.fill(QColor(theme.BRONZE))
        if not image.save(str(media)):
            raise OSError(f"Could not create {media}")
        item = {"file": member}
    with ZipFile(path, "w") as archive:
        archive.write(media, member)
        archive.writestr("pack.json", json.dumps({"items": [item]}))
    title = "City sessions" if kind == "music" else "City sessions cover"
    pack = Pack(
        f"city-{kind}",
        kind,
        title,
        "A new recording for Dark Duel Stories."
        if kind == "music"
        else "Alternate album artwork for Dark Duel Stories.",
        "Demo audio team",
        "CC0-1.0",
        f"{title} by Demo audio team.",
        "https://github.com/cntrl-alt-lenny/edopro-hd-cards/blob/main/tools/music_screenshots.py",
        "1.0.0",
        f"https://example.test/{kind}.zip",
        path.stat().st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        (),
        "5.5.0",
    )
    return pack, path


def render_packs(p: MusicPage, root: Path, shot: Callable[[str], None]) -> None:
    packs = p.packs
    music, archive = demo_pack(root, "music")
    covers, _ = demo_pack(root, "covers")
    listing = Catalogue(Manifest(1, ()), CatalogueSource.BUNDLED, "Offline")
    outcome = "pause"
    release = {"verifying": Event(), "installing": Event()}

    async def fetch(*args: object, **kwargs: object) -> Catalogue:
        return listing

    async def transfer(
        cache: PackCache,
        pack: Pack,
        *,
        progress: ProgressCallback,
        cancel: Event,
        **kwargs: object,
    ) -> Path:
        progress(Progress(pack.size_bytes // 2, pack.size_bytes, 65536))
        if outcome == "pause":
            while not cancel.is_set():
                await asyncio.sleep(0.01)
            raise DownloadError(DownloadFailure.CANCELLED, "Download canceled.")
        if outcome == "error":
            raise DownloadError(DownloadFailure.TRANSIENT, "The connection closed.")
        folder = cache.root / f"pack-{pack.id}"
        folder.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copyfile(archive, folder / f"{pack.version}--{pack.sha256}.zip"))

    async def install(*args: object, phase: Callable[[str], None], **kwargs: object) -> Changes:
        def show(name: str) -> None:
            phase(name)
            if name in release:
                release[name].wait(6)

        return await make_available(*args, phase=show, **kwargs)

    def click(text: str) -> None:
        button = next(
            button
            for view in (p.scroll.widget(), p.footer)
            if view
            for button in view.findChildren(Button)
            if button.text() == text
        )
        button.click()

    def idle() -> None:
        settle(lambda: packs.job is None)

    with (
        patch("studio.pages.music_packs.fetch_catalogue", fetch),
        patch("content.libraries.download_pack", transfer),
        patch("studio.pages.music_packs.make_available", install),
        patch.object(p, "confirm", return_value=True),
    ):
        click("Get more music")
        idle()
        shot("23-pack-catalogue-offline")
        listing = Catalogue(Manifest(1, ()), CatalogueSource.LIVE)
        click("Refresh catalog")
        idle()
        shot("24-pack-catalogue-empty")
        listing = Catalogue(Manifest(1, (music, covers)), CatalogueSource.LIVE)
        click("Refresh catalog")
        idle()
        shot("25-music-and-cover-packs")
        click("Download")
        panel = p.scroll.widget().findChild(Panel, music.id)
        meter = panel.findChild(ProgressBlock)
        try:
            settle(lambda: meter.bar.value() > 0)
            shot("26-pack-downloading")
        finally:
            meter.cancel.click()
            idle()
        shot("27-pack-cancelled")
        outcome = "error"
        click("Resume download")
        idle()
        shot("31-pack-error")
        outcome = "success"
        click("Retry")
        meter = packs.progress
        try:
            for phase, filename in (
                ("verifying", "32-pack-verifying"),
                ("installing", "33-pack-installing"),
            ):
                settle(lambda phase=phase: meter.phase.text().lower().startswith(phase))
                shot(filename)
                release[phase].set()
        finally:
            for gate in release.values():
                gate.set()
        idle()
        shot("28-pack-installed")
        click("View in collection")
        settle(lambda: p.state == "album")
        shot("29-pack-album")
        p.escape()
        idle()
        listing = Catalogue(
            Manifest(
                1, (replace(music, version="2.0.0"), replace(covers, min_app_version="99.0.0"))
            ),
            CatalogueSource.LIVE,
        )
        click("Refresh catalog")
        idle()
        shot("34-update-and-incompatible")
        click("Remove pack")
        idle()
        shot("30-pack-removed")
        p.escape()
        settle(lambda: p.state == "manage")


def render_music(out: Path, size: tuple[int, int], root: Path) -> list[Path]:
    app = QApplication.instance()
    theme.apply(app)
    cfg = create_demo(root)
    game = Path(cfg.edopro_path)
    seed_music(game, root / "soundtracks")
    ctx = AppContext(cfg, main.VERSION, DemoSync(), main._apply_gui_choices)
    window = StudioWindow(ctx, startup=False)
    install = Installation.inspect(str(game))
    ctx.set_installation(install)
    window.resize(*size)
    window.show()
    window.navigate("music")
    p = window.current
    files = []
    out.mkdir(parents=True, exist_ok=True)

    def shot(name: str, *, preview: bool = False) -> None:
        window.setFocus(Qt.FocusReason.MouseFocusReason)
        window.animation.setCurrentTime(theme.MOTION_MS)
        p.transition.setCurrentTime(theme.MOTION_MS)
        app.processEvents()
        app.processEvents()
        if p.state in ("home", "gallery", "album"):
            settle(lambda: not ctx.tasks.jobs)
        if preview:
            p.scroll.ensureWidgetVisible(
                p.preview.notice if p.preview.notice.isVisible() else p.preview.play
            )
            app.processEvents()
        target = out / (name + ".png")
        if not window.grab().save(str(target), "PNG"):
            raise OSError(str(target))
        files.append(target)

    release = Event()
    try:
        settle(lambda: p.state == "home")
        shot("01-soundtrack")
        from music.settings import GameProcessUnknown

        with patch("music.settings._game_running", side_effect=GameProcessUnknown()):
            p.toggle_music.click()
            settle(lambda: p.state == "error")
            shot("36-process-unknown")
            p.escape()
        with patch("music.settings._game_running", return_value=True):
            p.toggle_music.click()
            settle(lambda: p.state == "error")
            shot("02-close-game")
            p.escape()
        with patch("music.settings._game_running", return_value=False):
            p.toggle_music.click()
            settle(lambda: p.state == "home")
        shot("03-music-on")
        p.scene_buttons["duel"].click()
        shot("04-playlist")
        p.escape()
        p.scene_buttons["lose"].click()
        shot("05-empty-playlist")
        p.escape()
        p.browse.click()
        app.processEvents()
        settle(lambda: not ctx.tasks.jobs)
        shot("06-albums")
        p.albums.only.click()
        p.albums.field.setText("Temple")
        app.processEvents()
        settle(lambda: not ctx.tasks.jobs)
        shot("07-track-search")
        p.albums.field.setText("Does not exist")
        shot("08-no-matches")
        p.albums.open_game("forbidden")
        with patch.object(p.preview.player, "play", side_effect=OSError("No audio device")):
            p.preview.play.click()
            settle(lambda: p.preview.notice.isVisible() and not p.preview.pending)
            shot("37-no-audio-device", preview=True)
        with patch.object(p.preview, "player", PreviewPlayer(backends=[miniaudio.Backend.NULL])):
            p.preview.play.click()
            settle(lambda: not p.preview.pending and p.preview.player.playing)
            p.preview.scrub(0.4)
            p.preview.release()
            settle(lambda: not p.preview.pending)
            shot("09-album-preview", preview=True)
            p.albums.tracks.cellWidget(0, 2).click()
            shot("10-choose-scene")
        p.albums.open_game("dm3")
        shot("11-empty-album")
        p.imports.manage()
        shot("12-add-music")
        render_packs(p, root, shot)
        p.imports.tools["Create album"].click()
        p.imports.name.setText("Duel night mixtape")
        shot("13-create-game")
        p.imports.create.click()
        settle(lambda: p.state == "album")
        shot("19-personal-album")
        folder = root / "Recordings/Forbidden Memories"
        recording(folder / "Desert sands.wav", 40)
        (folder / "Session notes.txt").write_text("Notes")
        shutil.copyfile(
            root / "soundtracks/forbidden/deck/Into the archives.wav",
            folder / "Archive reprise.wav",
        )
        (folder / "Damaged take.mp3").write_bytes(b"broken")
        p.imports.plan_paths([folder])
        settle(lambda: p.state == "review")
        shot("14-review-folder")
        p.imports.confirm.click()
        settle(lambda: p.state == "results")
        shot("15-import-results")
        p.imports.manage()

        destination = root / "Exported library"
        destination.mkdir()
        with (
            patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)),
            patch.object(
                music_import,
                "export_library",
                pause_progress(music_import.export_library, 2, release),
            ),
        ):
            p.imports.tools["Export library"].click()
            settle(lambda: p.progress.bar.value() == 2)
            shot("16-transfer-progress")
            release.set()
            settle(lambda: p.state == "manage")
            shot("20-export-complete")
        p.imports.plan_paths([folder])
        settle(lambda: p.state == "review")
        release.clear()
        with patch.object(
            music_import, "execute_import", pause_progress(music_import.execute_import, 1, release)
        ):
            p.imports.confirm.click()
            settle(lambda: p.progress.bar.value() == 1)
            p.progress.cancel.click()
            release.set()
            settle(lambda: p.state == "results")
            shot("21-cancelled-import")
        p.albums.open_game("local")
        shot("22-recording-sources")
        ctx.set_installation(Installation())
        shot("17-no-installation")
        ctx.set_installation(Installation(str(game)))
        shot("18-waiting-for-game")
        state = game / "sound/.edopro-hd-sync/win"
        state.mkdir(parents=True, exist_ok=True)
        (state / "selection.json").write_bytes(b"{")
        ctx.set_installation(install)
        settle(lambda: p.state == "home")
        p.scene_buttons["win"].click()
        with patch.object(p, "confirm", return_value=True):
            p.albums.restore.click()
            settle(lambda: p.state == "error")
            shot("38-recovery-record")
        clean = create_demo(root / "Second installation")
        ctx.set_installation(Installation.inspect(clean.edopro_path))
        settle(lambda: p.state == "home")
        shot("35-no-assigned-music")
    finally:
        release.set()
        settle(lambda: not ctx.tasks.jobs)
        window.close()
        app.processEvents()
    return files
