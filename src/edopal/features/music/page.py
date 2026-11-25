"""The music gallery, preview transport and EDOPro's five configurable music roles."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import CancelledError
from dataclasses import replace
from functools import partial

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from edopal.ui import theme as t
from edopal.ui.widgets import PageHeader, page_layout

from .player import PreviewError, PreviewPlayer
from .service import ROLES, ConfirmClosedError, MusicService
from .widgets import CoverTile, RoleButton, Sleeve, TrackRow, icon, icon_button, metadata, mmss, track_label

_TRACKS_PER_PAGE = 3


def _label(text="", role="", *, wrap=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _button(text, role="", glyph=None):
    button = QPushButton(text)
    button.setProperty("role", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if glyph:
        button.setIcon(icon(glyph))
        button.setIconSize(QSize(16, 16))
    return button


def _box(layout):
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    return layout


def _same_album(a, b):
    return a and b and a.id == b.id and (a.pack is not None or (a.name == b.name and a.install_root == b.install_root))


def _prepare_preview(service, player, album, track, ctx):
    path, cached = service.preview(album, track, ctx)
    if ctx.cancelled:
        raise CancelledError()
    return player.prepare(path), cached


class MusicPage(QWidget):
    def __init__(self, core, *, player=None):
        super().__init__()
        self.core = core
        self.service = MusicService(core)
        self.player = player if player is not None else PreviewPlayer()
        self.albums = ()
        self.game = self.album = self.track = None
        self.columns, self.page_index, self.track_page = 4, 0, 0
        self._generation = self._discovery_generation = 0
        self._preview_job = self._write_job = None
        self._pending_audio = self._writing = self._loaded = self._catalog_failed = False
        self._pixmaps = {}
        self._shot = None
        self._last_player_state = None

        self.setObjectName("canvas")
        self.layout_main = page_layout(self)
        self._build_header()
        self.body = QHBoxLayout()
        self.body.setSpacing(16)
        self.layout_main.addLayout(self.body, 1)
        self._build_gallery()
        self._build_detail()
        self._build_assignments()
        self._update_detail()
        self._update_roles()
        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self._transport)

    def _build_header(self):
        self.header = PageHeader("Soundtrack library", "Music")
        self.summary = self.header.detail
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary.setText("Loading soundtracks…")
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(230)
        self.search.setFixedHeight(t.BUTTON_H)
        self.search.setPlaceholderText("Find a soundtrack")
        self.search.setAccessibleName("Find a soundtrack")
        self.search.addAction(
            QAction(icon("search", t.MUTED), "Find", self.search), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self._change_filter)
        self.header.actions.addWidget(self.search)
        self.layout_main.addWidget(self.header)

    def _build_gallery(self):
        gallery = QWidget()
        layout = _box(QVBoxLayout(gallery))
        bar = _box(QHBoxLayout())
        self.packs_tab = _button("Soundtracks", glyph="music")
        self.local_tab = _button("Your music", glyph="local")
        self.tab_group = QButtonGroup(self)
        for btn in (self.packs_tab, self.local_tab):
            btn.setCheckable(True)
            btn.setFixedHeight(t.CONTROL_H)
            btn.setStyleSheet(
                f"QPushButton {{padding: 3px 11px; color: {t.MUTED}; background: transparent; "
                "border-color: transparent; }"
                f"QPushButton:checked {{ color: {t.PALE}; background: {t.ACTIVE}; border-color: {t.EDGE}; }}"
                f"QPushButton:hover {{background: {t.HOVER};}}"
                f"QPushButton[keyboardFocus=true]:focus {{border-color: {t.PALE};}}"
            )
            self.tab_group.addButton(btn)
            bar.addWidget(btn)
        self.packs_tab.setChecked(True)
        self.tab_group.buttonClicked.connect(self._change_tab)
        bar.addStretch()
        self.prev_page = icon_button("left", "Previous cover page")
        self.next_page = icon_button("right", "Next cover page")
        self.page_number = QLabel()
        self.page_number.setProperty("role", "small")
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_number.setMinimumWidth(34)
        self.prev_page.clicked.connect(lambda: self._page_by(-1))
        self.next_page.clicked.connect(lambda: self._page_by(1))
        bar.addWidget(self.prev_page)
        bar.addWidget(self.page_number)
        bar.addWidget(self.next_page)
        layout.addLayout(bar)
        self.gallery_stack = QStackedWidget()
        self.grid_host = QWidget()
        self.grid = _box(QGridLayout(self.grid_host))
        self.tiles = [CoverTile() for _ in range(12)]
        self._grid_columns = 0
        for i, tile in enumerate(self.tiles):
            tile.clicked.connect(partial(self._tile_click, i))
            tile.moved.connect(partial(self._tile_move, i))
        self.gallery_stack.addWidget(self.grid_host)
        empty = QFrame()
        empty.setProperty("role", "panel")
        empty_layout = QVBoxLayout(empty)
        self.empty_title = _label("", "subheading", wrap=True)
        self.empty_hint = _label("", "muted", wrap=True)
        empty_layout.addStretch()
        for label in (self.empty_title, self.empty_hint):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(label)
        empty_layout.addStretch()
        self.gallery_stack.addWidget(empty)
        layout.addWidget(self.gallery_stack, 1)
        self.body.addWidget(gallery, 7)

    def _build_detail(self):
        self.detail = QFrame()
        self.detail.setProperty("role", "panel")
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self.detail)
        layout.setContentsMargins(t.MD, t.MD, t.MD, t.MD)
        layout.setSpacing(t.XS)
        self.sleeve = Sleeve()
        self.sleeve.setAccessibleName("Selected soundtrack artwork")
        layout.addWidget(self.sleeve, 1)
        self.album_title = _label("", "heading", wrap=True)
        self.album_meta = _label("", "muted")
        layout.addWidget(self.album_title)
        layout.addWidget(self.album_meta)
        bar = _box(QHBoxLayout())
        self.track_count = _label("", "goldKicker")
        bar.addWidget(self.track_count, 1)
        self.prev_track = icon_button("left", "Previous tracks")
        self.next_track = icon_button("right", "Next tracks")
        for button, step in ((self.prev_track, -1), (self.next_track, 1)):
            button.setFixedSize(24, 23)
            button.clicked.connect(partial(self._track_page_by, step))
            bar.addWidget(button)
        layout.addLayout(bar)
        self.track_rows = []
        for i in range(_TRACKS_PER_PAGE):
            row = TrackRow()
            row.clicked.connect(partial(self._track_click, i))
            layout.addWidget(row)
            self.track_rows.append(row)
        self.track_hint = _label("", "muted", wrap=True)
        layout.addWidget(self.track_hint)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(3)
        self.progress.setAccessibleName("Preview progress")
        layout.addWidget(self.progress)
        controls = _box(QHBoxLayout())
        self.play_button = _button("Preview", glyph="play")
        self.play_button.setFixedHeight(t.CONTROL_H)
        self.play_button.setStyleSheet("padding: 2px 10px; font-size: 12px;")
        self.play_button.clicked.connect(self._toggle_preview)
        self.clock = _label("", "muted")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mute_button = icon_button("volume", "Mute preview")
        self.mute_button.clicked.connect(self._toggle_mute)
        controls.addWidget(self.play_button)
        controls.addWidget(self.clock, 1)
        controls.addWidget(self.mute_button)
        layout.addLayout(controls)
        self.body.addWidget(self.detail, 3)

    def _build_assignments(self):
        line = QFrame()
        line.setProperty("role", "line")
        line.setFixedHeight(1)
        self.layout_main.addWidget(line)
        heading = _box(QHBoxLayout())
        heading.addWidget(_label("In-game assignments", "subheading"), 1)
        self.restore_button = _button("Restore original music", "quiet", "restore")
        self.restore_button.setFixedHeight(t.CONTROL_H)
        self.restore_button.setStyleSheet("padding: 1px 5px; font-size: 12px;")
        self.restore_button.clicked.connect(lambda: self._restore())
        heading.addWidget(self.restore_button)
        self.restore_one = icon_button("down", "Restore one role")
        self.restore_one.setFixedSize(t.CONTROL_H, t.CONTROL_H)
        self.restore_menu = QMenu(self.restore_one)
        for role, name in ROLES.items():
            action = self.restore_menu.addAction(name)
            action.setData(role)
        self.restore_menu.triggered.connect(lambda action: self._restore(action.data()))
        self.restore_one.clicked.connect(
            lambda: self.restore_menu.popup(self.restore_one.mapToGlobal(self.restore_one.rect().bottomLeft()))
        )
        heading.addWidget(self.restore_one)
        self.layout_main.addLayout(heading)
        buttons = _box(QHBoxLayout())
        self.roles = {}
        for key, label in ROLES.items():
            btn = RoleButton(key, label)
            btn.clicked.connect(partial(self._assign, key))
            self.roles[key] = btn
            buttons.addWidget(btn, 1)
        self.layout_main.addLayout(buttons)
        foot = _box(QHBoxLayout())
        self.status = _label("", "muted")
        self.status.setMinimumHeight(21)
        foot.addWidget(self.status, 1)
        self.cancel_button = _button("Cancel")
        self.cancel_button.setFixedHeight(t.CONTROL_H)
        self.cancel_button.setStyleSheet("padding:1px 9px; font-size:12px;")
        self.cancel_button.clicked.connect(self._cancel_job)
        self.cancel_button.hide()
        foot.addWidget(self.cancel_button)
        self.notice = QFrame()
        self.notice.setFixedHeight(t.BUTTON_H)
        self.notice.setStyleSheet(f"QFrame {{background:{t.ACTIVE}; border-radius:5px;}}")
        notice_layout = QHBoxLayout(self.notice)
        notice_layout.setContentsMargins(10, 4, 4, 4)
        notice_layout.setSpacing(9)
        notice = QLabel("In-game music is off")
        notice.setStyleSheet(f"color:{t.PALE}; font-size:12px;")
        mark = QLabel()
        mark.setPixmap(icon("mute", t.GOLD).pixmap(15, 15))
        notice_layout.addWidget(mark)
        notice_layout.addWidget(notice)
        self.enable_button = _button("Turn music on", "primary")
        self.enable_button.setFixedHeight(t.CONTROL_H)
        self.enable_button.setStyleSheet("padding:2px 8px; font-size:12px;")
        self.enable_button.clicked.connect(self._enable)
        notice_layout.addWidget(self.enable_button)
        self.header.actions.insertWidget(0, self.notice)
        self.notice.hide()
        self.layout_main.addLayout(foot)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.refresh_game()
        if not self._loaded or not self.albums:
            self._load_catalog()

    def hideEvent(self, event):
        self.timer.stop()
        self._stop_preview()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        columns = 6 if width >= 1480 else 4 if width >= 1000 else 3
        compact = width < 1080 or self.height() < 720
        wide = columns == 6
        self.layout_main.setSpacing(t.SM if compact else t.MD)
        self.body.setSpacing(t.MD)
        self.search.setFixedWidth(180 if width < 1000 else 220)
        self.body.setStretch(0, 73 if wide else 70)
        self.body.setStretch(1, 27 if wide else 30)
        for row in self.track_rows:
            row.setFixedHeight(38 if wide else 29 if compact else 33)
        for btn in self.roles.values():
            btn.setFixedHeight(76 if wide else 60 if compact else 65)
        if columns != self.columns or not self._grid_columns:
            self.columns = columns
            self.page_index = 0
            self._gallery()
        self._update_track_rows()

    def _load_catalog(self):
        self.core.jobs.run(
            self.service.catalog, key="music.catalog", on_done=self._catalog_ready, on_error=self._catalog_error
        )

    def _catalog_ready(self, albums):
        self.albums = tuple(sorted(albums, key=lambda a: not a.track_count))
        self._loaded = True
        self._catalog_failed = False
        self._gallery()
        self._apply_shot()

    def _catalog_error(self, error):
        self._catalog_failed = True
        self.summary.setText("Catalog unavailable")
        self._gallery()
        self._message(str(error) or "Could not load the soundtrack catalog.", error=True)
        self._apply_shot()

    def refresh_game(self):
        if self._writing:
            return
        if self.game and self.game.install != self.core.installs.current():
            self._stop_preview()
            self.game = None
            self._update_roles()
        self._discovery_generation += 1
        generation = self._discovery_generation
        service = self.service
        self.core.jobs.run(
            lambda ctx: service.game_state(),
            key="music.discovery",
            on_done=lambda state: self._game_ready(state) if generation == self._discovery_generation else None,
            on_error=lambda error: self._read_error(error) if generation == self._discovery_generation else None,
        )

    def _game_ready(self, state):
        if state.install != self.core.installs.current():
            self.refresh_game()
            return
        changed = self.game is None or self.game.install != state.install
        self.game = state
        if changed:
            self._default_message()
        if self.local_tab.isChecked():
            self._gallery()
        self._update_roles()
        self._apply_shot()

    def _read_error(self, error):
        self.game = None
        self._update_roles()
        self._message(str(error), error=True)

    def _change_tab(self, _button):
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText("Find a role or track" if self.local_tab.isChecked() else "Find a soundtrack")
        self.search.setAccessibleName(self.search.placeholderText())
        self._change_filter()

    def _change_filter(self, *_):
        self.page_index = 0
        self._gallery()

    def _local_albums(self):
        if not self.game:
            return []
        groups = defaultdict(list)
        for track in self.game.local.tracks:
            groups[track.role].append(track)
        return [
            replace(
                self.game.local,
                name=ROLES.get(role, role.capitalize()),
                game=ROLES.get(role, role.capitalize()),
                tracks=tuple(tracks),
                track_count=len(tracks),
            )
            for role, tracks in groups.items()
        ]

    def _gallery(self):
        local = self.local_tab.isChecked()
        items = self._local_albums() if local else self.albums
        query = self.search.text().strip().casefold()
        if local:
            if query:
                groups = []
                for item in items:
                    matches = tuple(
                        track
                        for track in item.tracks
                        if query in track.title.casefold() or query in track_label(track.title).casefold()
                    )
                    if query in item.name.casefold():
                        groups.append(item)
                    elif matches:
                        groups.append(replace(item, tracks=matches, track_count=len(matches)))
                items = groups
            count = self.game.local.track_count if self.game else 0
            self.summary.setText(f"{count} local track{'s' if count != 1 else ''} in your EDOPro music folders")
        else:
            if self._loaded:
                size = len(self.albums)
                playable = sum(a.track_count > 0 for a in self.albums)
                self.summary.setText(f"{size} soundtrack{'s' if size != 1 else ''}  ·  {playable} with audio")
            if query:
                items = [a for a in items if query in f"{a.name} {a.game} {a.year} {a.platform}".casefold()]
        capacity = self.columns * 2
        pages = max(1, (len(items) + capacity - 1) // capacity)
        self.page_index = min(pages - 1, max(0, self.page_index))
        visible = items[self.page_index * capacity : (self.page_index + 1) * capacity]
        self.page_number.setText(f"{self.page_index + 1} / {pages}")
        self.prev_page.setEnabled(self.page_index > 0)
        self.next_page.setEnabled(self.page_index < pages - 1)
        if self._grid_columns != self.columns:
            for col in range(6):
                self.grid.setColumnStretch(col, int(col < self.columns))
            for i, tile in enumerate(self.tiles):
                self.grid.removeWidget(tile)
                if i < capacity:
                    self.grid.addWidget(tile, i // self.columns, i % self.columns)
                    tile.columns = self.columns
            self.grid.setRowStretch(0, 1)
            self.grid.setRowStretch(1, 1)
            self._grid_columns = self.columns
        for i, tile in enumerate(self.tiles):
            tile.setVisible(i < len(visible))
            if i < len(visible):
                tile.set_album(visible[i], self._pixmap(visible[i]))
        self.gallery_stack.setCurrentIndex(0 if visible else 1)
        if not visible:
            self.empty_title.setText(
                "No matching soundtracks" if query else "No local music" if local else "No soundtracks available"
            )
            if query:
                hint = "Try another title, role or platform."
            elif local:
                hint = "Tracks in the selected game's sound/BGM folders appear here."
            else:
                hint = "Open Music again to check the pack catalog."
            if not local and not self._loaded and not self._catalog_failed:
                self.empty_title.setText("Loading soundtracks…")
                hint = ""
            self.empty_hint.setText(hint)
        match = next((a for a in visible if _same_album(a, self.album)), None)
        if not match or (local and match.tracks != self.album.tracks):
            self._select(match or (visible[0] if visible else None), autoplay=False)
        self._mark_selected()

    def _pixmap(self, album):
        if not album or not album.cover:
            return QPixmap()
        if album.cover not in self._pixmaps:
            self._pixmaps[album.cover] = QPixmap(str(album.cover))
        return self._pixmaps[album.cover]

    def _tile_click(self, index):
        self._select(self.tiles[index].album, autoplay=True)

    def _tile_move(self, index, steps):
        tiles = [tile for tile in self.tiles if tile.isVisible()]
        new = index + steps
        if 0 <= new < len(tiles):
            tiles[new].setFocus()

    def _page_by(self, step):
        self.page_index += step
        self._gallery()

    def _mark_selected(self):
        for tile in self.tiles:
            tile.selected = bool(_same_album(tile.album, self.album))
            tile.update()

    def _select(self, album, *, autoplay):
        self._stop_preview()
        self.album = album
        self.track_page = 0
        self.track = album.tracks[0] if album and album.tracks else None
        self._mark_selected()
        self._update_detail()
        self._update_roles()
        if not self._writing:
            self._default_message()
        if not autoplay or not album:
            return
        # A slim published catalog may only carry track counts. Resolve the manifest on selection.
        if not album.tracks and album.pack and album.pack.meta.get("track_count") != 0:
            generation = self._generation
            self._set_pending(True, "Loading tracks…")
            self._preview_job = self.core.jobs.run(
                partial(self.service.load_album, album),
                key="music.preview",
                on_done=lambda resolved: self._album_ready(generation, resolved),
                on_error=lambda exc: self._preview_error(generation, exc),
                on_progress=lambda d, n, msg: self._preview_progress(generation, d, n, msg),
            )
        elif self.track:
            self._start_preview()

    def _album_ready(self, generation, album):
        if generation != self._generation or not self.isVisible():
            return
        self._set_pending(False)
        self.albums = tuple(album if a.id == album.id else a for a in self.albums)
        self._select(album, autoplay=bool(album.tracks))
        self._gallery()

    def _update_detail(self):
        album = self.album
        self.sleeve.set_image(self._pixmap(album))
        sample = bool(album and album.id.startswith("music-sample-"))
        self.album_title.setText((album.name if sample else album.game or album.name) if album else "Track preview")
        self.album_title.setToolTip(album.game if album else "")
        meta = metadata(album) if album else ""
        if sample:
            meta += "  ·  Sample pack"
        self.album_meta.setText(meta)
        self.album_meta.setVisible(bool(meta))
        self.track_count.setText(f"TRACKS   ·   {album.track_count}" if album else "TRACKS")
        self._update_track_rows()
        self._transport()

    def _update_track_rows(self):
        tracks = self.album.tracks if self.album else ()
        offset = self.track_page * _TRACKS_PER_PAGE
        for i, row in enumerate(self.track_rows):
            index = offset + i
            row.setVisible(index < len(tracks))
            if index < len(tracks):
                row.set_track(index, tracks[index], tracks[index] == self.track)
        for button in (self.prev_track, self.next_track):
            button.setVisible(len(tracks) > _TRACKS_PER_PAGE)
        self.prev_track.setEnabled(offset > 0)
        self.next_track.setEnabled(offset + _TRACKS_PER_PAGE < len(tracks))
        self.track_hint.setVisible(not tracks)
        if not tracks:
            if not self.album:
                hint = "Select a soundtrack to see its tracks."
            elif self.album.track_count:
                hint = "Select this cover to load its tracks."
            else:
                hint = "This catalog entry has cover art only."
            self.track_hint.setText(hint)

    def _track_page_by(self, step):
        self.track_page += step
        self._update_track_rows()

    def _track_click(self, i):
        self.track = self.album.tracks[self.track_page * _TRACKS_PER_PAGE + i]
        self._update_track_rows()
        self._update_roles()
        self._start_preview()

    def _start_preview(self):
        if not self.track or not self.isVisible():
            return
        self._stop_preview()
        generation = self._generation
        self._set_pending(True, "Opening preview…")
        self._preview_job = self.core.jobs.run(
            partial(_prepare_preview, self.service, self.player, self.album, self.track),
            key="music.preview",
            on_done=lambda result: self._preview_ready(generation, *result),
            on_error=lambda exc: self._preview_error(generation, exc),
            on_progress=lambda d, n, msg: self._preview_progress(generation, d, n, msg),
        )

    def _preview_ready(self, generation, prepared, cached):
        if self._generation != generation or not self.isVisible():
            prepared.close()
            return
        self._set_pending(False)
        if cached:
            self._mark_cached(self.album.id)
        try:
            self.player.start(prepared)
        except PreviewError as exc:
            self._preview_error(generation, exc)
            return
        self._default_message()
        self._transport()

    def _mark_cached(self, pack_id):
        updated = next((replace(a, cached=True) for a in self.albums if a.id == pack_id and not a.cached), None)
        if updated:
            self.albums = tuple(updated if a.id == pack_id else a for a in self.albums)
            if self.album and self.album.id == pack_id:
                self.album = updated
            self._gallery()

    def _preview_error(self, generation, exc):
        if generation == self._generation:
            self._set_pending(False)
            self._message(str(exc) or "Preview cancelled.", error=not isinstance(exc, CancelledError))
            self._transport()

    def _preview_progress(self, generation, done, total, message):
        if self._generation == generation and not self._writing:
            self._job_progress(done, total, message)

    def _job_progress(self, done, total, message):
        self._message(f"{message} · {int(done * 100 / total)}%" if total else message)

    def _set_pending(self, value, message=""):
        self._pending_audio = value
        self.cancel_button.setVisible(value or self._writing)
        if message:
            self._message(message)
        self._transport()

    def _stop_preview(self):
        self._generation += 1
        if self._preview_job:
            self._preview_job.cancel()
            self._preview_job = None
        self.player.stop()
        self._set_pending(False)

    def _toggle_preview(self):
        try:
            if self.player.playing:
                self.player.pause()
            elif self.player.paused:
                self.player.resume()
            elif self.track:
                self._start_preview()
        except PreviewError as exc:
            self._message(str(exc), error=True)
        self._transport()

    def _toggle_mute(self):
        self.player.set_muted(not self.player.muted)
        self.mute_button.setIcon(icon("mute" if self.player.muted else "volume"))
        hint = "Unmute preview" if self.player.muted else "Mute preview"
        self.mute_button.setAccessibleName(hint)
        self.mute_button.setToolTip(hint)

    def _transport(self):
        playing = self.player.playing
        state = (playing, self.player.paused, self._pending_audio)
        if state != self._last_player_state:
            self.play_button.setIcon(icon("pause" if playing else "play", t.GOLD if playing else t.TEXT))
            self.play_button.setText("Pause" if playing else "Resume" if self.player.paused else "Preview")
            self._last_player_state = state
        self.play_button.setEnabled(bool(self.track) and not self._pending_audio)
        self.mute_button.setEnabled(bool(self.track))
        total = self.player.duration or (self.track.duration_seconds if self.track else 0)
        pos = self.player.position
        self.clock.setText(f"{mmss(pos)} / {mmss(total)}" if total else "")
        self.progress.setValue(min(1000, int(pos / total * 1000)) if total else 0)
        if self.player.error:
            self._message(self.player.error, error=True)
            self.player.stop()

    def _update_roles(self):
        game = self.game
        installed = game is not None and game.install is not None
        title = ""
        if self.track:
            title = track_label(self.track.title) if self.track.role else self.track.title
        for role, btn in self.roles.items():
            current = game.assignments.get(role, "Original") if game else "Original"
            pool = game.pools.get(role, ()) if game else ()
            if not pool:
                current = "No tracks" if installed else "Select a game folder"
            btn.set_assignment(current, pool, title, bool(game and role in game.managed_roles and pool))
            btn.setEnabled(bool(installed and self.track and not self._writing))
        self.restore_button.setEnabled(bool(game and game.can_restore and not self._writing))
        self.restore_one.setVisible(bool(game and game.can_restore))
        self.restore_one.setEnabled(not self._writing)
        for action in self.restore_menu.actions():
            action.setEnabled(bool(game and action.data() in game.managed_roles))
        self.notice.setVisible(bool(game and game.music_off))
        self.enable_button.setEnabled(not self._writing)
        if not self.status.text():
            self._default_message()

    def _default_message(self):
        if self.game and self.game.install is None:
            self._message("Select an EDOPro folder to assign music.")
        else:
            self._message(
                "Choose a role to assign this track. Changes take effect at the next game start." if self.track else ""
            )

    def _message(self, text, *, error=False):
        self.status.setStyleSheet(f"color: {t.RED if error else t.MUTED};")
        self.status.setText(text)
        self.status.setToolTip(text)
        self.status.setAccessibleName(text)

    def _assign(self, role):
        if not self.track or not self.game or not self.game.install:
            return
        install, track, album = self.game.install, self.track, self.album
        title = track_label(track.title) if track.role else track.title
        self._write(
            partial(self.service.assign, album, track, role, install),
            install,
            f"Assigning {title}…",
            f"{ROLES[role]} now includes {title}. Restart EDOPro to hear it.",
            pack_id=album.id if album.pack else None,
        )

    def _restore(self, role=None):
        if self.game and self.game.install:
            install = self.game.install
            message = f"Original music restored for {ROLES[role]}." if role else "Original music restored."
            self._write(partial(self.service.restore, install, role=role), install, "Restoring music…", message)

    def _enable(self):
        if self.game and self.game.install:
            install = self.game.install
            self._write(partial(self.service.enable_music, install), install, "Turning music on…", "In-game music is on.")

    def _write(self, work, install, pending, success, *, pack_id=None, confirmed=False):
        if self._writing:
            return
        self._stop_preview()
        self._writing = True
        self._discovery_generation += 1
        self.cancel_button.show()
        self._update_roles()
        self._message(pending)

        def finish():
            self._writing = False
            self.cancel_button.setVisible(self._pending_audio)
            self._update_roles()

        def done(state):
            finish()
            if pack_id:
                self._mark_cached(pack_id)
            if install != self.core.installs.current():
                self.refresh_game()
                return
            self._game_ready(state)
            if state.conflicts:
                self._message(f"{success} Preserved {len(state.conflicts)} externally changed file(s).")
            else:
                self._message(success)

        def failed(exc):
            finish()
            if (
                isinstance(exc, ConfirmClosedError)
                and not confirmed
                and self.isVisible()
                and install == self.core.installs.current()
            ):
                answer = QMessageBox.question(
                    self,
                    "Is EDOPro closed?",
                    "EDOPal could not check this game. Is EDOPro closed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._write(work, install, pending, success, pack_id=pack_id, confirmed=True)
                    return
            self._message(str(exc) or "Music change cancelled.", error=not isinstance(exc, CancelledError))
            self.refresh_game()

        self._write_job = self.core.jobs.run(
            partial(work, confirmed_closed=confirmed),
            key="music.write",
            on_done=done,
            on_progress=self._job_progress,
            on_error=failed,
        )

    def _cancel_job(self):
        if self._writing and self._write_job:
            self._write_job.cancel()
            self._message("Cancelling music change…")
        else:
            self._stop_preview()
            self._default_message()

    def preview_state(self, name):
        """Render named states through the ordinary controls on the preview harness's isolated game."""
        self._shot = name
        if self.isVisible():
            self._apply_shot()

    def _apply_shot(self):
        if not self._shot or not self.isVisible() or not self._loaded or not self.game:
            return
        name, self._shot = self._shot, None
        if self.tiles[0].isVisible():
            self.tiles[0].setFocus(Qt.FocusReason.OtherFocusReason)
        if name in ("music-off", "off") and not self.game.music_off:
            self.game = replace(self.game, music_off=True)
            self._update_roles()
        if name in ("playing", "preview", "assigned"):
            import miniaudio

            self.player.stop()
            self.player = PreviewPlayer(backends=[miniaudio.Backend.NULL])
            playable = [album for album in self.albums if album.track_count]
            if playable:
                index = min(1, len(playable) - 1) if name in ("playing", "preview") else 0
                self.tiles[index].setFocus(Qt.FocusReason.OtherFocusReason)
                self._select(playable[index], autoplay=name != "assigned")
                if name == "assigned" and self.track:
                    self._assign("deck")
