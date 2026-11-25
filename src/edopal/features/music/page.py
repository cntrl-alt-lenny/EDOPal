"""Browse and preview game soundtracks, pin tracks, and apply music setups."""

from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import replace
from functools import partial

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from edopal.ui import theme as t
from edopal.ui.widgets import page_layout

from .library import ALL, DEFAULTS, FAVORITES, TABS, MusicLibrary, bind_library
from .player import PreviewError, PreviewPlayer, audio_profile
from .service import ROLES, ConfirmClosedError, MusicService
from .widgets import Banner, Carousel, CoverTile, SceneRow, TrackRow, Waveform, icon, icon_button, mmss, track_label


def _label(text="", size=13, *, muted=False, heading=False, wrap=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    family = "font-family:Outfit,Inter,sans-serif;font-weight:600;" if heading else ""
    label.setStyleSheet(f"background:transparent;color:{t.MUTED if muted else t.INK};font-size:{size}px;{family}")
    if heading:
        font = label.font()
        font.setVariableAxis(QFont.Tag("wght"), 600)
        label.setFont(font)
    return label


def _button(text):
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _box(layout, spacing=0):
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def _star():
    button = _button("☆")
    button.setFixedSize(25, 26)
    button.setStyleSheet(
        f"QPushButton {{background:transparent;border:0;padding:0;color:{t.GOLD};font-size:19px}}"
        f"QPushButton:focus {{border:1px solid {t.GOLD}}}"
    )
    return button


def _prepare_preview(service, player, album, track, ctx):
    path, cached = service.preview(album, track, ctx)
    profile = audio_profile(path)
    if ctx.cancelled:
        raise CancelledError()
    return player.prepare(path), cached, profile


class MusicPage(QWidget):
    def __init__(self, core, *, player=None):
        super().__init__()
        self.core = core
        self.service = MusicService(core)
        self.library = MusicLibrary(core.settings)
        self.player = player if player is not None else PreviewPlayer()
        self.albums = self.catalog = ()
        self.defaults = self.game = self.album = self.track = None
        self._generation = self._discovery_generation = 0
        self._preview_job = self._write_job = None
        self._pending_audio = self._writing = self._loaded = self._catalog_failed = False
        self._pixmaps, self.banners = {}, {}
        self.profile_duration = 0
        self._shot = self._last_player_state = None
        self.active_tab = ALL
        self.track_page = 0
        self.tiles = []
        self.setObjectName("canvas")
        self.layout_main = page_layout(self)
        self._build_header()
        self._build_tabs()
        self.stack = QStackedWidget()
        self.layout_main.addWidget(self.stack, 1)
        browse = QWidget()
        self.browse_layout = _box(QVBoxLayout(browse), 16)
        self.stack.addWidget(browse)
        self._build_gallery()
        panel = QFrame()
        panel.setObjectName("musicPanel")
        panel.setStyleSheet("QFrame#musicPanel {background:#28282b;border:1px solid #424144;border-radius:6px}")
        self.body = _box(QHBoxLayout(panel))
        self.body.setContentsMargins(1, 1, 1, 1)
        self.browse_layout.addWidget(panel, 1)
        self._build_detail()
        self._build_assignments()
        self._build_favorites()
        self._build_status()
        self._update_detail()
        self._update_roles()
        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self._transport)

    def _build_header(self):
        bar = _box(QHBoxLayout(), 12)
        self.layout_main.addLayout(bar)
        bar.addWidget(_label("Music", 38, heading=True), 1)
        self.notice = self.enable_button = _button("Turn In-Game Music On")
        self.enable_button.setToolTip("Music is disabled in EDOPro.")
        self.enable_button.clicked.connect(self._enable)
        bar.addWidget(self.enable_button)
        self.setup_button = _button("Music Setup: Default")
        self.setup_button.setFixedSize(183, 34)
        self.setup_button.setToolTip("Assignments auto-save to the active setup.")
        self.setup_menu = QMenu(self.setup_button)
        self.setup_menu.setFixedWidth(237)
        self.setup_menu.setStyleSheet(
            "QMenu {background:#2c2b2d;border:1px solid #696051;border-radius:5px;padding:5px;}"
            "QMenu::item {padding:7px 10px 7px 24px;min-height:17px;}"
            "QMenu::item:selected,QMenu::item:checked {background:#40382c;color:#ebc987;}"
            "QMenu::item:disabled {color:#aaa9ab;}"
            "QMenu::separator {height:1px;background:#464448;margin:4px;}"
        )
        self.setup_menu.aboutToShow.connect(self._setup_actions)
        self.setup_button.setIcon(icon("down", t.TEXT))
        self.setup_button.setIconSize(QSize(12, 12))
        self.setup_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setup_button.clicked.connect(self._open_setup_menu)
        bar.addWidget(self.setup_button)
        self.search = QLineEdit()
        self.search.setFixedSize(165, 34)
        self.search.setStyleSheet("padding:3px 5px;font-size:13px")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Search games")
        self.search.setAccessibleName("Search by game name")
        self.search.addAction(
            QAction(icon("search", t.MUTED), "Search", self.search), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self._change_filter)
        bar.addWidget(self.search)

    def _build_tabs(self):
        rail = QFrame()
        rail.setObjectName("musicTabs")
        rail.setStyleSheet("QFrame#musicTabs {border-bottom:1px solid #454548}")
        layout = _box(QHBoxLayout(rail), 2)
        self.tab_group = QButtonGroup(self)
        self.tabs = {}
        for name in TABS:
            button = _button(name)
            button.setCheckable(True)
            button.setFixedHeight(33)
            button.setStyleSheet(
                "QPushButton {border:0;border-radius:0;background:transparent;padding:0 7px;"
                f"color:{t.TEXT};font-size:13px}}"
                f"QPushButton:checked {{color:{t.GOLD};border-bottom:2px solid {t.GOLD}}}"
                f"QPushButton:hover,QPushButton:focus {{background:{t.HOVER}}}"
            )
            self.tabs[name] = button
            self.tab_group.addButton(button)
            layout.addWidget(button)
        self.local_tab = self.tabs[DEFAULTS]
        self.tabs[ALL].setChecked(True)
        self.tab_group.buttonClicked.connect(lambda button: self._change_tab(button.text()))
        layout.addStretch(1)
        self.summary = _label("", 12, muted=True)
        self.summary.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.summary)
        self.layout_main.addWidget(rail)

    def _build_gallery(self):
        self.gallery_stack = QStackedWidget()
        self.carousel = Carousel()
        self.grid_host = QWidget()
        self.grid = _box(QGridLayout(self.grid_host))
        self.grid_host.installEventFilter(self.carousel)
        self.carousel.setWidget(self.grid_host)
        self.gallery_stack.addWidget(self.carousel)
        self.empty_title = _label("", 17, muted=True)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gallery_stack.addWidget(self.empty_title)
        self.browse_layout.addWidget(self.gallery_stack)

    def _build_detail(self):
        self.detail = Banner()
        layout = _box(QVBoxLayout(self.detail), 3)
        layout.setContentsMargins(16, 16, 16, 12)
        self.subtitle = _label("", 12)
        self.album_title = _label("", 28, heading=True, wrap=True)
        self.album_meta = _label("", 12)
        for label in (self.subtitle, self.album_title, self.album_meta):
            layout.addWidget(label)
        layout.addStretch(1)
        bar = _box(QHBoxLayout(), 4)
        self.track_count = _label("Tracks", 13)
        bar.addWidget(self.track_count, 1)
        self.prev_track = icon_button("left", "Previous Tracks")
        self.next_track = icon_button("right", "Next Tracks")
        for button, step in ((self.prev_track, -1), (self.next_track, 1)):
            button.setFixedSize(22, 20)
            button.clicked.connect(partial(self._track_page_by, step))
            bar.addWidget(button)
        layout.addLayout(bar)
        self.track_rows, self.stars = [], []
        for i in range(2):
            line = _box(QHBoxLayout())
            row = TrackRow()
            row.clicked.connect(partial(self._track_click, i))
            star = _star()
            star.clicked.connect(partial(self._pin_row, i))
            self.track_rows.append(row)
            self.stars.append(star)
            line.addWidget(row, 1)
            line.addWidget(star)
            layout.addLayout(line)
        self.track_hint = _label("", 14, muted=True)
        layout.addWidget(self.track_hint)
        self.transport = QWidget()
        controls = _box(QHBoxLayout(self.transport), 10)
        self.play_button = icon_button("play", "Play Preview")
        self.play_button.setFixedSize(29, 29)
        self.play_button.setStyleSheet(f"background:{t.GOLD};border-radius:14px;padding:0")
        self.play_button.clicked.connect(self._toggle_preview)
        controls.addWidget(self.play_button)
        wave = _box(QVBoxLayout())
        self.progress = Waveform()
        wave.addWidget(self.progress)
        times = _box(QHBoxLayout())
        self.clock = _label("", 10)
        self.duration = _label("", 10, muted=True)
        self.duration.setAlignment(Qt.AlignmentFlag.AlignRight)
        times.addWidget(self.clock, 1)
        times.addWidget(self.duration, 1)
        wave.addLayout(times)
        controls.addLayout(wave, 1)
        self.mute_button = icon_button("volume", "Mute Preview")
        self.mute_button.setFixedSize(20, 20)
        self.mute_button.setStyleSheet("background:transparent;border:0;padding:0")
        self.mute_button.clicked.connect(self._toggle_mute)
        controls.addWidget(self.mute_button)
        layout.addWidget(self.transport)
        self.body.addWidget(self.detail, 445)

    def _build_assignments(self):
        side = QWidget()
        layout = _box(QVBoxLayout(side), 0)
        layout.setContentsMargins(16, 13, 12, 9)
        heading = _box(QHBoxLayout(), 4)
        self.scene_heading = _label("Scene Assignments", 20, heading=True)
        heading.addWidget(self.scene_heading, 1)
        self.restore_button = icon_button("restore", "Restore Original Music")
        self.restore_button.clicked.connect(lambda: self._restore())
        self.restore_one = icon_button("down", "Restore One Scene")
        self.restore_menu = QMenu(self.restore_one)
        for role, name in ROLES.items():
            action = self.restore_menu.addAction(name)
            action.setData(role)
        self.restore_menu.triggered.connect(lambda action: self._restore(action.data()))
        self.restore_one.clicked.connect(
            lambda: self.restore_menu.popup(self.restore_one.mapToGlobal(self.restore_one.rect().bottomLeft()))
        )
        for btn in (self.restore_button, self.restore_one):
            btn.setFixedSize(23, 23)
            heading.addWidget(btn)
        layout.addLayout(heading)
        layout.addSpacing(9)
        header = SceneRow()
        header.setFixedHeight(26)
        layout.addWidget(header)
        self.roles = {}
        for role, title in ROLES.items():
            row = SceneRow(role, title)
            self.roles[role] = row
            row.clicked.connect(partial(self._assign, role))
            layout.addWidget(row, 1)
        self.scene_layout = layout
        self.body.addWidget(side, 555)

    def _build_status(self):
        self.feedback = QWidget()
        line = _box(QHBoxLayout(self.feedback), 8)
        self.status = _label("", 12, muted=True)
        self.cancel_button = _button("Cancel")
        self.cancel_button.clicked.connect(self._cancel_job)
        self.cancel_button.hide()
        line.addWidget(self.status, 1)
        line.addWidget(self.cancel_button)
        self.feedback.hide()
        self.layout_main.addWidget(self.feedback)

    def _build_favorites(self):
        page = QWidget()
        layout = _box(QVBoxLayout(page), 8)
        self.stack.addWidget(page)
        layout.addWidget(_label("Pinned Tracks", 23, heading=True))
        head = _box(QHBoxLayout(), 8)
        head.setContentsMargins(7, 17, 7, 0)
        head.addSpacing(29)
        for label, stretch in zip(("Track", "Soundtrack", "Game", "Series", "Scene"), (22, 21, 30, 12, 11), strict=True):
            head.addWidget(_label(label, 12, muted=True), stretch)
        head.addSpacing(25)
        layout.addLayout(head)
        self.fav_scroll = QScrollArea()
        self.fav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.fav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fav_scroll.setWidgetResizable(True)
        self.fav_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background:transparent;border:0}")
        layout.addWidget(self.fav_scroll, 1)
        self.fav_host = QWidget()
        self.fav_list = _box(QVBoxLayout(self.fav_host), 2)
        self.fav_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.fav_scroll.setWidget(self.fav_host)
        self.fav_plays = {}

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.width() > 1400 and self.height() >= 870
        self.wide = wide
        margin = 36 if wide else 32 if self.width() >= 940 else 16
        self.layout_main.setContentsMargins(margin, 24 if self.height() >= 650 else 12, margin, 23)
        self.layout_main.setSpacing(23 if wide else 14)
        self.body.setStretch(0, 455 if wide else 445)
        self.body.setStretch(1, 545 if wide else 555)
        pad = 24 if wide else 16
        self.detail.layout().setContentsMargins(pad, 25 if wide else 16, pad, 14)
        self.scene_layout.setContentsMargins(pad, 21 if wide else 13, pad - 4, 10)
        self.album_title.setStyleSheet(
            f"background:transparent;font-family:Outfit;font-weight:600;font-size:{35 if wide else 28}px"
        )
        self.scene_heading.setStyleSheet(
            f"background:transparent;font-family:Outfit;font-weight:600;font-size:{24 if wide else 20}px"
        )
        self.browse_layout.setSpacing(26 if wide else 15)
        self._size_shelf()
        if self.album and self.active_tab != FAVORITES:
            QTimer.singleShot(0, self._reveal_selected)
        for row in self.track_rows:
            row.setFixedHeight(36 if wide else 29)

    def _size_shelf(self):
        wide = getattr(self, "wide", False)
        cols, gap = (13, 12) if wide else (9, 9)
        margins = self.layout_main.contentsMargins()
        width = self.width() - margins.left() - margins.right()
        tile_w = max(77, (width - 20 - cols * gap) // cols)
        tile_h = (141 + 57) if wide else (80 + 45) if self.height() >= 640 else 104
        self.grid.setHorizontalSpacing(gap)
        self.grid.setVerticalSpacing(7 if wide else 6)
        items = [tile for tile in self.tiles if not tile.isHidden()]
        for tile in items:
            tile.wide = wide
            tile.setFixedSize(tile_w, tile_h)
        columns = (len(items) + 1) // 2
        rows_h = tile_h * (2 if len(items) > 1 else 1) + (self.grid.verticalSpacing() if len(items) > 1 else 0)
        self.grid_host.setFixedSize(max(1, columns * (tile_w + gap) - gap), rows_h)
        self.gallery_stack.setFixedHeight(rows_h + 11 if items else 62)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self._set_pending(False)
        self.refresh_game()
        if not self._loaded or not self.catalog:
            self.core.jobs.run(
                self.service.catalog, key="music.catalog", on_done=self._catalog_ready, on_error=self._catalog_error
            )
        if not self.banners:
            self.core.jobs.run(
                self.service.banners, key="music.banners", on_done=self._banners_ready, on_error=lambda _: None
            )

    def hideEvent(self, event):
        self.timer.stop()
        self._stop_preview(update_ui=False)
        super().hideEvent(event)

    def _catalog_ready(self, albums):
        self.catalog = tuple(sorted(albums, key=lambda a: (a.year or 0, a.name.casefold())))
        self._loaded = True
        self._catalog_failed = False
        self._bind()
        self._apply_shot()

    def _banners_ready(self, paths):
        self.banners = paths
        self._update_detail()
        if self.active_tab == FAVORITES:
            self._favorites()

    def _catalog_error(self, error):
        self._catalog_failed = True
        self._gallery()
        self._message(str(error) or "Could not load the soundtrack catalog.", error=True)

    def _bind(self):
        self.albums, self.defaults = bind_library(self.catalog, self.game)
        self._gallery()

    def _all_albums(self):
        return (*self.albums, self.defaults) if self.defaults else self.albums

    def _pixmap(self, path):
        if path is None:
            return QPixmap()
        if path not in self._pixmaps:
            self._pixmaps[path] = QPixmap(str(path))
        return self._pixmaps[path]

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
        self.game = state
        self._bind()
        self._update_setup_label()
        self._update_roles()
        self._apply_shot()

    def _read_error(self, error):
        self.game = None
        self._bind()
        self._update_roles()
        self._message(str(error), error=True)

    def _change_tab(self, name):
        self.active_tab = name
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText("Search favorites" if name == FAVORITES else "Search games")
        self.stack.setCurrentIndex(int(name == FAVORITES))
        self._stop_preview()
        self._change_filter()

    def _change_filter(self, *_):
        self.carousel.horizontalScrollBar().setValue(0)
        self._gallery()

    def _gallery(self):
        if self.active_tab == FAVORITES:
            self._favorites()
            return
        query = self.search.text().strip().casefold()
        items = (
            [self.defaults]
            if self.active_tab == DEFAULTS and self.defaults
            else [a for a in self.albums if self.active_tab == ALL or a.series == self.active_tab]
        )
        items = [a for a in items if query in a.game.casefold()]
        self.summary.setText(f"{len(items)} Game{'s' if len(items) != 1 else ''}")
        for i, album in enumerate(items):
            if i == len(self.tiles):
                tile = CoverTile()
                tile.installEventFilter(self.carousel)
                tile.clicked.connect(lambda checked=False, tile=tile: self._select(tile.album, autoplay=True))
                tile.moved.connect(partial(self._tile_move, i))
                self.grid.addWidget(tile, i % 2, i // 2)
                self.tiles.append(tile)
            tile = self.tiles[i]
            tile.set_album(album, self._pixmap(album.cover))
            tile.show()
        for tile in self.tiles[len(items) :]:
            tile.hide()
        self._size_shelf()
        self.gallery_stack.setCurrentIndex(0 if items else 1)
        self.empty_title.setText(
            "No matching games"
            if query
            else "No games available"
            if self._loaded or self._catalog_failed
            else "Loading soundtracks…"
        )
        match = next(
            (a for a in items if self.album and a.id == self.album.id and a.install_root == self.album.install_root), None
        )
        current_track = next((tr for tr in match.tracks if self.track and tr.id == self.track.id), None) if match else None
        if current_track:
            self.album, self.track = match, current_track
            self._update_detail()
        else:
            # Start with the game in the design when available, and reveal its jacket in the carousel.
            selected = match or next((a for a in items if a.id == "music-world-championship-2011"), None)
            selected = selected or next((a for a in items if a.track_count), items[0] if items else None)
            self._select(selected, autoplay=False)
            if selected and not match:
                QTimer.singleShot(0, self._reveal_selected)
        self._mark_selected()

    def _reveal_selected(self):
        for tile in self.tiles:
            if tile.selected and tile.isVisible():
                columns_before = 12 if self.wide else 7
                step = tile.width() + self.grid.horizontalSpacing()
                self.carousel.horizontalScrollBar().setValue(tile.x() - columns_before * step)
                break

    def _tile_move(self, index, step):
        target = index + step
        if 0 <= target < len(self.tiles) and self.tiles[target].isVisible():
            self.tiles[target].setFocus()
            self.carousel.ensureWidgetVisible(self.tiles[target])

    def _mark_selected(self):
        for tile in self.tiles:
            tile.selected = bool(self.album and tile.album and tile.album.id == self.album.id)
            tile.update()

    def _select(self, album, *, autoplay):
        self._stop_preview()
        self.album = album
        self.track_page = 0
        self.track = album.tracks[0] if album and album.tracks else None
        self.profile_duration, self.progress.values = 0, ()
        self._mark_selected()
        self._update_detail()
        self._update_roles()
        if not self._writing:
            self._default_message()
        if not autoplay or not album:
            if self.track and self.track.role:
                generation, track, service = self._generation, self.track, self.service
                self.core.jobs.run(
                    lambda ctx: audio_profile(service.preview(album, track, ctx)[0]),
                    key="music.waveform",
                    on_done=lambda profile: self._set_profile(profile) if generation == self._generation else None,
                    on_error=lambda _: None,
                )
            return
        if album.pack and album.track_count > len(album.tracks):
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
        self.catalog = tuple(album if a.id == album.id else a for a in self.catalog)
        self._bind()
        self._select(next(a for a in self.albums if a.id == album.id), autoplay=bool(album.tracks))

    def _update_detail(self):
        a = self.album
        self.detail.set_image(self._pixmap(self.banners.get(a.id) if a else None))
        title = a.name if a else "Select a Game"
        group = ""
        if a and a.game.endswith(": " + title):
            group = a.game[: -(len(title) + 2)].removeprefix("Yu-Gi-Oh! ").removeprefix("5D's ")
        self.subtitle.setText(group)
        self.subtitle.setVisible(bool(group))
        self.album_title.setText(title)
        self.album_title.setToolTip(a.game if a else "")
        self.album_meta.setText("  ·  ".join(str(v) for v in (a.series, a.platform, a.year) if v) if a else "")
        self._update_track_rows()
        self._transport()

    def _current_refs(self):
        if not self.game:
            return {}
        data = self.library.setups(self.game.install)
        return self.library.snapshot(self.game, self._all_albums(), data["choices"][data["active"]])

    def _scene(self, album, track):
        if not self.game:
            return ""
        refs = self._current_refs()
        return ", ".join(
            ROLES[role]
            for role in ROLES
            if refs.get(role) == [album.id, track.id] or (role not in self.game.managed_roles and track.role == role)
        )

    def _update_track_rows(self):
        tracks = self.album.tracks if self.album else ()
        self.track_page = min(self.track_page, max(0, (len(tracks) - 1) // 2))
        offset = self.track_page * 2
        pins = self.library.favorites()
        for i, (row, star) in enumerate(zip(self.track_rows, self.stars, strict=True)):
            index = offset + i
            row.setVisible(index < len(tracks))
            star.setVisible(index < len(tracks))
            if index < len(tracks):
                tr = tracks[index]
                row.set_track(tr, tr == self.track, self._scene(self.album, tr) or "Unassigned")
                pinned = self.library.favorite_ref(self.album, tr) in pins
                star.setText("★" if pinned else "☆")
                tip = ("Remove from Favorites" if pinned else "Add to Favorites") + f" · {row.title}"
                star.setToolTip(tip)
                star.setAccessibleName(tip)
        for button in (self.prev_track, self.next_track):
            button.setVisible(len(tracks) > 2)
        self.prev_track.setEnabled(offset > 0)
        self.next_track.setEnabled(offset + 2 < len(tracks))
        self.track_count.setVisible(bool(tracks))
        self.track_hint.setVisible(not tracks and self.album is not None)
        self.track_hint.setText(
            "Select the cover to load tracks"
            if self.album and self.album.track_count
            else "No installed tracks"
            if self.active_tab == DEFAULTS
            else "Audio coming soon"
        )
        self.transport.setVisible(bool(tracks))

    def _track_page_by(self, step):
        self.track_page += step
        self._update_track_rows()

    def _track_click(self, i):
        self.track = self.album.tracks[self.track_page * 2 + i]
        self.profile_duration, self.progress.values = 0, ()
        self._update_track_rows()
        self._update_roles()
        self._start_preview()

    def _pin_row(self, index):
        self.library.toggle(self.album, self.album.tracks[self.track_page * 2 + index])
        self._update_track_rows()

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

    def _set_profile(self, profile):
        self.profile_duration, self.progress.values = profile
        self._transport()

    def _preview_ready(self, generation, prepared, cached, profile):
        if self._generation != generation or not self.isVisible():
            prepared.close()
            return
        self._set_pending(False)
        if cached:
            self._mark_cached(self.album.id)
        try:
            self.player.start(prepared)
            self._set_profile(profile)
        except PreviewError as exc:
            self._preview_error(generation, exc)
            return
        self._default_message()
        self._transport()

    def _mark_cached(self, pack_id):
        if not any(a.id == pack_id and not a.cached for a in self.catalog):
            return
        self.catalog = tuple(replace(a, cached=True) if a.id == pack_id else a for a in self.catalog)
        self._bind()

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

    def _stop_preview(self, *, update_ui=True):
        self._generation += 1
        if self._preview_job:
            self._preview_job.cancel()
            self._preview_job = None
        self.player.stop()
        self._pending_audio = False
        if update_ui:
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
            self.play_button.setIcon(icon("pause" if playing else "play", t.ON_GOLD))
            label = "Pause" if playing else "Resume" if self.player.paused else "Play Preview"
            self.play_button.setAccessibleName(label)
            self.play_button.setToolTip(label)
            self._last_player_state = state
        if self.active_tab == FAVORITES:
            for (aid, tid), button in self.fav_plays.items():
                selected = bool(self.album and self.track and self.album.id == aid and self.track.id == tid)
                active = selected and playing
                row = button.parentWidget()
                if row.property("selected") != selected:
                    row.setProperty("selected", selected)
                    row.update()
                if button.property("playing") != active:
                    button.setIcon(icon("pause" if active else "play", t.GOLD if active else t.TEXT))
                    button.setProperty("playing", active)
        self.play_button.setEnabled(bool(self.track) and not self._pending_audio)
        total = self.player.duration or self.profile_duration or (self.track.duration_seconds if self.track else 0)
        pos = self.player.position
        self.clock.setText(mmss(pos))
        self.duration.setText(mmss(total) if total else "")
        self.progress.fraction = min(1, pos / total) if total else 0
        self.progress.update()
        if self.player.error:
            self._message(self.player.error, error=True)
            self.player.stop()

    def _update_roles(self):
        game = self.game
        installed = bool(game and game.install)
        refs = self._current_refs()
        albums = self._all_albums()
        for role, btn in self.roles.items():
            pool = game.pools.get(role, ()) if game else ()
            managed = bool(game and role in game.managed_roles and pool)
            current = game.assignments[role] if pool else "No tracks" if installed else "—"
            source = next((a for a in albums if a.id == refs.get(role, [None])[0]), None)
            if source:
                track = next((tr for tr in source.tracks if tr.id == refs[role][1]), None)
                if track:
                    current = track_label(track.title)
                sources = [source]
            else:
                sources = [a for a in albums if any(tr.role == role for tr in a.tracks)]
                if len(pool) == 1 and sources:
                    current = next(tr.title for tr in sources[0].tracks if tr.role == role)
            names = [a.name if a != self.defaults else "EDOPro" for a in sources]
            years = sorted({str(a.year) for a in sources if a.year})
            name = names[0] if len(names) == 1 else f"{len(names)} Games" if names else "—"
            year = "–".join((years[0], years[-1])) if len(years) > 1 else years[0] if years else None
            btn.set_assignment(current, pool, self.track.title if self.track else "", managed or role in refs, name, year)
            btn.setEnabled(bool(installed and self.track and not self._writing))
        can_restore = bool(refs or (game and game.can_restore))
        for button in (self.restore_button, self.restore_one):
            button.setVisible(can_restore)
            button.setEnabled(can_restore and not self._writing)
        for action in self.restore_menu.actions():
            action.setEnabled(bool(game and (action.data() in game.managed_roles or action.data() in refs)))
        self.notice.setVisible(bool(game and game.music_off))
        self.enable_button.setEnabled(not self._writing)
        self.setup_button.setEnabled(installed and not self._writing)

    def _default_message(self):
        self._message("Select an EDOPro folder to assign music." if self.game and not self.game.install else "")

    def _message(self, text, *, error=False):
        self.status.setStyleSheet(f"color:{t.RED if error else t.MUTED};font-size:12px")
        self.status.setText(text)
        self.status.setToolTip(text)
        self.feedback.setVisible(bool(text) or self._pending_audio or self._writing)

    def _favorites(self):
        self.fav_plays.clear()
        while self.fav_list.count():
            row = self.fav_list.takeAt(0).widget()
            row.hide()
            row.deleteLater()
        albums = {a.id: a for a in self._all_albums()}
        query = self.search.text().strip().casefold()
        count = 0
        for aid, tid, root in self.library.favorites():
            album = albums.get(aid)
            if album is None or (root and root != str(album.install_root)) or query not in album.game.casefold():
                continue
            track = next((tr for tr in album.tracks if tr.id == tid), None)
            if not track:
                continue
            self._favorite_row(album, track)
            count += 1
        self.summary.setText(f"{count} Track{'s' if count != 1 else ''}")
        if not count:
            self.fav_list.addWidget(
                _label("No matching games" if query else "Star a track to add it to Favorites.", 14, muted=True)
            )
        self._transport()

    def _favorite_row(self, album, track):
        row = Banner(favorite=True)
        row.set_image(self._pixmap(self.banners.get(album.id)))
        row.setMinimumHeight(54)
        line = _box(QHBoxLayout(row), 8)
        line.setContentsMargins(7, 3, 7, 3)
        play = icon_button("play", f"Preview {track.title}")
        play.setFixedSize(29, 24)
        play.setStyleSheet("border:1px solid #514f50;border-radius:11px;background:#39383a;padding:2px")
        play.clicked.connect(partial(self._favorite_play, album, track))
        self.fav_plays[album.id, track.id] = play
        line.addWidget(play)
        line.addWidget(_label(track_label(track.title), 15, wrap=True), 22)
        labels = _box(QVBoxLayout())
        labels.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        labels.addWidget(_label(album.name, 14, wrap=True))
        labels.addWidget(_label(" · ".join(str(v) for v in (album.platform, album.year) if v), 11, muted=True))
        line.addLayout(labels, 21)
        game = _label(album.game, 13, wrap=True)
        game.setToolTip(album.game)
        line.addWidget(game, 30)
        line.addWidget(_label(album.series or "—", 13, muted=True, wrap=True), 12)
        scene = _button(self._scene(album, track) or "Assign…")
        scene.setStyleSheet("text-align:left;padding:0;border:0;background:transparent;font-size:12px")
        scene.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        menu = QMenu(scene)
        for key, label in ROLES.items():
            menu.addAction(label, partial(self._assign_favorite, album, track, key))
        scene.setMenu(menu)
        scene.setEnabled(bool(self.game and self.game.install and not self._writing))
        line.addWidget(scene, 11)
        star = _star()
        star.setText("★")
        star.setAccessibleName(f"Remove {track.title} from Favorites")
        star.setToolTip(star.accessibleName())
        star.clicked.connect(partial(self._unpin, album, track))
        line.addWidget(star)
        self.fav_list.addWidget(row)

    def _favorite_play(self, album, track):
        if self.album and self.album.id == album.id and self.track == track:
            self._toggle_preview()
            return
        self.album, self.track = album, track
        self.profile_duration, self.progress.values = 0, ()
        self._start_preview()

    def _unpin(self, album, track):
        self.library.toggle(album, track)
        if self.track == track and self.album == album:
            self._stop_preview()
        self._favorites()

    def _assign_favorite(self, album, track, role):
        self._stop_preview()
        self.album, self.track = album, track
        self._assign(role)

    def _update_setup_label(self):
        data = self.library.setups(self.game.install if self.game else None)
        name = "Music Setup: " + data["active"]
        self.setup_button.setText(self.setup_button.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 146))
        self.setup_button.setAccessibleName(name)
        self.setup_button.setToolTip(name + "\nAssignments auto-save to the active setup.")

    def _open_setup_menu(self):
        button = self.setup_button
        anchor = QPoint((button.width() - self.setup_menu.width()) // 2, button.height() + 4)
        self.setup_menu.popup(button.mapToGlobal(anchor))

    def _setup_actions(self):
        menu = self.setup_menu
        menu.clear()
        data = self.library.setups(self.game.install)
        for name in data["choices"]:
            action = menu.addAction(name, partial(self._switch_setup, name))
            action.setCheckable(True)
            action.setChecked(name == data["active"])
        menu.addSeparator()
        menu.addAction("Save Current as New Setup…", self._new_setup)
        menu.addAction("Rename…", self._rename_setup).setEnabled(data["active"] != "Default")
        menu.addAction("Delete", self._delete_setup).setEnabled(data["active"] != "Default")
        menu.addSeparator()
        footer = QWidgetAction(menu)
        hint = _label("Assignments auto-save to this setup.", 11, muted=True)
        hint.setContentsMargins(10, 5, 5, 5)
        footer.setDefaultWidget(hint)
        menu.addAction(footer)

    def _request_name(self, title, old=""):
        value, ok = QInputDialog.getText(self, title, "Setup Name", text=old)
        if not ok:
            return None
        name = value.strip()
        choices = self.library.setups(self.game.install)["choices"]
        if not name or any(n != old and n.casefold() == name.casefold() for n in choices):
            self._message("Choose a unique, nonempty setup name.", error=True)
            return None
        return name

    def _new_setup(self):
        name = self._request_name("Save Current as New Setup")
        if name:
            install = self.game.install
            data = self.library.setups(install)
            refs = self._current_refs()
            choices = {**data["choices"], data["active"]: refs, name: refs}
            self.library.save(install, {"active": name, "choices": choices})
            self._update_setup_label()

    def _rename_setup(self):
        install = self.game.install
        data = self.library.setups(install)
        old = data["active"]
        if old == "Default":
            return
        name = self._request_name("Rename Music Setup", old)
        if name:
            choices = {name if k == old else k: v for k, v in data["choices"].items()}
            self.library.save(install, {"active": name, "choices": choices})
            self._update_setup_label()

    def _delete_setup(self):
        current = self.library.setups(self.game.install)["active"]
        if (
            current != "Default"
            and QMessageBox.question(
                self,
                "Delete Music Setup",
                f"Delete “{current}” and switch to Default?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self._switch_setup("Default", delete=current)

    def _switch_setup(self, name, *, delete=None):
        install = self.game.install
        data = self.library.setups(install)
        if data["active"] == name or self._writing:
            return
        albums = self._all_albums()
        choices = {**data["choices"], data["active"]: self._current_refs()}
        if delete:
            choices.pop(delete)
        self._write(
            partial(self.service.apply_setup, choices[name], albums, install),
            install,
            "Applying music setup…",
            f"Applied {name}.",
            setup={"active": name, "choices": choices},
        )

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
            pack_id=album.id if album.pack and not track.role else None,
            edits={role: [album.id, track.id]},
        )

    def _restore(self, role=None):
        if self.game and self.game.install:
            install = self.game.install
            message = f"Original music restored for {ROLES[role]}." if role else "Original music restored."
            self._write(
                partial(self.service.restore, install, role=role),
                install,
                "Restoring music…",
                message,
                edits=dict.fromkeys((role,) if role else ROLES),
            )

    def _enable(self):
        if self.game and self.game.install:
            install = self.game.install
            self._write(partial(self.service.enable_music, install), install, "Turning music on…", "In-game music is on.")

    def _write(self, work, install, pending, success, *, pack_id=None, confirmed=False, setup=None, edits=None):
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
            saved = setup or self.library.setups(install)
            intended = {**saved["choices"][saved["active"]], **(edits or {})}
            refs = self.library.snapshot(state, self._all_albums(), {k: v for k, v in intended.items() if v})
            choices = {**saved["choices"], saved["active"]: refs}
            self.library.save(install, {**saved, "choices": choices})
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
                    self._write(work, install, pending, success, pack_id=pack_id, confirmed=True, setup=setup, edits=edits)
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
        self._shot = name
        if self.isVisible():
            self._apply_shot()

    def _apply_shot(self):
        if not self._shot or not self.isVisible() or not self._loaded or not self.game:
            return
        name, self._shot = self._shot, None
        if name in ("music-off", "off"):
            self.game = replace(self.game, music_off=True)
            self._update_roles()
        elif name == "favorites":
            for album in self._all_albums():
                for track in album.tracks[:1]:
                    if self.library.favorite_ref(album, track) not in self.library.favorites():
                        self.library.toggle(album, track)
            self.tabs[FAVORITES].click()
        elif name in ("playing", "preview", "assigned"):
            import miniaudio

            self.player.stop()
            self.player = PreviewPlayer(backends=[miniaudio.Backend.NULL])
            album = next((a for a in self.albums if a.track_count), None)
            if album:
                self._select(album, autoplay=name != "assigned")
                if name == "assigned" and self.track:
                    self._assign("deck")
