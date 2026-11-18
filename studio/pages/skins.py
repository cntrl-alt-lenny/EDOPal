"""Game appearances, the local theme library and the live theme maker."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

import themes
from content.download import DownloadError, DownloadFailure, Progress
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.tasks import Job, Work
from studio.widgets import Button, label, margins

from .skins_detail import Workspace
from .skins_gallery import Gallery
from .skins_library import LibraryActions
from .skins_model import Look, download_message
from .skins_service import Collection, SkinStore


class SkinsPage(Page):
    id, title, icon = "skins", "Skins", "skins"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.store = SkinStore(context.cfg, context.version)
        self.data: Collection | None = None
        self.look: Look | None = None
        self.origin: Look | None = None
        self.base: themes.Theme | None = None
        self.mode = "gallery"
        self.naming = ""
        self.entered = self.tried_recovery = self.online = False
        self.generation = 0
        self.job: Job | None = None
        self.retry: Callable | None = None
        body = QVBoxLayout(self)
        margins(body, t.XL)
        body.setSpacing(t.MD)
        self.message_bar = QWidget()
        row = QHBoxLayout(self.message_bar)
        margins(row, 0)
        self.message = label("", "notice", True)
        self.retry_button = Button("Retry")
        self.retry_button.clicked.connect(lambda: self.retry and self.retry())
        row.addWidget(self.message, 1)
        row.addWidget(self.retry_button)
        body.addWidget(self.message_bar)
        self.message_bar.hide()
        self.install = QWidget()
        row = QHBoxLayout(self.install)
        margins(row, 0)
        self.install_hint = label("", "body", True)
        self.choose = Button("Choose folder", "secondary", "folder")
        self.choose.clicked.connect(context.installations_requested.emit)
        row.addWidget(self.install_hint, 1)
        row.addWidget(self.choose)
        body.addWidget(self.install)
        self.stack = QStackedWidget()
        self.motion = QPropertyAnimation(self)
        self.motion.setPropertyName(b"pos")
        self.motion.setDuration(t.MOTION_MS)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        body.addWidget(self.stack, 1)
        self.gallery = Gallery(context, self.store)
        self.view = Workspace(context, self.store)
        self.maker = self.view.maker
        self.stack.addWidget(self.gallery)
        self.stack.addWidget(self.view)
        self.gallery.selected.connect(self.open_look)
        self.gallery.create.clicked.connect(self.create)
        self.gallery.refresh.clicked.connect(lambda: self.reload(online=True))
        self.gallery.filters.changed.connect(self._group)
        self.gallery.restore.clicked.connect(partial(self.change_game, "restore"))
        self.view.back.clicked.connect(self.escape)
        info = self.view.inspector
        info.edit.clicked.connect(self.customise)
        info.apply.clicked.connect(self.primary)
        info.highlight.toggled.connect(self._preview)
        for button, action in (
            (info.back, "back"),
            (info.undo, "undo"),
            (info.remove, "remove"),
            (info.restore, "restore"),
        ):
            button.clicked.connect(partial(self.change_game, action))
        self.files = LibraryActions(self)
        self.maker.changed.connect(self._edited)
        self.view.progress.cancel.clicked.connect(self._cancel)

    @property
    def game(self) -> Path | None:
        path = self.ctx.installation.path
        return Path(path) if path else None

    @property
    def dirty(self) -> bool:
        return self.mode == "maker" and (
            self.maker.theme != self.base or self.maker.name.text() != self.base.name
        )

    def note(self, text: str = "", action: Callable | None = None, button: str = "Retry") -> None:
        self.retry = action
        self.message.setText(text)
        self.message.setToolTip("")
        self.message_bar.setVisible(bool(text))
        self.retry_button.setText(button)
        self.retry_button.setVisible(action is not None)

    def installation_changed(self, installation: Installation) -> None:
        self.generation += 1
        self.data = None
        self.gallery.looks = []
        self.gallery.filter()
        self.gallery.restore.hide()
        self.tried_recovery = False
        self.install.setVisible(not installation.ready)
        self.install_hint.setText(
            "Choose a game folder to apply a look. You can still design and save themes."
            if not installation.path
            else "Open EDOPro once to finish setting up this folder, then select it again."
        )
        self.choose.setText("Change folder" if installation.path else "Choose folder")
        self.to_gallery()
        if self.entered:
            self.reload()

    def shown(self) -> None:
        self.entered = True
        self.tried_recovery = False
        self.reload()
        self.gallery.search.setFocus()

    def hidden(self) -> bool:
        if self.job or not self._discard():
            return False
        self.entered = False
        self.generation += 1
        self.to_gallery()
        return True

    def _discard(self) -> bool:
        return not (self.dirty or self.files.name_edited) or self.ctx.confirm(
            "Discard unsaved changes?", "Leave without saving your theme edits?"
        )

    def escape(self) -> bool:
        if self.job:
            return True
        if self.naming:
            self.files.cancel_name()
        elif self.mode == "maker":
            if self._discard():
                if self.origin:
                    self.open_look(self.origin)
                else:
                    self.to_gallery()
        elif self.mode == "detail":
            self.to_gallery()
        else:
            return False
        return True

    def to_gallery(self) -> None:
        self.look = self.origin = self.base = None
        self.mode, self.naming = "gallery", ""
        self.view.preview.clear()
        self._show(self.gallery)
        self.note()
        self.gallery.grid.setFocus()

    def _show(self, widget: QWidget) -> None:
        if self.stack.currentWidget() is widget:
            return
        self.motion.stop()
        self.stack.setCurrentWidget(widget)
        if self.entered:
            self.motion.setTargetObject(widget)
            self.motion.setStartValue(QPoint(-t.XL if widget is self.gallery else t.XL, 0))
            self.motion.setEndValue(QPoint())
            self.motion.start()

    def _group(self, group: str) -> None:
        if group == "Downloadable" and not self.online:
            self.reload(online=True)

    def reload(self, *, online: bool = False, select: str = "") -> None:
        self.generation += 1
        generation = self.generation
        installation = self.ctx.installation
        source = self.data.catalogue if self.data else None
        if online:
            self.online = True
        self.gallery.info.setText("Checking for skin packs…" if online else "Reading your looks…")
        self.gallery.refresh.setEnabled(False)
        job = self.ctx.tasks.start(lambda _: self.store.collection(installation, source, online))

        def loaded(data: Collection) -> None:
            if generation != self.generation or not self.entered:
                return
            self.data = data
            self.gallery.set_collection(data, self.game)
            self.gallery.restore.setEnabled(installation.ready)
            self.gallery.refresh.setEnabled(True)
            count = sum(bool(v.pack) for v in data.looks)
            pack_status = (
                ("Showing the offline catalogue. " + data.catalogue.detail)
                if data.catalogue.detail
                else f"{count} downloadable skin{'s' if count != 1 else ''} available."
                if count
                else "No downloadable packs in this catalogue."
            )
            current = f"Current look · {data.active_name}     " if installation.path else ""
            self.gallery.info.setText(current + pack_status)
            key = (
                select
                if self.mode == "gallery"
                else (
                    self.look.key if self.look and self.mode == "detail" and not self.naming else ""
                )
            )
            if key and (look := next((v for v in data.looks if v.key == key), None)):
                self.open_look(look, clear=False)
            elif key:
                self.mode, self.look = "gallery", None
                self.stack.setCurrentWidget(self.gallery)
                self.view.preview.clear()
                self.gallery.grid.setFocus()
            if data.pending and installation.ready and not self.tried_recovery:
                self.tried_recovery = True
                self.change_game("recover")

        def failed(exc: Exception) -> None:
            if generation == self.generation:
                self.gallery.refresh.setEnabled(True)
                self.gallery.info.setText("Could not read the collection.")

                def retry_load() -> None:
                    self.note()
                    self.reload(online=online)

                self.note(str(exc), retry_load)

        job.result.connect(loaded)
        job.error.connect(failed)

    def open_look(self, look: Look, *, clear: bool = True) -> None:
        same_selection = self.mode == "detail" and self.look and self.look.key == look.key
        if clear:
            self.note()
            self.view.preview.clear()
            self.view.preview.scenes.buttons["Main menu"].setChecked(True)
            self.view.preview.turn.setChecked(False)
        self.mode, self.naming, self.look = "detail", "", look
        self.view.back.setText("Gallery")
        self._show(self.view)
        self.view.show_panel(self.view.inspector)
        self.view.title.set_value(look.name)
        self.view.kicker.setText("ACTIVE IN GAME" if look.active else "LOOK PREVIEW")
        self.view.inspector.display(
            look, self.ctx.installation.ready, bool(self.data and self.data.restore_pack)
        )
        if clear or not same_selection:
            with QSignalBlocker(self.view.inspector.highlight):
                self.view.inspector.highlight.setChecked(
                    bool(self.data and self.data.highlights.get(look.name))
                )
        self._preview()
        if clear:
            self.view.back.setFocus()

    def _preview(self) -> None:
        if self.look:
            self.view.preview.set_look(
                self.look,
                self.maker.theme if self.mode == "maker" else self.look.theme,
                self.mode == "maker",
                self.game,
                self.data.scale if self.data else True,
                self.view.inspector.highlight.isChecked(),
            )

    def create(self) -> None:
        self._maker(None, replace(themes.PRESETS[0], name="My theme"))

    def customise(self) -> None:
        look = self.look
        if look:
            theme = look.theme
            if look.group != "Mine":
                theme = replace(theme, name=f"{look.name[:54]} remix")
            self._maker(look, theme)
            if look.pack:
                self.note(
                    "Your new theme starts with this skin’s colours. Its original artwork stays with the pack."
                )

    def _maker(self, origin: Look | None, theme: themes.Theme) -> None:
        self.note()
        self.origin, self.base = origin, theme
        self.look = origin or Look("", theme, "Draft")
        self.mode = "maker"
        self._show(self.view)
        self.view.show_panel(self.view.maker_panel)
        self.view.title.set_value("Theme maker")
        self.view.back.setText("Look preview" if origin else "Gallery")
        self.maker.reset(theme)
        self.maker.name.setFocus()

    def _edited(self) -> None:
        if self.mode == "maker":
            self.view.kicker.setText(
                "UNSAVED CHANGES"
                if self.dirty
                else "MY LIBRARY"
                if self.look.group == "Mine"
                else "NEW THEME"
            )
            self._preview()

    def _run(
        self,
        work: Work,
        done: Callable,
        message: str,
        *,
        writing: bool = False,
        retry: Callable | None = None,
        transfer: bool = False,
    ) -> None:
        if self.job:
            return
        self.generation += 1
        self.note(message)
        self.view.back.setEnabled(False)
        self.gallery.setEnabled(False)
        for panel in self.view.panels:
            panel.setEnabled(panel is self.view.progress)
        if writing:
            self.ctx.set_busy(True)
        job = self.job = self.ctx.tasks.start(work)

        def release() -> None:
            self.job = None
            self.view.back.setEnabled(True)
            self.gallery.setEnabled(True)
            self.gallery.refresh.setEnabled(True)
            for panel in self.view.panels:
                panel.setEnabled(True)
            if writing:
                self.ctx.set_busy(False)

        def ok(value: object) -> None:
            release()
            done(value)

        def fail(exc: Exception) -> None:
            release()
            if transfer:
                self.view.show_panel(self.view.inspector)
                self.reload()
            if isinstance(exc, DownloadError) and exc.reason == DownloadFailure.CANCELLED:
                self.note("Download cancelled. You can resume it here.", retry, "Resume")
            elif isinstance(exc, DownloadError):
                self.note(download_message(exc), retry)
                self.message.setToolTip(str(exc))
            else:
                self.note(str(exc), retry)
            if writing:
                self.reload()

        job.result.connect(ok)
        job.error.connect(fail)
        if transfer:
            job.progress.connect(self._progress)

    def primary(self) -> None:
        if not self.look or not self.data:
            return
        if self.look.pack and self.look.availability in ("Not downloaded", "Update available"):
            self.download()
        elif (
            self.look.active_pack
            and self.look.name in self.data.highlights
            and self.view.inspector.highlight.isChecked() != self.data.highlights[self.look.name]
        ):
            self.change_game("highlight")
        else:
            self.change_game("apply")

    def change_game(self, action: str, *, chosen_highlight: bool | None = None) -> None:
        game, look = self.game, self.look
        if self.job or not game or not self.ctx.installation.ready or not self.data:
            return
        if action == "remove" and not self.ctx.confirm(
            "Remove theme from game?",
            "Remove the installed theme files? If this look is active, the game will return to its previous look.",
        ):
            return
        shared = self.data.shared
        highlight = (
            self.view.inspector.highlight.isChecked()
            if chosen_highlight is None
            else chosen_highlight
        )

        def done(message: str) -> None:
            self.note(message)
            self.reload()

        self._run(
            lambda _: self.store.change(game, action, look, shared, highlight),
            done,
            "Updating this installation. Keep this page open until the change finishes.",
            writing=True,
            retry=partial(self.change_game, action, chosen_highlight=highlight),
        )

    def download(self) -> None:
        pack = self.look.pack.pack
        shared = self.data.shared
        if not shared:
            return
        self.view.show_panel(self.view.progress)
        self.view.progress.begin(f"Downloading {pack.title}")

        def done(_: object) -> None:
            self.view.show_panel(self.view.inspector)
            self.note("Skin and shared fonts verified. Apply when you’re ready.")
            self.reload()

        self._run(
            lambda worker: asyncio.run(self.store.download(pack, shared, worker)),
            done,
            "Downloading to your library. You can cancel and resume.",
            transfer=True,
            retry=self.download,
        )

    def _progress(self, event: tuple[str, Progress]) -> None:
        name, p = event
        progress = self.view.progress
        progress.phase.setText(f"Downloading {name}")
        if p.total_bytes:
            progress.advance_to(min(100, 100 * p.bytes_done // p.total_bytes), 100)
            if p.bytes_done >= p.total_bytes:
                progress.phase.setText(f"Verifying {name}")
        total = f" of {p.total_bytes / 1048576:.1f}" if p.total_bytes else ""
        progress.counts.setText(
            f"{p.bytes_done / 1048576:.1f}{total} MB · {p.bytes_per_second / 1024:.0f} KB/s"
        )

    def _cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.view.progress.cancel.setEnabled(False)
