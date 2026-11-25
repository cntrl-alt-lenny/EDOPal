"""The Themes feature page. All downloads and changesets run on the job runner."""

from __future__ import annotations

import weakref
from concurrent.futures import CancelledError
from dataclasses import replace
from functools import partial

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from edopal.core.install import RunState
from edopal.core.storage import GameRunningError, UndoResult
from edopal.ui import theme as t
from edopal.ui.widgets import PageHeader, page_layout

from .controls import CardSlot, Hero, icon, label
from .drawers import CardPicker, CustomizeDrawer
from .gallery import PreviewCanvas, ThemeTile, stock_preview
from .media import artwork, card_label, download_size
from .service import ThemeService, ThemeState

DEFAULT = "default"
ORIGINAL = "EDOPro default"
SCENE_NAMES = {"menu": "Menu", "deck": "Deck editor", "duel": "Duel"}


def _catalog(packs, refresh, ctx):
    rows = [p for p in packs.catalog(refresh=refresh) if p.kind in {"theme", "cardback"}]
    roots = {}
    for pack in rows:
        if ctx.cancelled:
            raise CancelledError()
        if directory := packs.cached(pack.id):
            roots[pack.id] = directory
    return rows, roots


def _change(installs, expected, service, method, args, confirmed, ctx):
    if installs.current() != expected:
        raise ValueError("The game folder changed. Try again in the selected folder.")
    result = method(*args, ctx=ctx, confirmed_closed=confirmed)
    if isinstance(result, UndoResult):
        return service.state(), result
    return result, None


class ThemesPage(QWidget):
    install_changed = Signal()

    def __init__(self, core):
        super().__init__()
        self.setObjectName("canvas")
        self.core = core
        self.service = ThemeService(core)
        self.state = ThemeState()
        self.packs = {}
        self.roots = {}
        self.thumbnails = {}
        self.previews = {}
        self.tiles = {}
        self.slots = {}
        self.modes = {}
        self.selected = DEFAULT
        self.hovered = ""
        self.scene = "menu"
        self.drawer = None
        self._manual_selection = False
        self._busy = False
        self._ready = False
        self._message = ""
        self._generation = 0
        self._media_request = None
        self._pending_images = set()
        self._change_job = None
        self._build()
        self._build_tiles()
        self.install_changed.connect(self._read_state)
        page_ref = weakref.ref(self)

        def install_changed(_install):
            page = page_ref()
            if page is not None and isValid(page):
                page.install_changed.emit()

        core.installs.subscribe(install_changed)
        self._read_state()
        self._load_catalog()

    def _build(self):
        self.column = page_layout(self)
        header = PageHeader("Game appearance", "Themes")
        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("undo-theme")
        self.undo_button.setProperty("role", "quiet")
        self.undo_button.setIcon(icon("undo"))
        self.undo_button.setToolTip("Undo the last theme or card-back change")
        self.undo_button.clicked.connect(self._undo)
        self.customize_button = QPushButton("Customize")
        self.customize_button.setObjectName("customize-theme")
        self.customize_button.setIcon(icon("customize"))
        self.customize_button.clicked.connect(self._open_customize)
        for button in (self.undo_button, self.customize_button):
            button.setFixedHeight(t.BUTTON_H)
            button.setIconSize(QSize(t.ICON, t.ICON))
            header.actions.addWidget(button)
        self.column.addWidget(header)
        self._build_hero()

        library_head = QHBoxLayout()
        library_head.addWidget(label("Theme library", "subheading"))
        library_head.addStretch()
        self.hint = label("Hover to preview  ·  Click to select", "muted")
        library_head.addWidget(self.hint)
        self.reload = QPushButton("Reload")
        self.reload.setProperty("role", "quiet")
        self.reload.setStyleSheet("padding: 1px 6px;")
        self.reload.clicked.connect(lambda: self._load_catalog(refresh=True))
        self.reload.hide()
        library_head.addWidget(self.reload)
        self.column.addLayout(library_head)
        self.shelf = QScrollArea()
        self.shelf.setWidgetResizable(True)
        self.shelf.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shelf.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.shelf.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.shelf_body = QWidget()
        self.shelf_row = QHBoxLayout(self.shelf_body)
        self.shelf_row.setContentsMargins(0, 0, 0, 0)
        self.shelf_row.setSpacing(t.MD)
        self.shelf.setWidget(self.shelf_body)
        self.column.addWidget(self.shelf)

        self.column.addWidget(label("Card backs", "subheading"))
        self.backs_row = QHBoxLayout()
        self.backs_row.setSpacing(12)
        for slot in ("you", "opponent"):
            button = CardSlot(slot)
            button.clicked.connect(partial(self._open_picker, slot))
            self.backs_row.addWidget(button, 1)
            self.slots[slot] = button
        self.column.addLayout(self.backs_row)

    def _build_hero(self):
        self.hero = Hero()
        self.hero_layout = QHBoxLayout(self.hero)
        self.hero_layout.setSpacing(14)
        info = QWidget()
        info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.details = QVBoxLayout(info)
        self.details.setContentsMargins(0, 0, 0, 0)
        self.details.setSpacing(t.XS)
        self.details.addStretch(2)
        self.kicker = label("THEME PREVIEW", "goldKicker")
        self.details.addWidget(self.kicker)
        self.title = label("", "heroTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.details.addWidget(self.title)
        self.subtitle = label("", "body")
        self.subtitle.setWordWrap(True)
        self.details.addWidget(self.subtitle)

        segment = QFrame()
        segment.setObjectName("themeScenes")
        segment.setStyleSheet(
            f"QFrame#themeScenes {{background: {t.RAIL}; border: 1px solid {t.EDGE}; border-radius: 7px;}}"
            f"QPushButton {{background: transparent; color: {t.MUTED}; padding: 5px 10px; border: 1px solid transparent;"
            "border-radius: 4px; font-size: 12px;}"
            f"QPushButton:checked {{background: {t.RAISED}; color: {t.INK};}}"
            f"QPushButton:hover {{color: {t.INK};}} QPushButton[keyboardFocus=true]:focus {{border-color: {t.PALE};}}"
        )
        segments = QHBoxLayout(segment)
        segments.setContentsMargins(3, 3, 3, 3)
        segments.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        for key, name in SCENE_NAMES.items():
            button = QPushButton(name)
            button.setObjectName(f"preview-{key}")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(partial(self._scene, key))
            self.mode_group.addButton(button)
            segments.addWidget(button)
            self.modes[key] = button
        self.modes["menu"].setChecked(True)
        self.details.addWidget(segment, alignment=Qt.AlignmentFlag.AlignLeft)
        self.details.addSpacing(t.XS)

        self.apply_button = QPushButton("Apply theme")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.setObjectName("apply-theme")
        self.apply_button.setMinimumSize(140, t.BUTTON_H)
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.clicked.connect(self._apply)
        self.details.addWidget(self.apply_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.status = label("", "small")
        self.status.setWordWrap(True)
        self.details.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setMaximumWidth(254)
        self.details.addWidget(self.progress)
        self.progress.hide()
        self.details.addStretch(2)
        self.hero_layout.addWidget(info, 4)
        self.canvas = PreviewCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.hero_layout.addWidget(self.canvas, 6)
        self.column.addWidget(self.hero, 1)

    def _read_state(self):
        self._generation += 1
        generation = self._generation
        if self._change_job and self._change_job.running:
            self._change_job.cancel()
        self._close_drawer()
        self._busy = self._ready = self._manual_selection = False
        self._message = ""
        self.progress.hide()
        self.state = ThemeState()
        self._render()
        service = self.service

        def done(state):
            if generation != self._generation:
                return
            self.state = state
            self._ready = True
            self._select_initial()
            self._render()

        def error(exc):
            if generation == self._generation:
                self._show_error(exc)

        self.core.jobs.run(lambda ctx: service.state(), key="themes-state", on_done=done, on_error=error)

    def _load_catalog(self, refresh=False):
        self.hint.setText("Loading themes…")
        self.reload.hide()

        def done(data):
            rows, self.roots = data
            self.packs = {p.id: p for p in rows}
            self.previews.clear()
            self.thumbnails.clear()
            self._media_request = None
            self._build_tiles()
            self._select_initial()
            available = len(self.tiles) > 1
            self.hint.setText("Hover to preview  ·  Click to select" if available else "No theme packs available")
            self.reload.setVisible(not available)
            self._render()

        def error(exc):
            self.hint.setText("Theme catalog unavailable")
            self.reload.setToolTip(str(exc))
            self.reload.show()
            self._render()

        self.core.jobs.run(partial(_catalog, self.core.packs, refresh), key="themes-catalog", on_done=done, on_error=error)

    def _select_initial(self):
        if self._manual_selection and self.selected in self.tiles:
            return
        first = next((key for key in self.tiles if key != DEFAULT), DEFAULT)
        self.selected = self.state.active_theme if self.state.active_theme in self.tiles else first
        self.hovered = ""

    def _build_tiles(self):
        while self.shelf_row.count():
            item = self.shelf_row.takeAt(0)
            if old := item.widget():
                old.hide()
                old.deleteLater()
        self.tiles.clear()
        entries = [(DEFAULT, ORIGINAL, "Your original game skin")]
        entries.extend((p.id, p.name, p.meta.get("tagline", "")) for p in self.packs.values() if p.kind == "theme")
        for key, name, tagline in entries:
            tile = ThemeTile(key, name, tagline)
            tile.setObjectName(f"tile-{key}")
            tile.setMinimumWidth(132)
            tile.hovered.connect(self._hover)
            tile.clicked.connect(partial(self._select, key))
            self.shelf_row.addWidget(tile, 1)
            self.tiles[key] = tile
        self.tiles[DEFAULT].set_thumbnail(stock_preview("tile"))
        if len(entries) < 3:
            self.shelf_row.addStretch(3 - len(entries))

    def _select(self, key, *_):
        if self._busy:
            return
        self._manual_selection = True
        self.selected = key
        self.hovered = ""
        self._message = ""
        self._render()

    def _hover(self, key):
        if self._busy or self.drawer or (key and key not in self.tiles):
            return
        self.hovered = key
        self._render_preview()

    def _scene(self, scene, *_):
        self.scene = scene
        self.modes[scene].setChecked(True)
        self._render_preview()

    def _render_preview(self):
        key = self.hovered or self.selected
        for tile_id, tile in self.tiles.items():
            tile.set_previewed(tile_id == self.hovered)
        pack = self.packs.get(key)
        name = pack.name if pack else ORIGINAL
        self.title.setText(name)
        self.subtitle.setText(
            pack.meta.get("tagline", "") if pack else "Stock EDOPro preview. Applying restores your original skin and fonts."
        )
        active = (self.state.active_theme or DEFAULT) == key
        if self.hovered and self.hovered != self.selected:
            heading = "HOVER PREVIEW"
        elif active and self._ready and self.core.installs.current():
            heading = "ACTIVE THEME"
        else:
            heading = "THEME PREVIEW"
        self.kicker.setText(heading)
        self.hero.set_art(self.thumbnails.get(key) if pack else stock_preview("tile"))
        request = (key, self.scene)
        if self._media_request == request:
            return
        self._media_request = request
        description = f"{name} · {SCENE_NAMES[self.scene]}"
        if not pack:
            self.canvas.set_preview(stock_preview(self.scene), label=description)
            return
        if request in self.previews:
            self._set_preview_image(request, self.previews[request], description)
            return
        scene = self.scene
        packs, root = self.core.packs, self.roots.get(key)
        # Keep the previous image for the crossfade while the new scene loads.
        if self.canvas.accessibleDescription() == "":
            self.canvas.set_preview(None, label="Loading preview…")
        self.canvas.set_loading(True)

        def received(image):
            if image:
                self.previews[request] = image
            if self._media_request == request:
                self._set_preview_image(request, image, description)

        self.core.jobs.run(
            lambda ctx: artwork(packs, pack, scene, root),
            key="themes-preview",
            on_done=received,
            on_error=lambda exc: received(None),
        )

    def _set_preview_image(self, request, image, description):
        if image:
            self.canvas.set_preview(image, label=description)
        else:
            self.canvas.set_preview(None, label=f"{SCENE_NAMES[request[1]]} preview unavailable")

    def _render(self):
        active = self.state.active_theme or DEFAULT
        install = self.core.installs.current()
        for key, tile in self.tiles.items():
            tile.setChecked(key == self.selected)
            tile.setEnabled(not self._busy)
            if self._ready and install and key == active:
                badge = "Active"
            elif key == DEFAULT:
                badge = "Built-in"
            elif key in self.roots:
                badge = "Downloaded"
            else:
                badge = download_size(self.packs[key].size)
            tile.set_badge(badge)
        self.apply_button.setEnabled(bool(install) and self._ready and not self._busy and self.selected != active)
        self.apply_button.setText("Applied" if self.selected == active and install and self._ready else "Apply theme")
        self.apply_button.setIcon(icon("check", t.MUTED) if self.apply_button.text() == "Applied" else QIcon())
        self.apply_button.setVisible(not self._busy)
        self.undo_button.setEnabled(self._ready and self.state.can_undo and not self._busy)
        self.customize_button.setEnabled(bool(self.state.active_theme) and self._ready and not self._busy)
        self.customize_button.setToolTip(
            "Edit the active theme’s accent and font" if self.state.active_theme else "Apply a theme to customize it"
        )
        message = self._message or ("Choose a game folder to apply changes." if not install else "")
        self.status.setText(message)
        self.status.setVisible(bool(message))
        for slot, button in self.slots.items():
            key = self.state.cardbacks[slot]
            pack = self.packs.get(key)
            fallback = {"cardback-vortex-classic": "Classic Vortex", "cardback-ocg-1998": "1998 OCG"}
            name = pack.name if pack else fallback.get(key, key.removeprefix("cardback-").replace("-", " ").title())
            caption = card_label(pack) if pack else ""
            if slot in self.state.unknown_back_slots:
                name, caption = "Game card back", ""
            path = self.state.back_images.get(slot) or self.thumbnails.get(key)
            button.set_back(name, caption, path)
            button.setEnabled(not self._busy)
        self._render_preview()
        self._request_thumbnails([*self.tiles, *self.state.cardbacks.values()])

    def _request_thumbnails(self, keys):
        for key in dict.fromkeys(keys):
            if key not in self.packs or key in self.thumbnails or key in self._pending_images:
                continue
            self._pending_images.add(key)
            pack, packs, root = self.packs[key], self.core.packs, self.roots.get(key)

            def received(image, key=key):
                self._pending_images.discard(key)
                self.thumbnails[key] = image
                if key in self.tiles:
                    self.tiles[key].set_thumbnail(image)
                for slot, active in self.state.cardbacks.items():
                    if active == key and slot not in self.state.back_images:
                        self.slots[slot].portrait.set_image(image)
                if isinstance(self.drawer, CardPicker) and key in self.drawer.images:
                    self.drawer.images[key].set_image(image)
                if (self.hovered or self.selected) == key:
                    self.hero.set_art(image)

            self.core.jobs.run(
                lambda ctx, p=pack, client=packs, r=root: artwork(client, p, root=r),
                key=f"themes-thumb-{key}",
                on_done=received,
                on_error=lambda exc, callback=received: callback(None),
            )

    def _apply(self):
        pack = self.packs.get(self.selected)
        message = f"{pack.name} applied. Ready on next launch." if pack else "Original game skin restored."
        self._run_change(self.service.apply_theme, (pack,), message)

    def _undo(self):
        self._run_change(self.service.undo, (), "Last change undone.")

    def _pick(self, slot, key):
        self._close_drawer()
        self._run_change(self.service.apply_cardback, (slot, self.packs[key]), f"{self.packs[key].name} set for {slot}.")

    def _customize(self, color, use_font):
        self._close_drawer()
        self._run_change(self.service.customize, (color, use_font), "Theme settings saved.")

    def _run_change(self, method, args, message, *, confirmed=False):
        install = self.core.installs.current()
        if self._busy or not install:
            return
        generation = self._generation
        self.hovered = ""
        self._busy = True
        self._message = "Applying changes…"
        self.progress.setRange(0, 0)
        self.progress.show()
        self._render()

        def progress(done, total, text):
            if generation != self._generation:
                return
            percent = min(100, round(done * 100 / total)) if total else 0
            self._message = f"{text}  ·  {percent}%" if total else text
            self.status.setText(self._message)
            self.status.show()
            self.progress.setRange(0, 100 if total else 0)
            self.progress.setValue(percent)

        def done(result):
            if generation != self._generation:
                return
            self.state, undo = result
            self._busy = False
            self._message = message
            if undo and undo.conflicts:
                self._message = f"Undo kept {len(undo.conflicts)} externally changed file(s)."
            self.progress.hide()
            self._render()
            self.core.notify(self._message, "warning" if undo and undo.conflicts else "success")
            self._update_cache(method, args)

        def error(exc):
            if generation != self._generation:
                return
            self._busy = False
            self.progress.hide()
            self._render()
            if isinstance(exc, GameRunningError) and not confirmed:
                self._offer_confirmation(generation, method, args, message, exc)
            else:
                self._show_error(exc)

        self._change_job = self.core.jobs.run(
            partial(_change, self.core.installs, install, self.service, method, args, confirmed),
            key="themes-change",
            on_progress=progress,
            on_done=done,
            on_error=error,
        )

    def _offer_confirmation(self, generation, method, args, message, exc):
        installs = self.core.installs

        def done(state):
            if generation != self._generation:
                return
            if state != RunState.UNKNOWN:
                self._show_error(exc)
                return
            choice = QMessageBox.question(
                self, "Is EDOPro closed?", "EDOPal couldn’t check whether EDOPro is running. Is the game closed?"
            )
            if generation != self._generation:
                return
            if choice == QMessageBox.StandardButton.Yes:
                self._run_change(method, args, message, confirmed=True)
            else:
                self._message = "Change cancelled."
                self._render()

        self.core.jobs.run(lambda ctx: installs.run_state(), on_done=done, on_error=self._show_error)

    def _update_cache(self, method, args):
        if method != self.service.apply_theme or not args[0]:
            return
        pack, packs = args[0], self.core.packs

        def received(path):
            if path:
                self.roots[pack.id] = path
                self._render()

        self.core.jobs.run(lambda ctx: packs.cached(pack.id), on_done=received, on_error=lambda exc: None)

    def _show_error(self, exc):
        self._message = str(exc) or "The change could not be completed."
        self._render()
        self.core.notify(self._message, "error")

    def _open_picker(self, slot, *_):
        if self._busy:
            return
        self._close_drawer()
        self.hovered = ""
        self._render_preview()
        packs = [p for p in self.packs.values() if p.kind == "cardback"]
        current = None if slot in self.state.unknown_back_slots else self.state.cardbacks[slot]
        drawer = CardPicker(
            self,
            self.slots[slot],
            slot,
            packs,
            current,
            self.thumbnails,
            bool(self.core.installs.current()) and self._ready,
        )
        drawer.picked.connect(partial(self._pick, slot))
        self._show_drawer(drawer)
        self._request_thumbnails([p.id for p in packs])

    def _open_customize(self):
        self._close_drawer()
        pack = self.packs.get(self.state.active_theme)
        if self.state.active_theme:
            name = pack.name if pack else self.state.active_theme.removeprefix("theme-").replace("-", " ").title()
            accent = pack.meta.get("accent") if pack else None
            drawer = CustomizeDrawer(self, self.customize_button, self.state, name, accent)
            drawer.saved.connect(self._customize)
            self._show_drawer(drawer)

    def _show_drawer(self, drawer):
        self.drawer = drawer
        drawer.dismissed.connect(self._close_drawer)
        drawer.place()
        drawer.close_button.setFocus(Qt.FocusReason.PopupFocusReason)

    def _close_drawer(self):
        if self.drawer:
            drawer, self.drawer = self.drawer, None
            drawer.hide()
            drawer.deleteLater()
            drawer.anchor.setFocus(Qt.FocusReason.PopupFocusReason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.height()
        compact = height < 700
        roomy = height >= 950
        self.column.setSpacing(t.SM if compact else t.MD)
        self.title.setStyleSheet(f"font-size: {t.XXL if compact else t.HUGE}px;")
        self.hero_layout.setContentsMargins(t.XL if compact else t.XXL, t.SM, t.LG, t.SM)
        self.shelf.setFixedHeight(96 if compact else 142 if not roomy else 192)
        for button in self.slots.values():
            button.setFixedHeight(76 if compact else 84 if not roomy else 96)
        if self.drawer:
            self.layout().activate()
            self.drawer.place()

    def preview_state(self, name):
        """Deterministic page states used by the offscreen preview command."""
        themes = [p for p in self.packs.values() if p.kind == "theme"]
        if not themes or name == "idle":
            return
        by_id = {p.id: p for p in themes}
        primary = by_id.get("theme-abyss", themes[0])
        self.selected = primary.id
        if name == "hover-preview":
            self.hovered = by_id.get("theme-nebula", themes[-1]).id
        elif name == "downloading":
            sample = by_id.get("theme-moon", primary)
            self.selected = sample.id
            self.scene = "deck"
            self.modes["deck"].setChecked(True)
            self.modes["deck"].setFocus(Qt.FocusReason.OtherFocusReason)
            self._busy = True
            self._message = f"Downloading {sample.name}  ·  47%"
            self.progress.setRange(0, 100)
            self.progress.setValue(47)
            self.progress.show()
        elif name == "applied":
            self.state = replace(self.state, active_theme=primary.id, can_undo=True, has_font=True, theme_font=True)
            self.scene = "duel"
            self.modes["duel"].setChecked(True)
            self._message = f"{primary.name} applied. Ready on next launch."
        elif name == "cardback-picker-open":
            self._open_picker("you")
        self._render()
