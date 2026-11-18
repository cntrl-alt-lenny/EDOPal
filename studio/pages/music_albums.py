from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
)

import music_library as engine
from music.discovery import Track
from studio import theme as t
from studio.tasks import WorkerContext
from studio.widgets import Button, Panel, SearchField, label

from .music_model import SCENES, SOURCES, Album, change_order, term, track_count
from .music_widgets import (
    AlbumGrid,
    RecordHero,
    SceneButton,
    action,
    cells,
    paint_sleeve,
    row,
    table,
)

if TYPE_CHECKING:
    from .music import MusicPage


class AlbumHero(RecordHero):
    def __init__(self, page: MusicPage, album: Album) -> None:
        super().__init__(
            album.game.series,
            album.game.title,
            f"{album.edition}   ·   {track_count(len(album.tracks))}",
            "music",
        )
        self.album, self.pix = album, QPixmap()
        if album.game.cover_page:
            path = t.ASSETS / "music/detail" / (album.game.id + ".png")

            def read(_: WorkerContext) -> QImage:
                reader = QImageReader(str(path))
                reader.setScaledSize(
                    reader.size().scaled(
                        QSize(*t.TILE_SIZE) * 2, Qt.AspectRatioMode.KeepAspectRatio
                    )
                )
                return reader.read()

            page.ctx.tasks.start(read).result.connect(self.loaded)

    def loaded(self, image: QImage) -> None:
        self.pix = QPixmap.fromImage(image)
        self.update()

    def jacket(self, p: QPainter, x: float, y: float, height: float) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(-7)
        side = height * 0.91
        rect = QRectF(-side / 2, -side / 2, side, side)
        paint_sleeve(p, rect, self.album, self.pix)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(t.BRONZE))
        p.drawRect(rect)
        p.restore()


class AlbumViews:
    def __init__(self, page: MusicPage) -> None:
        self.page = page
        self.reset()

    def reset(self) -> None:
        self.search = self.series = self.key = ""
        self.with_music = False
        self.from_scene = False
        self.album_back: Callable = self.gallery
        self.selected = 0

    def gallery(self, from_scene: bool = False) -> None:
        self.from_scene = from_scene
        p = self.page
        body = p.screen("gallery", self.playlist if from_scene else p.home, "Browse games")
        self.field = SearchField("Find a game or track")
        self.field.setText(self.search)
        self.only = Button("Has music")
        self.only.setCheckable(True)
        self.only.setStyleSheet(
            f"QPushButton:checked {{background:{t.ACTIVE};color:{t.GOLD};border-color:{t.GOLD};}}"
        )
        self.only.setChecked(self.with_music)
        controls = row(
            self.field,
            self.only,
            action(
                "Add music",
                lambda: p.imports.open_tools(partial(self.gallery, from_scene)),
                icon="folder",
            ),
        )
        controls.setStretch(0, 1)
        body.addLayout(controls)
        split = QHBoxLayout()
        rail = Panel(t.MD)
        rail.setFixedWidth(t.RAIL_COMPACT)
        rail.body.addWidget(label("SERIES", "goldKicker"))
        self.series_list = QListWidget()
        self.series_list.setAccessibleName("Filter by series")
        self.series_list.setObjectName("installList")
        for name in ["", *sorted({a.game.series for a in p.data.albums.values()})]:
            item = QListWidgetItem(name or "All series")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.series_list.addItem(item)
            if name == self.series:
                self.series_list.setCurrentItem(item)
        rail.body.addWidget(self.series_list, 1)
        split.addWidget(rail)
        column = QVBoxLayout()
        self.count = label("", "muted")
        self.grid = AlbumGrid(p.ctx.tasks)
        self.empty = Panel(t.LG)
        no_matches = RecordHero(
            "Browse games", "No matches", "Try a different game or track title.", "music"
        )
        no_matches.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.empty.body.addWidget(no_matches, 1)
        self.empty.body.addWidget(
            action("Clear filters", self.clear_filters), 0, Qt.AlignmentFlag.AlignRight
        )
        column.addWidget(self.count)
        column.addWidget(self.empty, 1)
        column.addWidget(self.grid, 1)
        split.addLayout(column, 1)
        body.addLayout(split, 1)
        self.field.textChanged.connect(self.filter)
        self.only.toggled.connect(self.filter)
        self.series_list.currentRowChanged.connect(self.filter)
        self.grid.clicked.connect(lambda i: self.open_game(self.grid.albums[i.row()].game.id))
        self.grid.activated.connect(
            lambda i: (
                self.open_game(self.grid.albums[i.row()].game.id) if p.state == "gallery" else None
            )
        )
        self.filter()
        self.field.setFocus()

    def filter(self) -> None:
        self.search = self.field.text()
        self.with_music = self.only.isChecked()
        self.series = self.series_list.currentItem().data(Qt.ItemDataRole.UserRole)
        albums = self.page.data.browse(self.search, self.series, self.with_music)
        unit = "album" if len(albums) == 1 else "albums"
        self.count.setText(f"{self.series or 'All series'}   ·   {len(albums)} {unit}")
        self.empty.setVisible(not albums)
        self.grid.setVisible(bool(albums))
        self.grid.populate(albums)

    def clear_filters(self) -> None:
        self.only.setChecked(False)
        self.series_list.setCurrentRow(0)
        self.field.clear()
        self.field.setFocus()

    def open_game(self, key: str, *, back: Callable | None = None) -> None:
        self.key = key
        self.album_back = back or partial(self.gallery, self.from_scene)
        self.album()

    def album(self) -> None:
        p = self.page
        a = p.data.albums.get(self.key)
        if a is None:
            self.gallery(self.from_scene)
            return
        body = p.screen("album", self.album_back)
        hero = AlbumHero(p, a)
        if not a.tracks:
            hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            body.addWidget(hero, 1)
            panel = Panel(t.LG)
            prompt = label(
                "No recordings yet. Add files from your collection to this album.", "body", True
            )
            add = action("Add files", partial(p.imports.files, self.key), "primary", "folder")
            panel.body.addLayout(row(prompt, add))
            body.addWidget(panel)
            p.imports.undo_creation(body, self.key)
            add.setFocus()
            return
        body.addWidget(hero)
        head = row(
            label("Recordings", "subheading"),
            action("Add files", partial(p.imports.files, self.key), icon="folder"),
        )
        head.setStretch(0, 1)
        whole = action("Use this whole game", lambda: self.destination(None))
        whole.setEnabled(bool(a.tracks))
        head.addWidget(whole)
        body.addLayout(head)
        self.tracks = table(("Track", "Source", "Playlist"), "Album recordings")
        self.tracks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tracks.setColumnWidth(2, t.TILE_SIZE[0])
        for tr in a.tracks:
            i = cells(self.tracks, (tr.title, SOURCES[tr.source], ""))
            btn = action("Add to scene…", partial(self.destination, tr), "quiet")
            btn.setAccessibleName(f"Add {tr.title} to a scene")
            self.tracks.setCellWidget(i, 2, btn)
        body.addWidget(self.tracks, 1)
        body.addWidget(p.preview)
        p.preview.show()
        self.tracks.currentCellChanged.connect(
            lambda i, *_: p.preview.select(a.tracks[i].path if i >= 0 else None)
        )
        self.tracks.cellActivated.connect(lambda *_: p.preview.toggle())
        index = next(
            (
                i
                for i, tr in enumerate(a.tracks)
                if self.search and term(self.search) in term(tr.title)
            ),
            0,
        )
        self.tracks.setCurrentCell(index, 0)
        self.tracks.setFocus()

    def destination(self, track: Track | None) -> None:
        p = self.page
        a = p.data.albums[self.key]
        layout = p.screen(
            "destination",
            self.album,
            track.title if track else a.game.title,
            "ADD A TRACK" if track else "USE THIS WHOLE GAME",
        )
        layout.addWidget(
            label(
                "Choose a playlist to add this track to."
                if track
                else "Choose a playlist to replace with this album. Undo brings its previous tracks back.",
                "body",
                True,
            )
        )
        grid = QGridLayout()
        grid.setSpacing(t.MD)
        self.destinations = {}
        for i, (key, (name, symbol)) in enumerate(SCENES.items()):
            tracks = p.data.scenes[key].tracks
            btn = SceneButton(
                name,
                symbol,
                track_count(len(tracks)),
                tracks[0].title if tracks else "No tracks selected",
            )
            already = track and any(r.digest == track.digest for r in tracks)
            if already:
                btn.detail.set_value("Already in this playlist")
                btn.setEnabled(False)
            btn.clicked.connect(partial(self.assign, key, track))
            grid.addWidget(btn, i // 2, i % 2)
            self.destinations[key] = btn
        layout.addLayout(grid, 1)
        self.destinations[p.scene].setFocus()

    def assign(self, scene: str, track: Track | None) -> None:
        p = self.page
        album = p.data.albums[self.key]
        name = SCENES[scene][0]
        if track is None:
            if not p.ctx.confirm(
                "Use this whole game?",
                f"Replace the playlist for “{name}” with {album.game.title}? Undo will put the previous playlist back.",
            ):
                return
            tracks = album.tracks
        else:
            tracks = (*p.data.scenes[scene].tracks, track)
        p.scene = scene
        self.selected = 0
        self.save(tracks, partial(self.destination, track))

    def save(self, tracks: Sequence[Track], back: Callable) -> None:
        from .music import checkpoint

        p = self.page
        game, root, scene = p.game, p.root, p.scene
        expected = tuple(r.digest for r in p.data.scenes[scene].tracks)

        def write(worker: WorkerContext) -> None:
            def progress(n: int, total: int) -> None:
                checkpoint(worker)
                if total:
                    worker.progress((n, total))

            engine.set_playlist(
                game,
                scene,
                tracks,
                root=root,
                expected=expected,
                clear=not tracks,
                progress=progress,
            )

        p.run(
            "Updating the playlist",
            write,
            lambda _: p.refresh(self.playlist),
            lock=True,
            writes_game=True,
            fallback=back,
        )

    def playlist(self) -> None:
        p = self.page
        key = p.scene
        scene = p.data.scenes[key]
        title, symbol = SCENES[key]
        layout = p.screen("playlist", p.home, title, "WHAT THE GAME WILL PLAY")
        panel = Panel(t.LG)
        head = row(
            label(track_count(len(scene.tracks)), "subheading"),
            action("Browse games", partial(self.gallery, True), "primary", "library"),
        )
        head.setStretch(0, 1)
        panel.body.addLayout(head)
        self.list = table(("Track", "Game"), "Current playlist")
        for tr in scene.tracks:
            a = p.data.albums.get(tr.game)
            cells(self.list, (tr.title, a.game.title if a else "Your library"))
        if not scene.tracks:
            hero = RecordHero(
                "Playlist",
                "No tracks here yet",
                "Browse a game and choose a recording for this scene.",
                "music",
            )
            hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            panel.body.addWidget(hero, 1)
        panel.body.addWidget(self.list, 1)
        self.list.setVisible(bool(scene.tracks))
        self.up = action("Move up", partial(self.edit, -1))
        self.down = action("Move down", partial(self.edit, 1))
        self.remove = action("Remove", partial(self.edit, 0))
        self.clear = action("Clear playlist", self.clear_playlist, "quiet")
        tools = row(self.up, self.down, self.remove)
        tools.addStretch()
        tools.addWidget(self.clear)
        panel.body.addLayout(tools)
        if not scene.tracks:
            for button in (self.up, self.down, self.remove, self.clear):
                button.hide()
        layout.addWidget(panel, 1)
        footer = Panel(t.LG)
        footer.body.addWidget(
            label("Changes take effect next time you start EDOPro.", "body", True)
        )
        self.undo = action("Undo last edit", partial(self.recover, False))
        self.restore = action("Restore original music", partial(self.recover, True))
        self.undo.setEnabled(scene.undo)
        self.restore.setEnabled(scene.restore)
        footer.body.addLayout(row(self.undo, self.restore))
        footer.body.addWidget(
            label(
                "Undo puts the previous playlist back. Restore returns the original game files for this scene.",
                "muted",
                True,
            )
        )
        layout.addWidget(footer)
        self.list.currentCellChanged.connect(self.update_actions)
        self.list.setCurrentCell(min(self.selected, len(scene.tracks) - 1), 0)
        self.update_actions()
        if scene.tracks:
            self.list.setFocus()
        else:
            head.itemAt(1).widget().setFocus()

    def update_actions(self) -> None:
        i, n = self.list.currentRow(), self.list.rowCount()
        self.up.setEnabled(i > 0)
        self.down.setEnabled(0 <= i < n - 1)
        self.remove.setEnabled(i >= 0)
        self.clear.setEnabled(n > 0)

    def edit(self, step: int) -> None:
        p = self.page
        i, key = self.list.currentRow(), p.scene
        tracks = p.data.scenes[key].tracks
        self.selected = max(0, i + step)
        if step:
            chosen = change_order(tracks, i, step)
        elif len(tracks) == 1:
            self.clear_playlist()
            return
        else:
            chosen = [*tracks[:i], *tracks[i + 1 :]]
        self.save(chosen, self.playlist)

    def clear_playlist(self) -> None:
        p = self.page
        if p.ctx.confirm(
            "Clear this playlist?",
            f"Remove every track for “{SCENES[p.scene][0]}”? Undo will put the playlist back.",
        ):
            self.save((), self.playlist)

    def recover(self, original: bool) -> None:
        p = self.page
        work: Callable
        if original:
            if not p.ctx.confirm(
                "Restore original music?",
                "Put this scene’s original game files back in their original folders?",
            ):
                return
            work = partial(engine.restore_playlist, p.game, p.scene)
        else:
            work = partial(engine.undo_playlist, p.game, p.scene, root=p.root)
        from .music import checkpoint

        p.run(
            "Restoring the playlist",
            lambda worker: work(checkpoint=lambda: checkpoint(worker)),
            lambda _: p.refresh(self.playlist),
            lock=True,
            writes_game=True,
            fallback=self.playlist,
        )
