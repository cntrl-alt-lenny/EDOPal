"""Game appearances, the local theme library and the live theme maker."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

import extras
import themes
from content.download import DownloadError, DownloadFailure, Progress
from studio import theme as t
from studio.context import AppContext, GameOpenError, Installation
from studio.tasks import Job, Work
from studio.widgets import Button, label, margins

from .settings_storage import byte_size
from .skins_detail import Workspace
from .skins_feedback import AppearancePage
from .skins_gallery import Gallery
from .skins_library import LibraryActions
from .skins_model import Look, download_message
from .skins_packs import PackBrowser
from .skins_service import Collection, SkinStore


class SkinsPage(AppearancePage):
    id, title, icon = "skins", "Skins", "skins"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.built = False

    def _build(self, context: AppContext) -> None:
        self.built = True
        self.store = SkinStore(context.cfg, context.version)
        self.data: Collection | None = None
        self.look: Look | None = None
        self.origin: Look | None = None
        self.base: themes.Theme | None = None
        self.paused: set[str] = set()
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
        self.setup_feedback()
        row.addWidget(self.details)
        row.addWidget(self.retry_button)
        body.addWidget(self.message_bar)
        self.message_bar.hide()
        self.install = QWidget()
        row = QHBoxLayout(self.install)
        margins(row, 0)
        self.install_hint = label("", "body", True)
        self.choose = Button("Choose folder", "secondary", "folder")
        self.choose.clicked.connect(self.check_folder)
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
        self.packs = PackBrowser(
            context, "wallpaper", lambda title, message: self.confirm(title, message)
        )
        self.packs.back.clicked.connect(self.escape)
        self.packs.view_requested.connect(self.view_wallpapers)
        self.gallery.more.clicked.connect(self.open_packs)
        self.maker = self.view.maker
        self.stack.addWidget(self.gallery)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.packs)
        self.setup_block()
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
        self.view.progress.cancel.setText("Pause download")
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
        self.technical_error(None)
        self.message.setText(text)
        self.message.setToolTip("")
        self.message_bar.setVisible(bool(text))
        self.retry_button.setText(button)
        self.retry_button.setVisible(action is not None)

    def installation_changed(self, installation: Installation) -> None:
        if not self.built:
            return
        self.leave_block()
        self.generation += 1
        self.data = None
        self.gallery.data = None
        self.gallery.looks = []
        self.gallery.filter()
        self.gallery.restore.hide()
        self.tried_recovery = False
        self.install.setVisible(not installation.ready)
        self.install_hint.setText(
            "Choose your EDOPro folder. You can still design and save themes."
            if not installation.path
            else "Finish setting up EDOPro. Open EDOPro and let its updates finish, then choose Check again."
        )
        self.choose.setText("Check again" if installation.path else "Choose folder")
        if self.mode == "packs":
            return  # Library transfers are independent of the selected game.
        if self.entered and not self._discard():
            # Asynchronous installation detection can finish after editing has begun.
            self._preview()
        else:
            self.to_gallery()
        if self.entered:
            self.reload()

    def check_folder(self) -> None:
        if self.game:
            self.ctx.select_installation_requested.emit(str(self.game))
        else:
            self.ctx.installations_requested.emit()

    def shown(self) -> None:
        if not self.built:
            self._build(self.ctx)
            self.installation_changed(self.ctx.installation)
        self.entered = True
        self.tried_recovery = False
        if self.mode == "packs":
            self.packs.setFocus()
        else:
            self.reload()
            self.gallery.search.setFocus()

    def hidden(self) -> bool:
        if not self.built:
            return True
        if self.job or not self._discard():
            return False
        self.leave_block()
        self.entered = False
        self.motion.stop()
        self.gallery.grid.suspend()
        self.maker.samples.stop()
        self.generation += 1
        if self.mode != "packs":
            self.to_gallery()
        return True

    def _discard(self) -> bool:
        return not (self.dirty or self.files.name_edited) or self.confirm(
            "Discard unsaved changes?", "Leave without saving your theme edits?"
        )

    def escape(self) -> bool:
        if self.leave_block() or self.job or self.packs.job:
            return True
        if self.mode == "packs":
            self.to_gallery()
            self.reload()
            self.gallery.more.setFocus()
        elif self.naming:
            self.files.cancel_name()
        elif self.mode == "maker":
            if self._discard():
                if self.origin:
                    self.open_look(self.origin)
                    self.view.inspector.edit.setFocus()
                else:
                    self.to_gallery()
                    self.gallery.create.setFocus()
        elif self.mode == "detail":
            self.to_gallery()
        else:
            return False
        return True

    def open_packs(self) -> None:
        self.note()
        self.mode = "packs"
        self._show(self.packs)
        self.packs.load(online=True)
        self.packs.back.setFocus()

    def view_wallpapers(self, pack_id: str) -> None:
        self.to_gallery()
        self.reload(wallpaper_pack=pack_id)

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
        if self.entered and widget is not self.packs:
            self.motion.setTargetObject(widget)
            self.motion.setStartValue(QPoint(-t.XL if widget is self.gallery else t.XL, 0))
            self.motion.setEndValue(QPoint())
            self.motion.start()

    def _group(self, group: str) -> None:
        if group == "Downloadable" and not self.online:
            self.reload(online=True)

    def reload(self, *, online: bool = False, select: str = "", wallpaper_pack: str = "") -> None:
        self.generation += 1
        generation = self.generation
        installation = self.ctx.installation
        source = self.data.catalogue if self.data else None
        if online:
            self.online = True
        self.gallery.info.setText(
            "Checking for skin packs…" if online else "Reading skins and themes…"
        )
        self.gallery.refresh.setEnabled(False)
        job = self.ctx.tasks.start(lambda _: self.store.collection(installation, source, online))

        def loaded(data: Collection) -> None:
            # A result can arrive after the window has been torn down.
            if generation != self.generation or not self.entered or not isValid(self.gallery):
                return
            self.data = data
            self.gallery.set_collection(data, self.game)
            self.maker.set_wallpapers(data.wallpapers)
            self.gallery.restore.setEnabled(installation.ready)
            if wallpaper_pack and self.mode == "gallery":
                row = next((w for w in data.wallpapers if w.origin.id == wallpaper_pack), None)
                if row:
                    self.create()
                    self.maker.wall_toggle.setChecked(True)
                    self.maker.pick_wallpaper(self.maker.wallpapers.findData(row.digest))
                    self.view.scroll.ensureWidgetVisible(self.maker.wallpapers)
                else:
                    self.note("This pack is no longer in your wallpaper collection.")
            self.gallery.refresh.setEnabled(True)
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
            if data.issues:
                issue = data.issues[0]

                def set_aside() -> None:
                    self._run(
                        lambda _: self.store.library.set_aside(issue),
                        lambda _: (self.note(), self.reload()),
                        "Moving the file to quarantine…",
                    )

                self.note(
                    f"Could not load {issue.filename}. Set this file aside or repair it in the theme library.",
                    set_aside if issue.stamp else None,
                    "Set aside file",
                )
                self.message.setToolTip(f"Technical details\n{issue.reason}")
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

                self.note(
                    "Could not read your themes. Check that the library folder is accessible, then choose Retry.",
                    retry_load,
                )
                self.technical_error(exc)

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
        self.view.back.setText("Back")
        self.view.back.setAccessibleName("Back to skins and themes")
        self._show(self.view)
        self.view.show_panel(self.view.inspector)
        self.view.title.set_value(look.name)
        self.view.kicker.setText(
            "SAVED CHANGES"
            if look.needs_update
            else "APPLIED IN EDOPRO"
            if look.active
            else "SKIN PREVIEW"
            if look.pack
            else "THEME PREVIEW"
        )
        self.view.inspector.display(
            look, self.ctx.installation.ready, bool(self.data and self.data.restore_pack)
        )
        if clear or not same_selection:
            with QSignalBlocker(self.view.inspector.highlight):
                self.view.inspector.highlight.setChecked(
                    bool(self.data and self.data.highlights.get(look.name))
                )
        if self.data:
            self.view.inspector.restore.setText(f"Restore original · {self.data.original_skin}")
        self._preview()
        if clear:
            self.view.back.setFocus()

    def _preview(self) -> None:
        if self.look:
            if self.mode == "detail" and self.data:
                self.view.inspector.update_action(
                    self.look,
                    self.ctx.installation.ready,
                    self.look.partial or self.look.key in self.paused,
                    self.data.highlights.get(self.look.name, False),
                )
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
            if look.group != "My themes":
                theme = replace(theme, name=f"{look.name[:54]} remix")
            self._maker(look, theme)
            if look.pack:
                self.note(
                    "Your new theme starts with this skin’s colors. Its original artwork stays with the pack."
                )

    def _maker(self, origin: Look | None, theme: themes.Theme) -> None:
        self.note()
        self.origin, self.base = origin, theme
        self.look = origin or Look("", theme, "Draft")
        self.mode = "maker"
        self._show(self.view)
        self.view.show_panel(self.view.maker_panel)
        self.view.title.set_value("Theme maker")
        self.view.back.setText("Back")
        self.view.back.setAccessibleName(
            "Back to preview" if origin else "Back to skins and themes"
        )
        self.maker.reset(theme)
        self.maker.name.setFocus()

    def _edited(self) -> None:
        if self.mode == "maker":
            self.view.kicker.setText(
                "UNSAVED CHANGES"
                if self.dirty
                else "MY THEMES"
                if self.look.group == "My themes"
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
        apply_enabled = self.view.inspector.apply.isEnabled()
        self.view.game_actions.setEnabled(False)
        self.view.inspector.apply.setEnabled(False)
        self.view.back.setEnabled(False)
        self.maker.save_btn.setEnabled(False)
        self.maker.copy_btn.setEnabled(False)
        self.gallery.setEnabled(False)
        for panel in self.view.panels:
            panel.setEnabled(panel is self.view.progress)
        if writing:
            self.ctx.set_busy(True)
        job = self.job = self.ctx.tasks.start(work)

        def release() -> None:
            self.job = None
            self.view.game_actions.setEnabled(True)
            self.view.inspector.apply.setEnabled(apply_enabled)
            self.view.back.setEnabled(True)
            self.maker.save_btn.setEnabled(True)
            self.maker.copy_btn.setEnabled(True)
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
            if isinstance(exc, GameOpenError) and retry:
                self.note()
                self.show_block(exc, retry, self.message_bar, self.install)
                return
            if transfer:
                self.view.show_panel(self.view.inspector)
                self.reload()
            if isinstance(exc, DownloadError) and exc.reason == DownloadFailure.CANCELLED:
                self.note()
                self.paused.add(self.look.key)
            elif isinstance(exc, DownloadError):
                self.note(download_message(exc), None if transfer else retry)
                self.message.setToolTip(str(exc))
            else:
                self.note(
                    "Could not finish the change. Check folder permissions and free space, then retry."
                    if isinstance(exc, OSError) and not isinstance(exc, extras.SleeveReapplyError)
                    else str(exc),
                    self.retry_sleeves if isinstance(exc, extras.SleeveReapplyError) else retry,
                )
            if not (isinstance(exc, DownloadError) and exc.reason == DownloadFailure.CANCELLED):
                self.technical_error(exc)
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
        if action == "remove" and not self.confirm(
            "Remove theme from EDOPro?",
            "Remove the installed theme files? EDOPro will restore any previous skin settings this theme still controls.",
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
            "Updating the skin… You can change folders when this finishes.",
            writing=True,
            retry=partial(self.change_game, action, chosen_highlight=highlight),
        )

    def retry_sleeves(self) -> None:
        game = self.game
        if game:

            def restored(message: str) -> None:
                self.note(message)
                self.reload()

            self._run(
                lambda _: self.store.change(game, "sleeves", None, None, False),
                restored,
                "Restoring sleeves…",
                writing=True,
                retry=self.retry_sleeves,
            )

    def download(self) -> None:
        pack = self.look.pack.pack
        shared = self.data.shared
        if not shared:
            return
        self.paused.discard(self.look.key)
        self.view.show_panel(self.view.progress)
        self.view.progress.begin(f"Downloading {pack.title}")

        def done(_: object) -> None:
            self.view.show_panel(self.view.inspector)
            self.note("Download complete. Choose Apply skin to use it in EDOPro.")
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
        total = f" of {byte_size(p.total_bytes)}" if p.total_bytes else ""
        progress.counts.setText(
            f"{byte_size(p.bytes_done)}{total} · {byte_size(int(p.bytes_per_second))}/s"
        )

    def _cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.view.progress.cancel.setEnabled(False)
