"""Game-folder selection backed by the installation service."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from edopal.core import Core
from edopal.core.install import Install
from edopal.ui import theme
from edopal.ui.widgets import PrimaryButton, QuietButton


class FolderPicker(QDialog):
    def __init__(self, core: Core, parent):
        super().__init__(parent)
        self.core = core
        self._discover_job = None
        self._selecting = False
        self._attempted = False
        self._finished = False
        self.setWindowTitle("Choose game folder")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(570, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)
        title = QLabel("Choose game folder")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        self.status = QLabel("Looking for EDOPro installations…")
        self.status.setProperty("role", "body")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.listing = QListWidget()
        self.listing.setObjectName("installList")
        self.listing.setAccessibleName("Discovered game folders")
        self.listing.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        layout.addWidget(self.listing, 1)
        actions = QHBoxLayout()
        self.browse = QuietButton("Browse…")
        self.browse.setIcon(theme.icon("folder"))
        self.browse.clicked.connect(self._browse)
        actions.addWidget(self.browse)
        actions.addStretch()
        cancel = QuietButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.choose = PrimaryButton("Use folder")
        self.choose.setEnabled(False)
        self.choose.setDefault(True)
        actions.addWidget(self.choose)
        layout.addLayout(actions)
        self.choose.clicked.connect(self._use_current)
        self.listing.currentRowChanged.connect(lambda row: self.choose.setEnabled(row >= 0 and not self._selecting))
        self.listing.itemDoubleClicked.connect(self._use_current)
        self.finished.connect(self._cleanup)

    def open(self):
        super().open()
        discover = self.core.installs.discover
        self._discover_job = self.core.jobs.run(
            lambda _: discover(), on_done=self._found, on_error=self._discovery_failed, key="shell.discover"
        )

    def _found(self, installs: list[Install]):
        if self._finished:
            return
        current = self.core.installs.current()
        selected = 0
        for index, install in enumerate(installs):
            title = install.label or install.root.name
            if not install.ready:
                title += " · Waiting for databases"
            item = QListWidgetItem(theme.icon("folder"), f"{title}\n{install.root}")
            item.setData(Qt.ItemDataRole.UserRole, install.root)
            item.setToolTip(str(install.root))
            self.listing.addItem(item)
            if current and install.root == current.root:
                selected = index
        if installs:
            self.listing.setCurrentRow(selected)
        if not self._attempted:
            self.status.setText(
                "Select an installation or browse to its folder."
                if installs
                else "No installations found. Browse to your EDOPro folder."
            )

    def _discovery_failed(self, error):
        if not self._finished and not self._attempted:
            self.status.setText(f"Couldn’t search for installations. Browse to your EDOPro folder.\n{error}")

    def _browse(self):
        current = self.core.installs.current()
        root = QFileDialog.getExistingDirectory(self, "Choose EDOPro folder", str(current.root) if current else "")
        if root:
            self._select(Path(root))

    def _use_current(self):
        if item := self.listing.currentItem():
            self._select(item.data(Qt.ItemDataRole.UserRole))

    def _select(self, root: Path):
        if self._selecting:
            return
        self._selecting = self._attempted = True
        self.setEnabled(False)
        self.status.setText("Checking folder…")
        select = self.core.installs.select
        self.core.jobs.run(
            lambda _: select(root),
            on_done=self._selected,
            on_error=self._selection_failed,
            key="shell.select",
        )

    def _selected(self, install: Install):
        if not self._finished:
            self.core.notify(f"Game folder set to {install.label or install.root.name}.", "success")
            self.accept()

    def _selection_failed(self, error):
        if not self._finished:
            self._selecting = False
            self.setEnabled(True)
            self.choose.setEnabled(self.listing.currentRow() >= 0)
            self.status.setText(f"Couldn’t use this folder. {error}")

    def reject(self):
        if not self._selecting:
            super().reject()

    def _cleanup(self):
        self._finished = True
        if self._discover_job:
            self._discover_job.cancel()
