"""The two-slot sleeve desk. Choices stay local until the player presses Apply."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QImage, QResizeEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from sleeves import Entry, Layout, Library
from studio import theme as t
from studio.context import AppContext, GameOpenError, Installation
from studio.icons import icon
from studio.widgets import Button, label, margins

from . import sleeves_work as work
from .skins_feedback import AppearancePage
from .skins_packs import PackBrowser
from .sleeves_collection import Choice, saved
from .sleeves_design import Designer
from .sleeves_editor import Editor
from .sleeves_gallery import Gallery
from .sleeves_images import ImageEditor
from .sleeves_manage import Manager
from .sleeves_widgets import SlotBanner


class SleevesPage(AppearancePage):
    id, title, icon = "sleeves", "Sleeves", "sleeves"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.built = False

    def _build(self, context: AppContext) -> None:
        self.built = True
        self.config = Path(context.cfg.config_path)
        self.library = Library(self.config.parent / "sleeves" if context.cfg.cli.config else None)
        self.thumbs = work.Thumbnails(self.library)
        self.snapshot: work.Snapshot | None = None
        self.install = context.installation
        self.opened = False
        self.writing = False
        self.saving = False
        self.generation = 0
        self.loading = False
        self.target = "mine"
        self.pending: dict[str, Choice] = {}
        self.images: dict[str, QImage] = {}
        self.editor: Editor | None = None
        self.message = ""
        self.retry_action: Callable[[], None] | None = None
        QApplication.instance().applicationStateChanged.connect(self.activation_changed)
        body = QVBoxLayout(self)
        margins(body, t.XL)
        body.setSpacing(t.MD)
        self.banner = SlotBanner()
        for key, button in self.banner.slots.items():
            button.clicked.connect(lambda checked=False, slot=key: self.select_slot(slot))
        body.addWidget(self.banner)
        self.stack = QStackedWidget()
        self.slide = QPropertyAnimation(self)
        self.slide.setPropertyName(b"pos")
        self.slide.setDuration(t.MOTION_MS)
        self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        body.addWidget(self.stack, 1)
        self.gallery = Gallery(context, self.thumbs)
        self.return_focus: QWidget = self.gallery.search
        self.stack.addWidget(self.gallery)
        self.packs = PackBrowser(
            context, "sleeves", lambda title, message: self.confirm(title, message)
        )
        self.stack.addWidget(self.packs)
        self.setup_block()
        self.packs.back.clicked.connect(self.close_packs)
        self.packs.view_requested.connect(self.view_pack)
        self.gallery.more.clicked.connect(self.open_packs)
        self.gallery.opened.connect(self.open_editor)
        self.gallery.picked.connect(self.stage)
        self.status = label("", "small", True)
        self.footer = QWidget()
        footer_layout = QVBoxLayout(self.footer)
        margins(footer_layout, 0)
        self.controls = QHBoxLayout()
        footer_layout.addLayout(self.controls)
        self.setup_feedback()
        margins(self.controls, 0)
        self.controls.addWidget(self.status, 1)
        self.retry = Button("Retry")
        self.retry.clicked.connect(self.run_retry)
        self.same = Button("Use for both")
        self.same.clicked.connect(self.copy_slot)
        self.reset = Button("Discard sleeve choices", "quiet")
        self.reset.clicked.connect(self.discard)
        self.undo = Button("Undo last change")
        self.undo.setToolTip("Restore the game’s sleeve files from before your last Apply.")
        self.undo.clicked.connect(self.confirm_undo)
        self.apply = Button("Apply sleeves", "primary", "check")
        self.apply.clicked.connect(self.apply_pressed)
        for btn in (self.details, self.retry):
            self.controls.addWidget(btn)
        self.game_actions = QWidget()
        actions = QHBoxLayout(self.game_actions)
        margins(actions, 0)
        actions.addStretch()
        for btn in (self.reset, self.same, self.undo, self.apply):
            actions.addWidget(btn)
        footer_layout.addWidget(self.game_actions)
        body.addWidget(self.footer)
        self.fit_banner(self.width())
        self.update_controls()

    def shown(self) -> None:
        if not self.built:
            self._build(self.ctx)
        if self.install != self.ctx.installation:
            self.installation_changed(self.ctx.installation)
        self.opened = True
        self.return_focus = self.gallery.search
        self.message = ""
        if self.stack.currentWidget() is self.packs:
            self.packs.setFocus()
        else:
            self.refresh()
            self.gallery.search.setFocus()

    def activation_changed(self, state: Qt.ApplicationState) -> None:
        if (
            state == Qt.ApplicationState.ApplicationActive
            and self.opened
            and self.stack.currentWidget() is self.gallery
            and not (self.loading or self.writing or self.saving or self.retry_action)
        ):
            self.message = ""
            self.refresh()

    def hidden(self) -> bool:
        if not self.built:
            return True
        if self.writing or self.saving:
            return False
        if not self.guard_edits(include_slots=True):
            return False
        self.leave_block()
        self.opened = False
        self.close_editor()
        self.slide.stop()
        self.gallery.grid.suspend()
        self.gallery.stop_measurement()
        self.generation += 1
        return True

    def request_close(self) -> bool:
        return self.hidden()

    def installation_changed(self, installation: Installation) -> None:
        if not self.built:
            return
        self.leave_block()
        self.generation += 1
        if (
            self.opened
            and installation.path != self.install.path
            and not self.guard_edits(include_slots=True)
        ):
            # Detection may finish while a draft is open. Keep the draft bound to its original game.
            self.message = "Your sleeve choices belong to the previous EDOPro folder. Return to that folder or discard them."
            self.update_controls()
            return
        self.close_editor()
        if installation.path != self.install.path:
            self.pending.clear()
            self.images.clear()
            self.target = "mine"
        self.install = installation
        self.snapshot = None
        self.message = ""
        self.retry_action = None
        if self.stack.currentWidget() is self.packs:
            return
        if self.opened:
            self.refresh()
        else:
            self.update_controls()

    def open_packs(self) -> None:
        if self.writing:
            return
        self.stack.setCurrentWidget(self.packs)
        self.banner.hide()
        self.footer.hide()
        self.packs.load(online=True)
        self.packs.back.setFocus()

    def view_pack(self, pack_id: str) -> None:
        self.gallery.clear_filters()
        self.gallery.source.setCurrentText("Pack sleeve")
        self.gallery.search.setText(pack_id)
        self.close_packs()

    def close_packs(self) -> None:
        if self.stack.currentWidget() is self.packs and not self.packs.job:
            self.stack.setCurrentWidget(self.gallery)
            self.return_focus = self.gallery.more
            self.banner.show()
            self.footer.show()
            if self.opened:
                self.refresh()

    def refresh(self) -> None:
        self.generation += 1
        generation = self.generation
        self.loading = True
        self.technical_error(None)
        self.retry_action = None
        game = Path(self.install.path) if self.install.path else None
        lib, config = self.library, self.config
        self.update_controls()

        def ready(snapshot: work.Snapshot) -> None:
            if generation != self.generation:
                return
            self.loading = False
            self.snapshot = snapshot
            self.gallery.set_items(snapshot.items)
            known = {item.key: item for item in snapshot.items}
            for slot, item in list(self.pending.items()):
                if item.entry and item.key not in known:
                    self.pending.pop(slot)
                elif item.key in known:
                    self.pending[slot] = known[item.key]
                if slot in self.pending:
                    self.load_pick(slot)
            self.update_controls()
            if self.opened and self.stack.currentWidget() is self.gallery:
                self.return_focus.setFocus()
                self.return_focus = self.gallery.search

        def failed(exc: Exception) -> None:
            if generation == self.generation:
                self.loading = False
                self.snapshot = None
                self.message = "Could not read your sleeves. Check that the library folder is accessible, then choose Retry."
                self.technical_error(exc)
                self.retry_action = self.refresh
                self.update_controls()

        job = self.ctx.tasks.start(lambda _: work.inspect(lib, game, config))
        job.result.connect(ready)
        job.error.connect(failed)

    def update_controls(self) -> None:
        available = bool(self.snapshot and not (self.writing or self.saving or self.loading))
        slots = self.snapshot.slots if self.snapshot else {}
        changed = any(slot.status.matches is False for slot in slots.values())
        for key, btn in self.banner.slots.items():
            btn.setChecked(key == self.target)
            btn.setEnabled(not self.saving)
            btn.show_choice(
                slots.get(key),
                self.pending.get(key),
                self.images.get(key, QImage()),
                bool(self.install.path),
            )
        current = slots.get(self.target)
        self.same.setEnabled(
            available
            and bool(
                self.pending.get(self.target)
                or (current and (current.choice or current.status.path))
            )
        )
        self.reset.setVisible(bool(self.pending))
        self.reset.setEnabled(available)
        self.stack.setEnabled(available or self.writing)
        self.gallery.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.writing)
        self.gallery.actions["card"].setEnabled(self.install.ready)
        self.gallery.actions["card"].setToolTip(
            "Search this installation’s cards."
            if self.install.ready
            else "Choose your EDOPro folder and let its card updates finish."
        )
        self.undo.setEnabled(available and self.install.ready and self.snapshot.can_undo)
        assigned = any(slot.status.matches for slot in slots.values())
        self.apply.setText(
            self.write_label
            if self.writing
            else "Apply staged pair"
            if self.pending
            else "Apply saved sleeves again"
            if changed
            else "Applied"
            if assigned
            else "Apply sleeves"
        )
        self.apply.setIcon(icon("check" if self.apply.text() == "Applied" else "arrow", t.ON_GOLD))
        self.apply.setEnabled(
            available
            and self.install == self.ctx.installation
            and self.install.ready
            and bool(self.pending or changed)
        )
        caption = self.message
        if self.writing:
            caption = "Updating sleeves… You can change folders when this finishes."
        elif self.saving:
            caption = "Updating your collection…"
        elif self.loading:
            caption = "Reading your sleeve collection…"
        elif not caption and not self.install.path:
            caption = "Choose your EDOPro folder to apply sleeves."
        elif not caption and not self.install.ready:
            caption = "Finish setting up EDOPro. Open EDOPro and let its updates finish, then choose Check again."
        elif not caption and self.editor:
            caption = ""
        elif not caption and self.pending:
            pair = []
            for key, title in (("mine", "You"), ("opponent", "Opponent")):
                item = self.pending.get(key) or (slots[key].choice if key in slots else None)
                pair.append(f"{title} · {item.full_name if item else 'Current game sleeve'}")
            caption = "     ".join(pair)
        elif not caption and changed:
            caption = "Game files changed. Reapply your saved sleeves to restore them."
        elif not caption:
            caption = (
                f"Current skin · {self.snapshot.active_name}"
                if self.snapshot and self.snapshot.active_name
                else "Changes take effect next time you start EDOPro."
            )
        self.status.setText(caption)
        if self.editor:
            self.footer.setVisible(bool(self.message and not self.saving))
        self.retry.setVisible(
            not self.loading and (bool(self.retry_action) or not self.install.ready)
        )
        self.retry.setEnabled(not (self.saving or self.writing))
        self.retry.setText(
            "Retry"
            if self.retry_action
            else "Check again"
            if self.install.path
            else "Choose folder"
        )

    def run_retry(self) -> None:
        if self.retry_action:
            self.retry_action()
        elif self.install.path:
            path, generation = self.install.path, self.generation
            job = self.ctx.tasks.start(lambda _: Installation.inspect(path))
            job.result.connect(
                lambda inst: (
                    self.ctx.set_installation(inst) if self.generation == generation else None
                )
            )
            job.error.connect(lambda exc: self.ctx.notify(str(exc)))
        else:
            self.ctx.installations_requested.emit()

    def select_slot(self, slot: str) -> None:
        if self.writing:
            return
        self.target = slot
        self.gallery.grid.clearSelection()
        self.update_controls()

    def stage(self, item: Choice) -> None:
        if self.writing:
            return
        self.pending[self.target] = item
        self.message = ""
        self.retry_action = None
        self.load_pick(self.target)
        self.update_controls()

    def load_pick(self, slot: str) -> None:
        item = self.pending[slot]
        cached = self.gallery.grid.tiles.cache.get(item.key)
        if cached and not cached.isNull():
            self.images[slot] = cached.toImage()
            return
        self.images.pop(slot, None)
        generation, thumbs = self.generation, self.thumbs

        def ready(img: QImage) -> None:
            if generation == self.generation and self.pending.get(slot) == item:
                self.images[slot] = img
                self.update_controls()

        job = self.ctx.tasks.start(lambda worker: thumbs(item.key, worker))
        job.result.connect(ready)
        job.error.connect(
            lambda _: (
                self.ctx.notify("Could not read the sleeve preview.")
                if self.opened and generation == self.generation
                else None
            )
        )

    def discard(self) -> None:
        self.pending.clear()
        self.images.clear()
        self.message = ""
        self.retry_action = None
        self.gallery.grid.clearSelection()
        if self.install != self.ctx.installation and not self.editor:
            self.installation_changed(self.ctx.installation)
        else:
            self.update_controls()

    def copy_slot(self) -> None:
        source = self.target
        other = "opponent" if source == "mine" else "mine"
        current = self.snapshot.slots.get(source)
        choice = self.pending.get(source) or (current.choice if current else None)
        if choice:
            if current and current.status.matches is False and source not in self.pending:
                self.pending[source] = choice
                self.load_pick(source)
            self.pending[other] = choice
            self.load_pick(other)
            self.message = ""
            self.retry_action = None
            self.update_controls()
        elif current and current.status.path:
            lib, path = self.library, current.status.path

            def both(entry: Entry) -> None:
                self.pending = {"mine": saved(entry), "opponent": saved(entry)}
                self.refresh()

            self.mutate(lambda: lib.add_image("Current game sleeve", path, Layout("fit")), both)

    def open_editor(self, mode: str) -> None:
        if self.writing or self.editor or not self.snapshot:
            return
        self.message = ""
        self.update_controls()
        current = self.pending.get(self.target)
        if not current and self.target in self.snapshot.slots:
            current = self.snapshot.slots[self.target].choice
        if mode == "design":
            self.editor = Designer(self.ctx.tasks, self.library, current)
        elif mode == "manage":
            self.editor = Manager(
                self.ctx.tasks,
                self.library,
                self.snapshot.items,
                self.snapshot.removed,
                current,
                lambda title, message: self.confirm(title, message),
            )
        else:
            game = Path(self.install.path) if mode == "card" and self.install.ready else None
            self.editor = ImageEditor(self.ctx.tasks, self.library, game)
        self.editor_opener = self.gallery.actions[mode]
        self.banner.hide()
        self.footer.hide()
        self.game_actions.hide()
        self.editor.back.connect(self.back_from_editor)
        self.editor.commit.connect(self.save_entry)
        self.stack.addWidget(self.editor)
        self.stack.setCurrentWidget(self.editor)
        self.animate(t.XL)
        self.update_controls()
        self.editor.back_button.setFocus()

    def guard_edits(self, *, include_slots: bool) -> bool:
        if not (self.editor and self.editor.dirty or include_slots and self.pending):
            return True
        if not self.confirm(
            "Discard unsaved changes?",
            "Leave without saving your sleeve edits or applying your choices to EDOPro?",
        ):
            return False
        if include_slots:
            self.discard()
        return True

    def back_from_editor(self) -> None:
        if self.guard_edits(include_slots=False):
            self.close_editor()

    def close_editor(self) -> None:
        if self.editor and not (self.writing or self.saving):
            self.editor.leave()
            self.stack.setCurrentWidget(self.gallery)
            self.stack.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None
            self.banner.show()
            self.footer.show()
            self.game_actions.show()
            if self.opened:
                self.animate(-t.XL)
            self.return_focus = self.editor_opener
            self.editor_opener.setFocus()

    def animate(self, offset: int) -> None:
        self.slide.stop()
        self.slide.setTargetObject(self.stack.currentWidget())
        self.slide.setStartValue(QPoint(offset, 0))
        self.slide.setEndValue(QPoint(0, 0))
        self.slide.start()

    def escape(self) -> bool:
        if self.leave_block() or self.saving or self.writing or self.packs.job:
            return True
        if self.stack.currentWidget() is self.packs:
            self.close_packs()
            return True
        if self.editor:
            self.back_from_editor()
            return True
        if self.pending:
            self.guard_edits(include_slots=True)
            return True
        return False

    def mutate(self, operation: Callable, success: Callable) -> None:
        self.saving = True
        self.ctx.set_busy(True)
        self.update_controls()
        job = self.ctx.tasks.start(lambda _: operation())

        def release() -> None:
            self.saving = False
            self.ctx.set_busy(False)

        def ready(value: object) -> None:
            release()
            success(value)

        def failed(exc: Exception) -> None:
            release()
            self.message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Could not save the sleeve. Check folder permissions and free space, then try again."
            )
            self.technical_error(exc)
            self.update_controls()

        job.result.connect(ready)
        job.error.connect(failed)

    def save_entry(self, operation: Callable) -> None:
        target = self.target

        def complete(entry: Entry | None) -> None:
            self.close_editor()
            if entry:
                self.pending[target] = saved(entry)
                self.message = "Saved to your collection. Apply to use it in game."
            else:
                self.message = "Collection updated. Restore removed sleeves in Manage → Trash."
            self.refresh()

        self.mutate(operation, complete)

    def apply_pressed(self) -> None:
        self.change("apply" if self.pending else "reapply")

    def confirm_undo(self) -> None:
        if self.confirm(
            "Undo last sleeve change?",
            "Put back the game’s sleeve files from before your last Apply?",
        ):
            self.change("undo")

    def change(self, action: str) -> None:
        if (
            self.writing
            or self.saving
            or self.install != self.ctx.installation
            or not self.install.ready
        ):
            return
        library, game, config = self.library, Path(self.install.path), self.config
        choices = self.pending.copy()
        self.technical_error(None)
        self.write_label = "Restoring sleeves…" if action == "undo" else "Applying sleeves…"
        self.writing = True
        self.retry_action = None
        self.ctx.set_busy(True)
        self.update_controls()

        def release() -> None:
            self.writing = False
            self.ctx.set_busy(False)

        def ready(_: object) -> None:
            release()
            self.pending.clear()
            self.images.clear()
            self.message = (
                "Previous sleeves restored." if action == "undo" else "Sleeves applied."
            ) + " Visible next time you start EDOPro."
            self.refresh()

        def failed(exc: Exception) -> None:
            release()
            if isinstance(exc, GameOpenError):
                self.message = ""
                self.update_controls()
                self.show_block(exc, lambda: self.change(action), self.banner, self.footer)
                return
            self.message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Could not update the sleeves. Check that the game folder is writable, then choose Retry."
            )
            self.technical_error(exc)
            self.retry_action = lambda: self.change(action)
            self.update_controls()

        job = self.ctx.tasks.start(lambda _: work.write(library, game, config, action, choices))
        job.result.connect(ready)
        job.error.connect(failed)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.built:
            self.fit_banner(event.size().width())

    def fit_banner(self, width: int) -> None:
        narrow = width < t.COMPACT_WIDTH - t.RAIL_COMPACT
        margins(self.layout(), t.MD if narrow else t.XL)
        self.banner.setFixedHeight(t.TILE_H + t.XXL if narrow else t.TILE_LARGE)
        inset = t.SM if narrow else t.LG
        self.banner.layout().setContentsMargins(t.XL, inset, t.LG, inset)
        self.banner.title.setStyleSheet(f"font-size:{t.HEADING if narrow else t.DISPLAY}px;")
        for slot in self.banner.slots.values():
            slot.heading.setStyleSheet(f"font-size:{t.BODY if narrow else t.SUBTITLE}px;")
            slot.art.setFixedSize(
                t.BUTTON_H if narrow else t.HUGE + t.SM, t.HUGE + t.SM if narrow else t.TILE_H
            )
