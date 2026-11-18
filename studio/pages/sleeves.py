"""The two-slot sleeve desk. Choices stay local until the player presses Apply."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QImage, QResizeEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QStackedWidget, QVBoxLayout

from sleeves import Entry, Layout, Library
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.widgets import Button, label, margins

from . import sleeves_work as work
from .sleeves_collection import Choice, saved
from .sleeves_design import Designer
from .sleeves_editor import Editor
from .sleeves_gallery import Gallery
from .sleeves_images import ImageEditor
from .sleeves_manage import Manager
from .sleeves_widgets import SlotBanner


class SleevesPage(Page):
    id, title, icon = "sleeves", "Sleeves", "sleeves"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
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
        self.measuring = False
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
        self.gallery = Gallery(context.tasks, self.thumbs)
        self.stack.addWidget(self.gallery)
        self.gallery.opened.connect(self.open_editor)
        self.gallery.picked.connect(self.stage)
        self.gallery.colours_needed.connect(self.measure)
        self.status = label("", "small", True)
        self.controls = QHBoxLayout()
        self.controls.addWidget(self.status, 1)
        self.retry = Button("Retry")
        self.retry.clicked.connect(self.run_retry)
        self.same = Button("Same for both")
        self.same.clicked.connect(self.copy_slot)
        self.reset = Button("Discard picks", "quiet")
        self.reset.clicked.connect(self.discard)
        self.undo = Button("Undo")
        self.undo.setToolTip("Restore the game’s sleeve files from before your last Apply.")
        self.undo.clicked.connect(self.confirm_undo)
        self.apply = Button("Apply", "primary", "check")
        self.apply.clicked.connect(self.apply_pressed)
        for btn in (self.retry, self.reset, self.same, self.undo, self.apply):
            self.controls.addWidget(btn)
        body.addLayout(self.controls)
        self.update_controls()

    def shown(self) -> None:
        self.opened = True
        self.message = ""
        self.refresh()
        self.gallery.search.setFocus()

    def activation_changed(self, state: Qt.ApplicationState) -> None:
        if (
            state == Qt.ApplicationState.ApplicationActive
            and self.opened
            and not (self.loading or self.writing or self.saving or self.retry_action)
        ):
            self.message = ""
            self.refresh()

    def hidden(self) -> bool:
        if self.writing or self.saving:
            return False
        self.opened = False
        self.close_editor()
        self.generation += 1
        return True

    def request_close(self) -> bool:
        return self.hidden()

    def installation_changed(self, installation: Installation) -> None:
        self.generation += 1
        self.close_editor()
        if installation.path != self.install.path:
            self.pending.clear()
            self.images.clear()
            self.target = "mine"
        self.install = installation
        self.snapshot = None
        self.message = ""
        self.retry_action = None
        if self.opened:
            self.refresh()
        else:
            self.update_controls()

    def refresh(self) -> None:
        self.generation += 1
        generation = self.generation
        self.loading = True
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
            if self.opened and not self.editor:
                self.gallery.search.setFocus()

        def failed(exc: Exception) -> None:
            if generation == self.generation:
                self.loading = False
                self.snapshot = None
                self.message = f"Could not read your sleeves. {exc}"
                self.retry_action = self.refresh
                self.update_controls()

        job = self.ctx.tasks.start(lambda _: work.inspect(lib, game, config))
        job.result.connect(ready)
        job.error.connect(failed)

    def measure(self) -> None:
        if not self.snapshot or self.measuring:
            return
        missing = [
            item
            for item in self.snapshot.items
            if not item.family and item.key not in self.gallery.measured
        ]
        if not missing:
            return
        self.measuring = True
        thumbs = self.thumbs
        job = self.ctx.tasks.start(lambda worker: thumbs.colours(missing, worker))

        def ready(colours: dict[Path, str]) -> None:
            self.gallery.measured.update(colours)
            self.gallery.filter()

        def done() -> None:
            self.measuring = False

        job.result.connect(ready)
        job.error.connect(lambda _: self.ctx.notify("Could not read a saved sleeve’s colours."))
        job.finished.connect(done)

    def update_controls(self) -> None:
        available = bool(self.snapshot and not (self.writing or self.saving or self.loading))
        slots = self.snapshot.slots if self.snapshot else {}
        changed = any(slot.status.matches is False for slot in slots.values())
        for key, btn in self.banner.slots.items():
            btn.setChecked(key == self.target)
            btn.setEnabled(not (self.saving or self.writing))
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
        self.stack.setEnabled(available)
        self.gallery.actions["card"].setEnabled(self.install.ready)
        self.gallery.actions["card"].setToolTip(
            "Search this installation’s cards."
            if self.install.ready
            else "Choose an installation with its card list downloaded."
        )
        self.undo.setEnabled(available and self.install.ready and self.snapshot.can_undo)
        self.apply.setText("Reapply" if changed and not self.pending else "Apply")
        self.apply.setEnabled(available and self.install.ready and bool(self.pending or changed))
        caption = self.message
        if self.writing:
            caption = "Updating the game’s sleeves. Folder switching is paused."
        elif self.saving:
            caption = "Updating your collection…"
        elif self.loading:
            caption = "Reading your sleeve collection…"
        elif not caption and not self.install.path:
            caption = "Choose a game folder to apply sleeves."
        elif not caption and not self.install.ready:
            caption = "Open EDOPro to download its card list, then check again."
        elif not caption and self.pending:
            caption = "Apply to use your picks next time you start EDOPro."
        elif not caption and changed:
            caption = "Game files changed. Reapply your saved sleeves to restore them."
        elif not caption:
            caption = "Changes take effect next time you start EDOPro."
        self.status.setText(caption)
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
        self.target = slot
        self.gallery.grid.clearSelection()
        self.update_controls()

    def stage(self, item: Choice) -> None:
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
        if self.editor or not self.snapshot:
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
                self.ctx.confirm,
            )
        else:
            game = Path(self.install.path) if mode == "card" and self.install.ready else None
            self.editor = ImageEditor(self.ctx.tasks, self.library, game)
        self.editor.back.connect(self.close_editor)
        self.editor.commit.connect(self.save_entry)
        self.stack.addWidget(self.editor)
        self.stack.setCurrentWidget(self.editor)
        self.animate(t.XL)
        self.editor.back_button.setFocus()

    def close_editor(self) -> None:
        if self.editor and not (self.writing or self.saving):
            self.editor.leave()
            self.stack.setCurrentWidget(self.gallery)
            self.stack.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None
            if self.opened:
                self.animate(-t.XL)
            self.gallery.search.setFocus()

    def animate(self, offset: int) -> None:
        self.slide.stop()
        self.slide.setTargetObject(self.stack.currentWidget())
        self.slide.setStartValue(QPoint(offset, 0))
        self.slide.setEndValue(QPoint(0, 0))
        self.slide.start()

    def escape(self) -> bool:
        if self.saving or self.writing:
            return True
        if self.editor:
            self.close_editor()
            return True
        if self.pending:
            self.discard()
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
            self.message = f"Could not save the change. {exc}"
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
                self.message = "Collection updated. Restore removed sleeves in Manage → Bin."
            self.refresh()

        self.mutate(operation, complete)

    def apply_pressed(self) -> None:
        self.change("apply" if self.pending else "reapply")

    def confirm_undo(self) -> None:
        if self.ctx.confirm(
            "Restore previous sleeves?",
            "Put back the game’s sleeve files from before your last Apply?",
        ):
            self.change("undo")

    def change(self, action: str) -> None:
        if self.writing or self.saving or not self.install.ready:
            return
        library, game, config = self.library, Path(self.install.path), self.config
        choices = self.pending.copy()
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
            self.message = str(exc)
            self.retry_action = lambda: self.change(action)
            self.update_controls()

        job = self.ctx.tasks.start(lambda _: work.write(library, game, config, action, choices))
        job.result.connect(ready)
        job.error.connect(failed)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        narrow = event.size().width() < t.COMPACT_WIDTH - t.RAIL_COMPACT
        margins(self.layout(), t.MD if narrow else t.XL)
        self.banner.setFixedHeight(t.TILE_H + t.XXL if narrow else t.TILE_LARGE)
        self.banner.title.setStyleSheet(f"font-size:{t.HEADING if narrow else t.DISPLAY}px;")
        for slot in self.banner.slots.values():
            slot.heading.setStyleSheet(f"font-size:{t.BODY if narrow else t.SUBTITLE}px;")
            slot.art.setFixedSize(
                t.BUTTON_H if narrow else t.HUGE + t.SM, t.HUGE + t.SM if narrow else t.TILE_H
            )
