"""The soundtrack workspace for the selected installation."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QModelIndex, Qt, QVariantAnimation
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import music_library as engine
from music import settings
from studio import theme as t
from studio.context import AppContext, GameOpenError, Installation
from studio.pages.base import Page
from studio.tasks import Job, WorkerContext
from studio.widgets import GameClosedBlock, Panel, ProgressBlock, glyph, label, margins

from .music_albums import AlbumViews
from .music_imports import ImportViews
from .music_model import SCENES, Library, read_library, track_count
from .music_packs import MusicPacks
from .music_preview import Preview
from .music_widgets import STYLE, AlbumGrid, RecordHero, SceneButton, action


class Stopped(Exception):
    pass


def checkpoint(worker: WorkerContext) -> None:
    if worker.cancel.is_set():
        raise Stopped


class MusicPage(Page):
    id, title, icon = "music", "Music", "music"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.setStyleSheet(STYLE)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transition = QVariantAnimation(self)
        self.transition.setDuration(t.MOTION_MS)
        self.transition.setStartValue(t.MD)
        self.transition.setEndValue(0)
        self.transition.valueChanged.connect(self.slide)
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.addWidget(self.scroll)
        self.root = (
            Path(context.cfg.config_path).parent / "soundtracks"
            if context.cfg.cli.config
            else engine.library_root()
        )
        self.preview = Preview(context.tasks, context.notify)
        self.preview.setParent(self)
        self.preview.hide()
        self.albums = AlbumViews(self)
        self.imports = ImportViews(self)
        self.packs = MusicPacks(self)
        self.data: Library | None = None
        self.job: Job | None = None
        self.active = self.locked = False
        self.state = ""
        self.back: Callable | None = None
        self.scene = "duel"
        self.generation = 0
        self.scene_buttons: dict[str, SceneButton] = {}
        self.message = ""
        self._columns = 0

    @property
    def game(self) -> Path:
        return Path(self.ctx.installation.path)

    def shown(self) -> None:
        self.active = True
        if self.state != "packs":
            self.refresh()

    def hidden(self) -> bool:
        if self.locked:
            return False
        self.active = False
        self.invalidate()
        return True

    def invalidate(self) -> None:
        self.generation += 1
        self.preview.stop()
        if self.job:
            self.job.cancel()
            self.job = None

    def request_close(self) -> bool:
        if self.packs.job:
            self.packs.job.cancel()
        if self.job:
            self.job.cancel()
        self.preview.stop()
        self.active = False
        return True

    def installation_changed(self, installation: Installation) -> None:
        self.invalidate()
        self.data = None
        self.albums.reset()
        self.imports.reset()
        self.scene, self.message = "duel", ""
        if self.active and self.state != "packs":
            self.refresh()

    def escape(self) -> bool:
        if self.job:
            if self.progress.cancel.isVisible():
                self.progress.cancel.click()
            return True
        if self.state == "packs" and self.packs.progress:
            cancel = self.packs.progress.cancel
            if cancel.isVisible() and cancel.isEnabled():
                cancel.click()
            return True
        if self.back:
            self.back()
            return True
        return False

    def screen(
        self,
        name: str,
        back: Callable | None = None,
        title: str = "",
        section: str = "MUSIC LIBRARY",
    ) -> QVBoxLayout:
        self.transition.stop()
        self.preview.stop()
        self.preview.setParent(self)
        self.preview.hide()
        self.state, self.back = name, back
        old = self.scroll.takeWidget()
        if old:
            old.hide()
            old.deleteLater()
        body = QWidget()
        body.setMaximumWidth(t.CONTENT_MAX)
        layout = QVBoxLayout(body)
        self.inset = t.LG if self.width() < t.COMPACT_WIDTH - t.RAIL_WIDTH else t.XXL
        margins(layout, self.inset)
        layout.setSpacing(t.LG)
        self.scroll.setWidget(body)
        if back or title:
            top = QHBoxLayout()
            if back:
                self.back_button = action("Back", back, "quiet", "back")
                top.addWidget(self.back_button)
            top.addWidget(label(section, "goldKicker"))
            top.addStretch()
            layout.addLayout(top)
            if title:
                layout.addWidget(label(title, "heading", True))
        if back:
            self.transition.start()
        return layout

    def slide(self, offset: int) -> None:
        layout = self.scroll.widget().layout()
        layout.setContentsMargins(self.inset + offset, self.inset, self.inset - offset, self.inset)

    def refresh(self, after: Callable | None = None) -> None:
        install = self.ctx.installation
        if not install.ready or not install.path:
            self.unavailable()
            return
        game, root = self.game, self.root
        if after:
            # The preceding edit may have moved files referenced by the old snapshot.
            self.data = None

        def read(worker: WorkerContext) -> Library:
            return read_library(game, root, lambda: checkpoint(worker))

        def loaded(data: Library) -> None:
            self.data = data
            (after or self.home)()

        self.run(
            "Reading your soundtracks",
            read,
            loaded,
            fallback=self.home if self.data else self.unavailable,
        )

    def run(
        self,
        title: str,
        work: Callable[[WorkerContext], Any],
        success: Callable,
        *,
        lock: bool = False,
        writes_game: bool = False,
        destination: Path | None = None,
        cancellable: bool = True,
        fallback: Callable | None = None,
    ) -> None:
        if self.job:
            return
        game = self.game
        write_root = destination or self.root
        self.locked = lock
        if lock:
            self.ctx.set_busy(True)
        generation = self.generation
        fallback = fallback or self.home
        layout = self.screen("busy")
        layout.addWidget(RecordHero("Music", "Soundtrack library", title, "music"), 1)
        self.progress = ProgressBlock()
        self.progress.begin(title)
        self.progress.detail.setText(
            "Keep this installation selected until the change finishes."
            if lock
            else "Reading the selected installation and your library."
        )
        self.progress.cancel.setVisible(cancellable)
        layout.addWidget(self.progress, 2)

        def execute(worker: WorkerContext) -> Any:
            checkpoint(worker)
            if writes_game or (lock and write_root.resolve().is_relative_to(game.resolve())):
                # An installed file may still be open while the audio device releases it.
                with self.preview.lock:
                    self.preview.player.stop()
                self.ctx.require_game_closed(game)
                checkpoint(worker)
            value = work(worker)
            if not lock:
                checkpoint(worker)
            return value

        job = self.ctx.tasks.start(execute)
        self.job = job

        def cancel() -> None:
            job.cancel()
            self.progress.detail.setText("Stopping at the next safe point…")
            self.progress.cancel.setEnabled(False)

        self.progress.cancel.clicked.connect(cancel)
        self.progress.cancel.setFocus()

        def finish() -> bool:
            if job is self.job:
                self.job = None
            if lock:
                self.locked = False
                self.ctx.set_busy(False)
            return self.active and generation == self.generation

        def done(value: Any) -> None:
            if finish():
                success(value)

        def failed(exc: Exception) -> None:
            if not finish():
                return
            if isinstance(exc, Stopped):
                self.message = "Stopped."
                fallback()
            else:
                retry = partial(
                    self.run,
                    title,
                    work,
                    success,
                    lock=lock,
                    writes_game=writes_game,
                    destination=destination,
                    cancellable=cancellable,
                    fallback=fallback,
                )
                self.problem(exc, retry, fallback)

        job.progress.connect(
            lambda pair: self.progress.advance_to(*pair) if job is self.job else None
        )
        job.result.connect(done)
        job.error.connect(failed)

    def problem(self, error: Exception, retry: Callable, back: Callable) -> None:
        layout = self.screen("error", back)
        if isinstance(error, GameOpenError):
            block = GameClosedBlock()
            layout.addWidget(block)
            layout.addStretch()
            block.retry.clicked.connect(retry)
            block.show_reason(str(error))
            return
        title = "Music needs attention"
        message = str(error) or "The soundtrack library could not be read."
        hero = RecordHero("Music", title, message, "music")
        hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(hero, 1)
        retry_button = action("Retry", retry, "primary")
        layout.addWidget(retry_button, 0, Qt.AlignmentFlag.AlignRight)
        retry_button.setFocus()

    def unavailable(self) -> None:
        install = self.ctx.installation
        layout = self.screen("unavailable")
        title = "Choose your installation" if not install.path else "Get your game ready"
        message = (
            "Choose an EDOPro folder to see its music and build playlists."
            if not install.path
            else "Open EDOPro once to download its card databases, then close the game and select its folder again."
        )
        if install.ready:
            title, message = (
                "Your soundtrack library",
                "Reload to read the albums and playlists for this game.",
            )
        hero = RecordHero("Music", title, message, "music")
        hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(hero, 1)
        choose = action(
            "Reload" if install.ready else "Choose folder",
            self.refresh if install.ready else self.ctx.installations_requested.emit,
            "primary",
            "folder",
        )
        layout.addWidget(choose, 0, Qt.AlignmentFlag.AlignRight)
        choose.setFocus()

    def feedback(self, layout: QVBoxLayout) -> None:
        if self.message:
            layout.addWidget(label(self.message, "notice", True))
            self.message = ""

    def home(self) -> None:
        if not self.data:
            self.unavailable()
            return
        layout = self.screen("home")
        if self.data.enabled is False:
            self.setting_bar(layout)
        self.hero = RecordHero(
            "Music library",
            "Duel soundtrack",
            "Choose what plays from the main menu to the final turn.",
            "music",
        )
        self.hero.setMinimumHeight(t.TILE_SIZE[1])
        self.hero.setMaximumHeight(t.HERO_MIN + t.TILE_H)
        self.hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.hero, 3)
        self.feedback(layout)
        header = QHBoxLayout()
        header.addWidget(label("What the game will play", "heading", True), 1)
        self.browse = action("Browse games", self.albums.gallery, "primary", "library")
        self.manage = action("Add music", lambda: self.imports.open_tools(self.home), icon="folder")
        header.addWidget(self.manage)
        header.addWidget(self.browse)
        layout.addLayout(header)
        self.scene_grid = QGridLayout()
        self.scene_grid.setSpacing(t.SM)
        self.scene_buttons = {}
        for key, (name, icon) in SCENES.items():
            tracks = self.data.scenes[key].tracks
            games = {track.game for track in tracks}
            status = track_count(len(tracks)) if tracks else ""
            if len(games) > 1:
                status += f" · {len(games)} games"
            scene = SceneButton(
                name,
                icon,
                status,
                tracks[0].title if tracks else "No music assigned",
            )
            if tracks:
                scene.setToolTip("\n".join([name, *(track.title for track in tracks)]))
            scene.clicked.connect(partial(self.open_scene, key))
            self.scene_buttons[key] = scene
        configured = any(s.tracks or s.restore or s.undo for s in self.data.scenes.values())
        if not configured:
            empty = Panel(t.LG)
            empty.body.addWidget(label("No music assigned yet", "subheading"))
            empty.body.addWidget(
                label("Browse an album and add a recording to a scene.", "muted", True)
            )
            layout.addWidget(empty)
            for button in self.scene_buttons.values():
                button.hide()
        self._columns = 0
        layout.addLayout(self.scene_grid)
        self.shelf = QWidget()
        shelf_layout = QVBoxLayout(self.shelf)
        margins(shelf_layout, 0)
        shelf_layout.addWidget(label("Albums with music", "heading"))
        grid = AlbumGrid(self.ctx.tasks)
        grid.populate(self.data.browse("", has_music=True))

        def open_album(index: QModelIndex) -> None:
            if self.state == "home":
                self.albums.open_game(grid.albums[index.row()].game.id, back=self.home)

        grid.activated.connect(open_album)
        grid.clicked.connect(open_album)
        shelf_layout.addWidget(grid)
        layout.addWidget(self.shelf, 3)
        self.reflow()
        if self.data.enabled is not False:
            self.setting_bar(layout)
        if self.data.enabled is False:
            self.toggle_music.setFocus()
        else:
            self.browse.setFocus()

    def reflow(self) -> None:
        self.shelf.setVisible(
            self.width() >= t.WIDE_CONTENT and self.height() >= t.TALL_HEIGHT - t.TILE_H
        )
        cols = 4 if self.width() >= t.COMPACT_WIDTH - t.RAIL_COMPACT else 2
        if self._columns == cols:
            return
        self._columns = cols
        for i, btn in enumerate(self.scene_buttons.values()):
            self.scene_grid.addWidget(btn, i // cols, i % cols)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.state == "home" and hasattr(self, "shelf"):
            self.reflow()

    def setting_bar(self, layout: QVBoxLayout) -> None:
        enabled = self.data.enabled
        box = Panel(t.MD)
        line = QHBoxLayout()
        line.addWidget(glyph("warning" if enabled is False else "music"))
        text = (
            "Music is switched off in EDOPro."
            if enabled is False
            else "Game music is on."
            if enabled
            else "Check Music in EDOPro’s audio settings."
        )
        line.addWidget(
            label(text + " Changes take effect next time you start EDOPro.", "body", True), 1
        )
        if enabled is not None:
            self.toggle_music = action(
                "Turn music off" if enabled else "Turn music on",
                lambda: self.toggle(not enabled),
                "quiet" if enabled else "primary",
            )
        line.addWidget(action("Reload", self.refresh, "quiet"))
        if enabled is not None:
            line.addWidget(self.toggle_music)
        box.body.addLayout(line)
        layout.addWidget(box)

    def toggle(self, enabled: bool) -> None:
        game = self.game
        self.run(
            "Changing game music",
            lambda _: settings.write_music_enabled(game, enabled),
            lambda _: self.refresh(),
            lock=True,
            writes_game=True,
            cancellable=False,
        )

    def open_scene(self, scene: str) -> None:
        self.scene = scene
        self.albums.playlist()
