"""Inline folder chooser. Discovery and validation run outside the event loop."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

import config
from studio import theme as t
from studio.context import AppContext, Installation
from studio.icons import icon
from studio.tasks import WorkerContext
from studio.widgets import Button, label, margins


class InstallationPicker(QWidget):
    dismissed = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.serial = 0
        body = QVBoxLayout(self)
        margins(body, t.XXL)
        body.setSpacing(t.LG)
        body.addStretch()
        body.addWidget(label("GAME FOLDERS", "goldKicker"))
        body.addWidget(label("Choose your installation", "display", True))
        body.addWidget(
            label("Artwork, customization and decks use the folder you select.", "body", True)
        )
        self.hint = label("", "muted", True)
        body.addWidget(self.hint)
        self.loading = QProgressBar()
        self.loading.setRange(0, 0)
        self.loading.setTextVisible(False)
        self.loading.setFixedHeight(t.XS)
        body.addWidget(self.loading)
        self.list = QListWidget()
        self.list.setObjectName("installList")
        self.list.setSpacing(t.XS)
        self.list.setIconSize(QSize(t.ICON_BIG, t.ICON_BIG))
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setAccessibleName("Discovered installations")
        self.list.itemDoubleClicked.connect(self._use)
        self.list.itemActivated.connect(self._use)
        self.list.currentRowChanged.connect(lambda row: self.use.setEnabled(row >= 0))
        body.addWidget(self.list)
        actions = QHBoxLayout()
        self.browse = Button("Browse…", "secondary", "folder")
        self.browse.clicked.connect(self._browse)
        self.back = Button("Cancel", "quiet")
        self.back.clicked.connect(self.close_picker)
        self.use = Button("Use this folder", "primary", "check")
        self.use.clicked.connect(self._use)
        actions.addWidget(self.browse)
        actions.addStretch()
        actions.addWidget(self.back)
        actions.addWidget(self.use)
        body.addLayout(actions)
        body.addStretch()

    def open_picker(self) -> None:
        self.serial += 1
        serial = self.serial
        path = self.ctx.installation.path or self.ctx.cfg.edopro_path
        self.list.clear()
        self.list.hide()
        self.loading.show()
        self.use.setEnabled(False)
        self.browse.setEnabled(True)
        self.back.setEnabled(True)
        self.hint.setText("Searching your usual game folders…")
        job = self.ctx.tasks.start(lambda _: config.find_edopro_folders([path]))
        job.result.connect(lambda paths: self._found(serial, paths))
        job.error.connect(lambda exc: self._failed(serial, exc))
        self.browse.setFocus()

    def _found(self, serial: int, paths: list[str]) -> None:
        if self.serial != serial:
            return
        self.loading.hide()
        self.list.setVisible(bool(paths))
        height = t.BUTTON_H + t.LG
        for path in paths:
            item = QListWidgetItem(icon("folder", t.GOLD), f"{Path(path).name}\n{path}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(0, height))
            self.list.addItem(item)
        self.list.setFixedHeight(min(t.PICKER_ROWS, len(paths)) * (height + 2 * t.XS) + t.MD)
        if paths:
            current = self.ctx.installation.path
            self.list.setCurrentRow(paths.index(current) if current in paths else 0)
            self.hint.setText("Nearby installations")
        else:
            self.hint.setText("No installations found nearby. Browse to your ProjectIgnis folder.")

    def _failed(self, serial: int, error: Exception) -> None:
        if self.serial == serial:
            self.loading.hide()
            self.list.setEnabled(True)
            self.use.setEnabled(self.list.currentRow() >= 0)
            self.hint.setText(str(error))
            self.back.setEnabled(True)
            self.browse.setEnabled(True)

    @Slot()
    def _browse(self) -> None:
        if path := self.ctx.choose_folder(self.ctx.installation.path):
            self._validate(path)

    @Slot()
    def _use(self) -> None:
        if self.use.isEnabled() and (item := self.list.currentItem()):
            self._validate(item.data(Qt.ItemDataRole.UserRole))

    def _validate(self, path: str) -> None:
        self.serial += 1
        serial = self.serial
        self.use.setEnabled(False)
        self.back.setEnabled(False)
        self.browse.setEnabled(False)
        self.hint.setText("Checking game folder…")
        self.loading.show()
        self.list.setEnabled(False)
        settings = self.ctx.cfg.config_path

        def inspect(_: WorkerContext) -> tuple[Installation, bool]:
            installation = Installation.inspect(path)
            saved = bool(installation.path) and config.save_edopro_path(settings, installation.path)
            return installation, saved

        def accepted(result: tuple[Installation, bool]) -> None:
            if self.serial != serial:
                return
            self.list.setEnabled(True)
            self.back.setEnabled(True)
            self.browse.setEnabled(True)
            self.use.setEnabled(self.list.currentRow() >= 0)
            self.loading.hide()
            installation, saved = result
            if not installation.path:
                self.hint.setText("Choose the game folder containing EDOPro or its card databases.")
                return
            self.ctx.cfg.folder_detected = False
            self.ctx.set_installation(installation)
            if not saved:
                self.ctx.notify(
                    "Folder selected for this session. Studio could not save the preference."
                )
            self.close_picker()

        job = self.ctx.tasks.start(inspect)
        job.result.connect(accepted)
        job.error.connect(lambda exc: self._failed(serial, exc))

    def close_picker(self) -> None:
        if self.back.isEnabled():
            self.serial += 1
            self.dismissed.emit()
