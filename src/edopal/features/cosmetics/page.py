"""Card backs and shared duel icons. All pack access and game writes run as jobs."""

from functools import partial
from weakref import ref

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QMessageBox, QPushButton, QStackedWidget
from shiboken6 import isValid

from edopal.core.install import RunState
from edopal.core.storage import GameRunningError
from edopal.features.themes.media import artwork
from edopal.features.themes.service import ThemeService, _asset, _check, _manifest
from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label, rule

from .editor import IconEditor
from .icons import apply_icons, installed_choices, load_icons
from .museum import Museum


class CosmeticsPage(ShellPage):
    install_changed = Signal()

    def __init__(self, core):
        super().__init__("Card Backs & Icons")
        self.setObjectName("canvas")
        self.core = core
        self.theme_service = ThemeService(core)
        self.generation = 0
        self.busy = False
        self.receipts = {}
        self.dest = None
        self.details = {}
        self.loaded_backs = False
        self.loading_backs = False
        self.loading_icons = False
        self.tab = "cardbacks"
        self.undo = QPushButton("Undo")
        self.undo.setProperty("role", "quiet")
        self.undo.setIcon(t.icon("undo"))
        self.undo.clicked.connect(self._undo)
        self.title_row.addWidget(self.undo)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.tab_buttons = {}
        self.group = QButtonGroup(self)
        for key, title in (("cardbacks", "Card Backs"), ("icons", "Icons")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty("role", "scope")
            button.setFixedHeight(32)
            button.setStyleSheet("padding: 0 14px; text-align: center;")
            button.clicked.connect(partial(self.open_tab, key))
            self.group.addButton(button)
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        self.tab_buttons["cardbacks"].setChecked(True)
        tabs.addStretch()
        self.status = label("", "muted")
        tabs.addWidget(self.status)
        self.retry = QPushButton("Retry")
        self.retry.clicked.connect(lambda: self.open_tab(self.tab))
        self.retry.hide()
        tabs.addWidget(self.retry)
        self.body.addLayout(tabs)
        self.body.addWidget(rule())
        self.stack = QStackedWidget()
        self.museum = Museum()
        self.editor = IconEditor()
        self.stack.addWidget(self.museum)
        self.stack.addWidget(self.editor)
        self.body.addWidget(self.stack, 1)
        self.museum.focused.connect(self._load_detail)
        self.museum.equip.connect(self._equip)
        self.editor.apply_requested.connect(self._apply_icons)
        self.install_changed.connect(self._new_install)
        page = ref(self)

        def changed(_):
            widget = page()
            if widget is not None and isValid(widget):
                widget.install_changed.emit()

        core.installs.subscribe(changed)
        self.open_tab("cardbacks")

    def _notify_error(self, exc):
        self.status.setText(str(exc))
        self.core.notify(str(exc), "error")

    def open_tab(self, key):
        self.tab = key
        self.tab_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.museum if key == "cardbacks" else self.editor)
        self.status.setText("" if key == "cardbacks" else "Applies to both players")
        self.retry.hide()
        if key == "cardbacks" and not self.loaded_backs and not self.loading_backs:
            self._load_backs()
        elif key == "icons" and not self.editor.library and not self.loading_icons:
            self._load_icons()
        self._controls()

    def _load_backs(self):
        self.loading_backs = True
        packs = self.core.packs

        def done(rows):
            self.loading_backs = False
            if not rows:
                self._load_error("cardbacks", "No card backs available in the catalog.")
                return
            self.loaded_backs = True
            self.museum.populate(rows, {})
            for pack in rows:
                self.core.jobs.run(
                    lambda ctx, p=pack: artwork(packs, p, root=None),
                    key=f"cosmetics-thumb-{pack.id}",
                    on_done=partial(self.museum.set_thumbnail, pack.id),
                    on_error=lambda exc: None,
                )

        def error(exc):
            self.loading_backs = False
            self._load_error("cardbacks", exc)

        self.core.jobs.run(lambda ctx: packs.catalog("cardback"), key="cosmetics-museum", on_done=done, on_error=error)

    def _load_detail(self, key):
        if key in self.details:
            self.museum.detail(key, *self.details[key])
            return
        packs, pack = self.core.packs, self.museum.packs[key]

        def load(ctx):
            directory = packs.ensure(pack, cancel=lambda: ctx.cancelled)
            manifest = _manifest(directory, key, "cardback")
            return manifest, _asset(directory, manifest, manifest["image"])

        def done(result):
            self.details[key] = result
            self.museum.detail(key, *result)

        def error(exc):
            if self.museum.selected == key:
                self.core.notify(str(exc), "error")

        self.core.jobs.run(load, key="cosmetics-focused-back", on_done=done, on_error=error)

    def _load_icons(self):
        self.loading_icons = True
        self.status.setText("Loading icon textures…")
        self.editor.setEnabled(False)
        packs = self.core.packs

        def done(library):
            self.loading_icons = False
            self.editor.set_library(library)
            if self.tab == "icons":
                self.status.setText("Applies to both players")
            self._refresh(reset=True)

        def error(exc):
            self.loading_icons = False
            self._load_error("icons", exc)

        self.core.jobs.run(partial(load_icons, packs), key="cosmetics-icons", on_done=done, on_error=error)

    def _load_error(self, tab, exc):
        if self.tab == tab:
            self.status.setText(str(exc))
            self.retry.show()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()

    def _new_install(self):
        self.generation += 1
        self.busy = False
        self.receipts = {}
        self.dest = None
        self.editor.can_apply = False
        self.museum.state = None
        self._controls()
        self._refresh(reset=True)

    def _refresh(self, *, reset=False):
        generation = self.generation
        core, service = self.core, self.theme_service
        install, library = core.installs.current(), self.editor.library
        features = tuple(self.tab_buttons)

        def load(ctx):
            state = service.state()
            receipts = {tab: core.storage.last_receipt(install, tab) for tab in features} if install else {}
            icons = installed_choices(install, library) if library else None
            return state, receipts, icons

        def done(data):
            if generation != self.generation or core.installs.current() != install:
                return
            self.museum.state, self.receipts, icons = data
            if icons is not None:
                self.dest, choices, custom = icons
                self.editor.set_installed(choices, bool(install), custom, reset=reset)
            self._controls()

        core.jobs.run(load, key="cosmetics-state", on_done=done, on_error=self._notify_error)

    def _controls(self):
        self.undo.setEnabled(bool(self.receipts.get(self.tab)) and not self.busy)
        self.undo.setToolTip(f"Undo the last {'card back' if self.tab == 'cardbacks' else 'icon'} change")
        self.museum.can_apply = bool(self.core.installs.current() and self.museum.state and not self.busy)
        self.museum.refresh_slots()
        self.editor.setEnabled(bool(self.editor.library and not self.busy))
        for button in self.tab_buttons.values():
            button.setEnabled(not self.busy)

    def _equip(self, slot, key):
        pack = self.museum.packs[key]
        self._change(
            partial(self.theme_service.apply_cardback, slot, pack, feature="cardbacks"), f"{pack.name} equipped for {slot}."
        )

    def _apply_icons(self):
        work = partial(
            apply_icons, self.core, self.core.installs.current(), self.editor.library, self.editor.choices.copy(), self.dest
        )
        self._change(work, "Duel icons applied.")

    def _undo(self):
        storage, install, feature = self.core.storage, self.core.installs.current(), self.tab

        def undo(ctx, confirmed_closed):
            _check(ctx)
            return storage.undo_last(install, feature, confirmed_closed=confirmed_closed)

        self._change(undo, "Last change undone.")

    def _change(self, action, message, confirmed=False):
        core, expected, generation = self.core, self.core.installs.current(), self.generation
        if not expected or self.busy:
            return
        self.busy = True
        self.status.setText("Updating game textures…")
        self._controls()

        def run(ctx):
            if core.installs.current() != expected:
                raise ValueError("The game folder changed. Review your selection again.")
            try:
                return action(ctx=ctx, confirmed_closed=confirmed)
            except GameRunningError:
                if confirmed or core.installs.run_state() != RunState.UNKNOWN:
                    raise
                return RunState.UNKNOWN

        def done(result):
            if generation != self.generation:
                return
            self.busy = False
            self._controls()
            if result == RunState.UNKNOWN:
                answer = QMessageBox.question(
                    self, "Is EDOPro Closed?", "EDOPal could not check the game. Is EDOPro closed?"
                )
                if answer == QMessageBox.StandardButton.Yes and generation == self.generation:
                    self._change(action, message, True)
                else:
                    self.status.setText("Change cancelled.")
                return
            conflicts = getattr(result, "conflicts", ())
            notice = f"Undo kept {len(conflicts)} externally changed file(s)." if conflicts else message
            self.status.setText(notice)
            core.notify(notice, "warning" if conflicts else "success")
            self._refresh(reset=True)

        def error(exc):
            if generation == self.generation:
                self.busy = False
                self._controls()
                self._notify_error(exc)

        core.jobs.run(run, key="cosmetics-change", on_done=done, on_error=error)

    def preview_state(self, name):
        if name == "icons":
            self.open_tab("icons")
            if self.editor.library:
                self.editor.select_preset("minimal")
                for key, choice in (
                    ("act", "C"),
                    ("number", "D"),
                    ("negated", "A"),
                    ("target", "B"),
                    ("equip", "C"),
                    ("lim", "A"),
                    ("lpf", "C"),
                    ("attack", "B"),
                ):
                    self.editor.pick(key, choice)
        elif name == "duel-links":
            self.museum.select("cardback-duel-links-dark-magician")
