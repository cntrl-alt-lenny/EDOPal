"""EDOPro effect previews and the game sound-pack catalogue."""

from functools import partial
from html import escape
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from edopal.core.feature import FeatureSpec
from edopal.features.music.player import PreviewError, PreviewPlayer
from edopal.features.music.service import MusicService
from edopal.features.music.widgets import icon as audio_icon
from edopal.features.sounds.releases import GAMES, REGIONS, release_date
from edopal.features.sounds.service import GROUPS, cached_sound_packs, fetch_pack, stock_previews
from edopal.features.sounds.widgets import GameCover, GamePicker, SoundTile
from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label, rule
from edopal.ui.widgets import PrimaryButton, QuietButton

_AMBIENT = (
    ("soft-felt", "Soft Felt"),
    ("rain-room", "Rain Room"),
    ("paper-and-sleeve", "Paper & Sleeve"),
)
_SOURCE_NAMES = {
    "gamefaqs.gamespot.com": "GameFAQs",
    "en.wikipedia.org": "Wikipedia",
    "konami.com": "Konami",
    "yugipedia.com": "Yugipedia",
}
_PANEL_STYLE = f"""
    QFrame#soundTile, QPushButton#soundTile {{
        background: {t.PANEL}; border: 1px solid {t.EDGE}; border-radius: 5px;
    }}
    QFrame#soundTile[stock="true"] {{ background: #353028; border-color: #907953; }}
    QPushButton#soundTile:hover, QPushButton#soundTile:focus {{ border-color: #907953; }}
    QLabel[role="soundTileTitle"] {{ color: {t.TEXT}; font: 500 15px Outfit; }}
    QLabel[role="soundGroup"] {{
        background: {t.RAISED}; color: {t.TEXT}; border-radius: 3px; font-weight: 600; padding-left: 12px;
    }}
    QWidget#soundEffectRow {{ border-bottom: 1px solid {t.RULE}; }}
    QWidget#soundEffectRow[playing="true"] {{ background: #322f29; border-left: 2px solid {t.GOLD}; }}
    QPushButton#soundPlay {{
        padding: 0; border-radius: 10px; background: {t.RAISED}; border: 1px solid {t.EDGE};
    }}
    QPushButton#soundPlay:disabled {{ background: transparent; }}
    QPushButton#soundPlay:hover, QPushButton#soundPlay:focus {{ border-color: {t.GOLD}; }}
    QLabel[role="soundAssignment"] {{
        background: {t.PANEL}; color: {t.TEXT}; border: 1px solid {t.EDGE};
        border-radius: 4px; padding: 3px 12px; font-size: 12px;
    }}
    QLabel[role="soundDate"] {{ font-size: 12px; }}
    QFrame#selectedSoundPack {{ border: 1px solid {t.EDGE}; border-radius: 5px; }}
"""


def _scroll(content):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def _section(text, size=20):
    title = label(text, "heading")
    title.setStyleSheet(f"font-size: {size}px;")
    return title


def _release_sources(game):
    by_source = {}
    for region, (_day, override) in game.dates.items():
        by_source.setdefault(override or game.source, []).append(region)
    if not by_source:
        by_source[game.source] = []
    lines = []
    for source, regions in by_source.items():
        host = urlsplit(source).netloc.removeprefix("www.")
        name = escape(_SOURCE_NAMES.get(host, host))
        link = f'<a href="{escape(source)}" style="color:{t.GOLD};">{name}</a>'
        lines.append(" · ".join((link, " / ".join(regions))) if regions else link)
    return "<br>".join(lines)


class SoundsPage(ShellPage):
    def __init__(self, core):
        super().__init__("Sounds")
        self.core = core
        self.setStyleSheet(_PANEL_STYLE)
        self.game = next(game for game in GAMES if game.slug == "gx-spirit-caller")
        self.banners = {}
        self.packs = {}
        self.downloaded = set()
        self.transfers = {}
        self.errors = {}
        self.checking_catalog = True
        self.catalog_unavailable = False
        self.checking = set()
        self.checked_cache = set()
        self.picker = None
        self.scrim = None
        self.selected_slug = self.game.slug
        self.files = {}
        self.effects = []
        self.player = PreviewPlayer()
        self.playing = None
        self.preview_job = None
        self.preview_timer = QTimer(self, interval=90)
        self.preview_timer.timeout.connect(self._preview_state)

        sides = QHBoxLayout()
        sides.setSpacing(12)
        self.body.addLayout(sides, 1)
        rail = QWidget()
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 4, 0)
        rail_layout.setSpacing(6)
        self.rail = _scroll(rail)
        sides.addWidget(self.rail)
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {t.RULE};")
        sides.addWidget(divider)
        sides.addWidget(self._build_effects(), 1)

        rail_layout.addWidget(_section("EDOPro Default"))
        self.default = SoundTile("EDOPro Default", "", logo=True)
        rail_layout.addWidget(self.default.widget)
        rail_layout.addSpacing(14)
        rail_layout.addWidget(_section("Ambient & ASMR"))
        self.ambient = {}
        for slug, title in _AMBIENT:
            tile = SoundTile(title, "Not Yet Available", button=True)
            tile.widget.clicked.connect(partial(self._select_ambient, slug))
            rail_layout.addWidget(tile.widget)
            self.ambient[slug] = tile
        rail_layout.addSpacing(14)
        header = QHBoxLayout()
        header.addWidget(_section("Game Sound Packs"), 1)
        header.addWidget(label(f"{len(GAMES)} Games", "muted"))
        rail_layout.addLayout(header)
        self.game_tile = SoundTile("Choose Game", None, button=True)
        self.game_tile.widget.clicked.connect(self._open_picker)
        rail_layout.addWidget(self.game_tile.widget)
        rail_layout.addWidget(self._build_details())
        rail_layout.addStretch()

        self._update_tiles()
        self._show_selection()
        self.refresh_stock()
        self.core.jobs.run(
            lambda _ctx: self.core.packs.catalog("sound"),
            key="sounds.catalog",
            on_done=self._catalog_ready,
            on_error=self._catalog_failed,
        )
        self.core.jobs.run(
            MusicService(self.core).banners,
            key="sounds.banners",
            on_done=self._banners_ready,
            on_error=lambda _exc: None,
        )

    def _build_effects(self):
        widget = QWidget()
        col = QVBoxLayout(widget)
        col.setContentsMargins(2, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(_section("Duel Effects", 22))
        col.addSpacing(9)
        head = self._effect_columns(widget=None)
        for title, stretch in (("Sound", 30), ("EDOPro Slot", 25), ("Assigned Pack", 35)):
            head.addWidget(label(title, "muted"), stretch)
        caption = label("Preview", "muted")
        caption.setFixedWidth(53)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(caption)
        col.addLayout(head)
        col.addSpacing(5)
        for title, effects in GROUPS:
            group = label(title, "soundGroup")
            group.setFixedHeight(26)
            col.addWidget(group)
            for name, slot in effects:
                row = QWidget()
                row.setObjectName("soundEffectRow")
                row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
                row.setMinimumHeight(30)
                row.setMaximumHeight(46)
                line = self._effect_columns(row)
                line.addWidget(label(name), 30)
                line.addWidget(label(slot, "muted"), 25)
                assigned_cell = QWidget()
                assignment = QHBoxLayout(assigned_cell)
                assignment.setContentsMargins(0, 0, 0, 0)
                assigned = label("", "muted")
                assigned.setAlignment(Qt.AlignmentFlag.AlignCenter)
                assigned.setMaximumWidth(207)
                assigned.setFixedHeight(28)
                assignment.addWidget(assigned, 1)
                assignment.addStretch()
                line.addWidget(assigned_cell, 35)
                button = QuietButton("")
                button.setObjectName("soundPlay")
                button.setFixedSize(22, 22)
                button.setIconSize(QSize(12, 12))
                button.setAccessibleName(f"Preview {name}")
                button.clicked.connect(partial(self._preview, name, slot))
                cell = QWidget()
                cell.setFixedWidth(53)
                center = QHBoxLayout(cell)
                center.setContentsMargins(0, 0, 0, 0)
                center.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
                line.addWidget(cell)
                col.addWidget(row, 1)
                self.effects.append((name, slot, row, assigned, button))
        col.addStretch()
        return _scroll(widget)

    @staticmethod
    def _effect_columns(widget):
        row = QHBoxLayout(widget) if widget else QHBoxLayout()
        row.setContentsMargins(12, 0, 5, 0)
        row.setSpacing(6)
        return row

    def _build_details(self):
        card = QFrame()
        card.setObjectName("selectedSoundPack")
        col = QVBoxLayout(card)
        col.setContentsMargins(10, 8, 10, 9)
        col.setSpacing(8)
        self.details_title = _section("", 16)
        self.details_title.setWordWrap(True)
        self.details_art = GameCover()
        self.details_art.setAccessibleName("Selected Game Artwork")
        self.details_art.setFixedHeight(54)
        col.addWidget(self.details_art)
        col.addWidget(self.details_title)
        self.details_metadata = label("", "muted", wrap=True)
        col.addWidget(self.details_metadata)
        self.dates = QWidget()
        dates = QGridLayout(self.dates)
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setHorizontalSpacing(6)
        dates.setVerticalSpacing(3)
        self.date_labels = {}
        for column, (region, name) in enumerate(REGIONS.items()):
            dates.setColumnStretch(column, 1)
            header = label(region, "muted")
            header.setToolTip(name)
            dates.addWidget(header, 0, column)
            self.date_labels[region] = day = label("", "soundDate")
            dates.addWidget(day, 1, column)
        col.addWidget(self.dates)
        self.sources = QWidget()
        sources = QVBoxLayout(self.sources)
        sources.setContentsMargins(0, 0, 0, 0)
        sources.setSpacing(5)
        sources.addWidget(rule())
        sources.addWidget(label("Release Sources", "muted"))
        self.source_links = label("", "muted", wrap=True)
        self.source_links.setTextFormat(Qt.TextFormat.RichText)
        self.source_links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.source_links.setOpenExternalLinks(True)
        sources.addWidget(self.source_links)
        col.addWidget(self.sources)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.state = label("", "muted", wrap=True)
        line.addWidget(self.state, 1)
        self.download = PrimaryButton("Download")
        self.download.setFixedHeight(30)
        self.download.clicked.connect(self._download)
        line.addWidget(self.download)
        col.addLayout(line)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Sound Pack Download Progress")
        col.addWidget(self.progress)
        return card

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.window().width() > t.WIDE_WIDTH
        self.rail.setFixedWidth(358 if wide else 262)
        self.default.set_height(63 if wide else 54)
        for tile in self.ambient.values():
            tile.set_height(49 if wide else 40)
        self.game_tile.set_height(39 if wide else 32)
        self.details_art.setFixedHeight(116 if wide else 54)

    def refresh_stock(self):
        self._stop_preview()
        install = self.core.installs.current()
        root = install.root if install else None
        self.files = {}
        self._update_effects()
        self.core.jobs.run(
            lambda _ctx: stock_previews(root),
            key="sounds.stock",
            on_done=self._stock_ready,
            on_error=lambda exc: self.core.notify(f"Couldn’t read EDOPro sounds. {exc}", "error"),
        )

    def _stock_ready(self, files):
        self.files = files
        self._update_effects()

    def _update_effects(self):
        self.default.subtitle.setText(f"{len(self.files)} Assigned")
        for name, slot, row, assigned, button in self.effects:
            active = name == self.playing
            row.setProperty("playing", active)
            row.style().unpolish(row)
            row.style().polish(row)
            button.setEnabled(slot in self.files)
            button.setIcon(audio_icon("pause", t.GOLD) if active else t.icon("play", t.TEXT))
            description = f"Stop {name} Preview" if active else f"Preview {name}"
            button.setAccessibleName(description)
            button.setToolTip(description if slot in self.files else "No Audio File")
            value = "Follows Activate" if name == "Chain" else "EDOPro Default" if slot in self.files else "—"
            assigned.setText(value)
            role = "soundAssignment" if value == "EDOPro Default" else "muted"
            if assigned.property("role") != role:
                assigned.setProperty("role", role)
                assigned.style().unpolish(assigned)
                assigned.style().polish(assigned)

    def _preview(self, name, slot, _checked=False):
        current = self.playing
        self._stop_preview()
        if current == name or slot not in self.files:
            return
        self.playing = name
        self._update_effects()
        prepare = partial(self.player.prepare, self.files[slot])
        self.preview_job = self.core.jobs.run(
            lambda _ctx: prepare(),
            key="sounds.preview",
            on_done=lambda result: self._preview_ready(name, result),
            on_error=lambda exc: self._preview_failed(name, exc),
        )

    def _preview_ready(self, name, prepared):
        if self.playing != name:
            prepared.close()
            return
        try:
            self.player.start(prepared)
        except PreviewError as exc:
            self._preview_failed(name, exc)
        else:
            self.preview_timer.start()

    def _preview_failed(self, name, exc):
        if self.playing == name:
            self._stop_preview()
            self.core.notify(str(exc), "error")

    def _stop_preview(self):
        if self.preview_job and self.preview_job.running:
            self.preview_job.cancel()
        self.preview_timer.stop()
        self.player.stop()
        self.playing = None
        self._update_effects()

    def _preview_state(self):
        if not self.player.playing:
            error = self.player.error
            self._stop_preview()
            if error:
                self.core.notify(error, "error")

    def hideEvent(self, event):
        if self.picker:
            self.picker.reject()
        self._stop_preview()
        super().hideEvent(event)

    def _select_ambient(self, slug, _checked=False):
        self.selected_slug = slug
        self._show_selection()
        self._check_cache(slug)

    def _open_picker(self):
        if self.picker:
            return
        window = self.window()
        self.scrim = QFrame(window)
        self.scrim.setStyleSheet("background: rgba(17, 17, 19, 120);")
        self.scrim.setGeometry(window.rect())
        self.scrim.show()
        self.scrim.raise_()
        self.picker = GamePicker(self, self.game, self.banners, self._status)
        self.picker.finished.connect(self._picker_closed)
        self.picker.open()
        center = self.mapToGlobal(self.rect().center() + QPoint(0, 32))
        self.picker.move(center - QPoint(self.picker.width() // 2, self.picker.height() // 2))
        self._check_cache(*(game.slug for game in GAMES))

    def _picker_closed(self, result):
        picker, self.picker = self.picker, None
        self.scrim.hide()
        self.scrim.deleteLater()
        self.scrim = None
        if result and picker.game:
            self.game = picker.game
            self.selected_slug = self.game.slug
            self._update_tiles()
            self._show_selection()
            self._check_cache(self.selected_slug)
        picker.deleteLater()

    def _banners_ready(self, paths):
        self.banners = paths
        self._update_tiles()
        self._show_selection()
        if self.picker:
            self.picker.set_banners(paths)

    def _catalog_ready(self, packs):
        self.checking_catalog = False
        self.packs = {pack.id: pack for pack in packs}
        self._update_tiles()
        self._check_cache(self.selected_slug)

    def _catalog_failed(self, exc):
        self.catalog_unavailable = True
        self.core.notify(f"Couldn’t load sound packs. {exc}", "error")
        self._catalog_ready(())

    def _check_cache(self, *slugs):
        pending = tuple(slug for slug in slugs if slug not in self.checking and slug not in self.transfers)
        if not pending:
            return
        self.checking.update(pending)
        self._update_tiles()
        self._show_selection()
        self.core.jobs.run(
            partial(cached_sound_packs, self.core.packs, pending),
            on_done=lambda downloaded: self._cache_result(pending, downloaded),
            on_error=lambda _exc: self._cache_result(pending, set()),
        )

    def _cache_result(self, checked, downloaded):
        self.checking.difference_update(checked)
        self.checked_cache.update(checked)
        self.downloaded.difference_update(checked)
        self.downloaded.update(downloaded)
        self._update_tiles()
        self._show_selection()

    def _status(self, slug):
        if transfer := self.transfers.get(slug):
            done, total = transfer
            return "Verifying…" if total and done >= total else "Downloading…"
        if slug in self.downloaded:
            return "Downloaded"
        if slug in self.errors:
            return self.errors[slug]
        if self.checking_catalog or (slug in self.checking and slug not in self.checked_cache):
            return "Checking…"
        if self.catalog_unavailable:
            return "Catalog Unavailable"
        return "Not Downloaded" if "sound-" + slug in self.packs else "Not Yet Available"

    def _update_tiles(self):
        for slug, tile in self.ambient.items():
            tile.subtitle.setText(self._status(slug))
        if self.picker:
            self.picker.update_statuses(self._status)

    def _show_selection(self):
        slug = self.selected_slug
        game_selected = slug == self.game.slug
        title = self.game.name if game_selected else self.ambient[slug].title.text()
        self.details_title.setText(title)
        self.details_metadata.setVisible(game_selected)
        self.dates.setVisible(game_selected)
        self.sources.setVisible(game_selected)
        art = self.banners.get("music-" + slug) if game_selected else None
        self.details_art.set_cover(slug, art)
        self.details_art.setVisible(not self.details_art.cover.isNull())
        if game_selected:
            self.details_metadata.setText(f"{self.game.platform} · {self.game.series}")
            self.source_links.setText(_release_sources(self.game))
            for region, cell in self.date_labels.items():
                day, source = self.game.dates.get(region, ("", ""))
                cell.setText(release_date(day))
                cell.setToolTip((source or self.game.source) if day else "")
        status = self._status(slug)
        self.state.setText(status)
        self.download.setEnabled(
            "sound-" + slug in self.packs
            and slug not in self.downloaded
            and slug not in self.transfers
            and slug not in self.checking
        )
        self.download.setToolTip(status if not self.download.isEnabled() else "Download Sound Pack")
        transfer = self.transfers.get(slug)
        self.progress.setVisible(transfer is not None)
        if transfer is not None:
            done, total = transfer
            self.progress.setRange(0, 100 if total else 0)
            if total:
                self.progress.setValue(min(100, round(done * 100 / total)))

    def _download(self):
        slug = self.selected_slug
        pack = self.packs.get("sound-" + slug)
        if not pack or not self.download.isEnabled():
            return
        self.errors.pop(slug, None)
        self._progress(slug, 0, 0)
        self.core.jobs.run(
            partial(fetch_pack, self.core.packs, pack),
            key="sounds.download." + slug,
            on_done=lambda path: self._download_done(slug, path),
            on_error=lambda exc: self._download_failed(slug, exc),
            on_progress=lambda done, total, _text: self._progress(slug, done, total),
        )

    def _progress(self, slug, done, total):
        self.transfers[slug] = (done, total)
        self._update_tiles()
        self._show_selection()

    def _download_done(self, slug, path):
        self.transfers.pop(slug, None)
        self._cache_result((slug,), {slug} if path else set())

    def _download_failed(self, slug, exc):
        self.transfers.pop(slug, None)
        self.errors[slug] = "Download Failed"
        self.core.notify(f"Couldn’t download the sound pack. {exc}", "error")
        self._update_tiles()
        self._show_selection()

    def preview_state(self, state):
        if state == "picker":
            QTimer.singleShot(0, self._open_picker)


def sounds_spec():
    def refresh(core):
        # The shell delivers install changes to feature hooks after updating the current install.
        page = widget_by_core.get(id(core))
        if page:
            page.refresh_stock()

    widget_by_core = WeakValueDictionary()

    def make_page(core):
        page = SoundsPage(core)
        widget_by_core[id(core)] = page
        return page

    return FeatureSpec("sounds", "Sounds", "sounds", 35, make_page, "EDOPro sound effects", on_install_changed=refresh)
