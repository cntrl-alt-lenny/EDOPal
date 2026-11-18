"""Detailed offline Music gallery for the shared Studio screenshot command."""

from __future__ import annotations

import shutil
import wave
from collections.abc import Callable
from functools import partial
from pathlib import Path
from threading import Event
from unittest.mock import patch

import miniaudio
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog

import main
import music_import
import music_library
from studio import theme
from studio.context import AppContext, Installation
from studio.shell import StudioWindow
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
    conf = game / "config/system.conf"
    conf.parent.mkdir()
    conf.write_text("enable_music = 0\n")
    music_library.use_game(game, "duel", "forbidden", root=library, all_scenes=True)
    track = next(
        row for row in music_library.tracks(game, "duel", root=library) if row.game == "tf5"
    )
    music_library.add_to_playlist(game, track, root=library)


def pause_progress(operation: Callable, at: int, release: Event) -> Callable:
    def run(*args, progress: Callable, **kwargs):
        def report(n: int, total: int) -> None:
            progress(n, total)
            if n == at:
                release.wait(6)

        return operation(*args, progress=report, **kwargs)

    return run


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

    def shot(name: str) -> None:
        window.setFocus(Qt.FocusReason.MouseFocusReason)
        window.animation.setCurrentTime(theme.MOTION_MS)
        p.transition.setCurrentTime(theme.MOTION_MS)
        app.processEvents()
        app.processEvents()
        if p.state in ("home", "gallery", "album"):
            settle(lambda: not ctx.tasks.jobs)
        target = out / (name + ".png")
        if not window.grab().save(str(target), "PNG"):
            raise OSError(str(target))
        files.append(target)

    release = Event()
    try:
        settle(lambda: p.state == "home")
        shot("01-soundtrack")
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
        with patch.object(
            miniaudio,
            "PlaybackDevice",
            partial(miniaudio.PlaybackDevice, backends=[miniaudio.Backend.NULL]),
        ):
            p.preview.play.click()
            settle(lambda: not p.preview.pending and p.preview.player.playing)
            p.preview.scrub(0.4)
            p.preview.release()
            settle(lambda: not p.preview.pending)
            shot("09-album-preview")
            p.albums.tracks.cellWidget(0, 2).click()
            shot("10-choose-scene")
        p.albums.open_game("dm3")
        shot("11-empty-album")
        p.imports.manage()
        shot("12-add-music")
        p.imports.tools["Create a game"].click()
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
    finally:
        release.set()
        settle(lambda: not ctx.tasks.jobs)
        window.close()
        app.processEvents()
    return files
