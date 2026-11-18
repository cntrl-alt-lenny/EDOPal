"""Saved-set and download views."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout

import config
from content.catalogue import PackState
from content.download import Progress
from studio import theme as t
from studio.pages.music_sounds_controls import pack_size
from studio.pages.sounds_model import Downloadable
from studio.pages.sounds_views import InlineView, button_row, elided
from studio.widgets import Button, Hero, Notice, Panel, ProgressBlock, glyph, label

DOWNLOAD_LABELS = {
    PackState.NOT_DOWNLOADED: ("Not downloaded", "Download"),
    PackState.DOWNLOADING: ("Downloading in another window", ""),
    PackState.DOWNLOADED: ("Downloaded", ""),
    PackState.UPDATE_AVAILABLE: ("Update available", "Download update"),
    PackState.INCOMPATIBLE: ("Requires a newer app version", ""),
}


class LibraryView(InlineView):
    def __init__(self) -> None:
        super().__init__(
            "Sound collection", "Find your saved mixes and downloaded sound sets here."
        )
        self.import_button = Button("Import a set", "primary", "folder")
        self.actions.addWidget(self.import_button)
        self.hint = Notice()
        self.body.addWidget(self.hint)
        self.cards = QGridLayout()
        self.cards.setSpacing(t.MD)
        self.body.addLayout(self.cards)
        self.downloaded = QGridLayout()
        self.body.addLayout(self.downloaded)
        self.body.addStretch(1)


class DownloadsView(InlineView):
    def __init__(self) -> None:
        super().__init__(
            "Downloadable sets", "Download a set to hear it and use its sounds in a mix."
        )
        self.refresh = Button("Refresh catalog", "secondary")
        self.actions.addWidget(self.refresh)
        self.hint = Notice()
        self.body.addWidget(self.hint)
        self.empty = Hero("Sound sets", "Catalog offline", "", "sounds")
        self.empty.setFixedHeight(t.TILE_LARGE)
        self.body.addWidget(self.empty)
        self.empty.hide()
        self.browse_local = Button("Browse built-in sets", "secondary", "back")
        self.body.addWidget(self.browse_local, 0, Qt.AlignmentFlag.AlignLeft)
        self.browse_local.hide()
        self.cards = QGridLayout()
        self.cards.setSpacing(t.MD)
        self.body.addLayout(self.cards)
        self.body.addStretch(1)


class DownloadCard(Panel):
    def __init__(self, item: Downloadable, start: Callable[[], None]) -> None:
        super().__init__(t.LG)
        pack = item.pack
        copy = QHBoxLayout()
        copy.setSpacing(t.MD)
        copy.addWidget(glyph("sounds"))
        title = elided("subheading")
        title.set_value(pack.title)
        copy.addWidget(title, 1)
        status, action = DOWNLOAD_LABELS[item.status.state]
        if item.status.state is PackState.INCOMPATIBLE:
            status = f"Requires {config.APP_NAME} {pack.min_app_version}"
        if item.paused:
            status, action = "Paused", "Resume download"
        if item.error:
            status, action = item.error, "Retry download"
        self.status = label(f"{status}  ·  {pack_size(pack.size_bytes)}", "small", True)
        self.body.addLayout(copy)
        self.body.addWidget(label(pack.description, "body", True))
        credit = elided("muted")
        credit.set_value(
            f"{pack.author}  ·  {pack.license}\n{pack.attribution}",
            f"{pack.author}  ·  {pack.license}",
        )
        self.body.addWidget(credit)
        self.body.addWidget(self.status)
        self.download = Button(action or "Download", "secondary", "download")
        self.download.clicked.connect(start)
        self.download.setVisible(bool(action))
        self.buttons = button_row(self.download)
        self.buttons.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.body.addLayout(self.buttons)
        self.progress = ProgressBlock()
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.notice = Notice()
        self.body.addWidget(self.notice)

    def begin(self) -> None:
        self.notice.hide()
        self.download.hide()
        self.status.hide()
        self.progress.begin("Downloading…")
        self.progress.cancel.setText("Pause download")
        self.progress.show()

    def advance(self, progress: Progress) -> None:
        if progress.total_bytes:
            self.progress.advance_to(progress.bytes_done, progress.total_bytes)
            self.progress.counts.setText(
                f"{pack_size(progress.bytes_done)} of {pack_size(progress.total_bytes)}"
            )
            if progress.bytes_done >= progress.total_bytes:
                self.progress.phase.setText("Verifying…")
        if progress.bytes_per_second:
            self.progress.detail.setText(f"{pack_size(progress.bytes_per_second)}/s")

    def interrupted(self, message: str, retry: str) -> None:
        self.progress.hide()
        self.status.setVisible(not message)
        if not message:
            self.status.setText("Paused")
        self.notice.show_message(message)
        self.download.setText(retry)
        self.download.show()
