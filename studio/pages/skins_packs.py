"""Inline library-pack browser shared by the two appearance pages."""

import asyncio
from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from content import catalogue, libraries
from content.cache import CacheBusyError, PackCache
from content.catalogue import Catalogue, PackState, PackStatus
from content.download import DownloadError, DownloadFailure, Progress
from content.media import Changes, Origin
from sleeves import Library
from studio import theme as t
from studio.context import AppContext
from studio.tasks import Job, WorkerContext
from studio.widgets import Button, ElidedLabel, Hero, Panel, glyph, label, margins
from themes import ThemeLibrary

from .settings_storage import byte_size
from .skins_model import download_message


@dataclass(frozen=True)
class Listing:
    title: str
    origin: Origin
    state: str
    status: PackStatus | None
    removable: bool


def listings(cache: PackCache, source: Catalogue, kind: str, version: str) -> list[Listing]:
    root = cache.root.parent
    if kind == "wallpaper":
        entries = ThemeLibrary(root / "themes").wallpapers()
    else:
        entries = Library(root / "sleeves").entries()
    installed = {e.origin.id: e.origin for e in entries if e.origin and not e.retained}
    rows = []
    for status in source.list_packs(cache, app_version=version, kind=kind):
        pack = status.pack
        current = installed.pop(pack.id, None)
        state = {
            PackState.DOWNLOADING: "Downloading",
            PackState.INCOMPATIBLE: "Incompatible",
            PackState.DOWNLOADED: "Downloaded",
            PackState.UPDATE_AVAILABLE: "Update available",
            PackState.NOT_DOWNLOADED: "Not downloaded",
        }[status.state]
        if current and status.state not in (PackState.INCOMPATIBLE, PackState.DOWNLOADING):
            state = (
                "Available in library"
                if (current.version, current.sha256) == (pack.version, pack.sha256)
                else "Update available"
            )
        rows.append(
            Listing(
                pack.title, Origin.from_pack(pack), state, status, bool(current or status.cached)
            )
        )
    rows.extend(Listing(o.id, o, "Available in library", None, True) for o in installed.values())
    return rows


class PackBrowser(QWidget):
    def __init__(self, ctx: AppContext, kind: str) -> None:
        super().__init__()
        self.ctx, self.kind = ctx, kind
        self.cache = PackCache.for_config(ctx.cfg)
        self.source: Catalogue | None = None
        self.job: Job | None = None
        self.loading = False
        self.rows: list[Listing] = []
        noun = "wallpapers" if kind == "wallpaper" else "sleeves"
        outer = QHBoxLayout(self)
        margins(outer, 0)
        content = QWidget()
        content.setMaximumWidth(t.WIDE_CONTENT)
        outer.addStretch(1)
        outer.addWidget(content, 100)
        outer.addStretch(1)
        body = QVBoxLayout(content)
        margins(body, 0)
        nav = QHBoxLayout()
        self.back = Button("Back", "quiet", "back")
        self.refresh = Button("Refresh catalogue", "secondary")
        self.refresh.clicked.connect(lambda: self.load(online=True))
        nav.addWidget(self.back)
        nav.addStretch()
        nav.addWidget(self.refresh)
        body.addLayout(nav)
        body.addWidget(label(f"Get more {noun}", "display"))
        self.source_hint = label("", "muted", True)
        body.addWidget(self.source_hint)
        columns = QHBoxLayout()
        body.addLayout(columns, 1)
        self.menu = QListWidget()
        self.menu.setObjectName("installList")
        self.menu.setAccessibleName("Catalogue packs")
        self.menu.setWordWrap(True)
        self.menu.currentRowChanged.connect(self.display)
        columns.addWidget(self.menu, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        columns.addWidget(scroll, 4)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        margins(layout, 0)
        scroll.setWidget(inner)
        self.empty = Hero(
            "Catalogue",
            "No packs yet",
            "Use a built-in wallpaper or your own picture in the theme maker."
            if kind == "wallpaper"
            else "Browse the ready-made sleeves or design one for your collection.",
            "skins" if kind == "wallpaper" else "sleeves",
        )
        self.empty.setFixedHeight(t.HERO_MIN)
        layout.addWidget(self.empty)
        self.card = Panel(t.XL)
        layout.addWidget(self.card)
        layout.addStretch()
        self.card.body.addWidget(glyph("skins" if kind == "wallpaper" else "sleeves", t.ICON_BIG))
        self.title = label("", "heading", True)
        self.description = label("", "body", True)
        self.credits = label("", "muted", True)
        self.link = ElidedLabel()
        self.feedback = label("", "body", True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(t.SM)
        for widget in (
            self.title,
            self.description,
            self.credits,
            self.link,
            self.feedback,
            self.progress,
        ):
            self.card.body.addWidget(widget)
        for field in (self.title, self.description, self.credits, self.feedback, self.source_hint):
            field.setTextFormat(Qt.TextFormat.PlainText)
        actions = QHBoxLayout()
        actions.addStretch()
        self.remove = Button("Remove")
        self.remove.clicked.connect(self.confirm_remove)
        self.cancel = Button("Cancel")
        self.cancel.clicked.connect(self.stop)
        self.action = Button("Download", "primary", "download")
        self.action.clicked.connect(lambda: self.run(False))
        for widget in (self.remove, self.cancel, self.action):
            actions.addWidget(widget)
        self.card.body.addLayout(actions)
        self.display()

    def load(self, *, online: bool = False, message: str = "", retry: str = "") -> None:
        if self.job or self.loading:
            return
        self.loading = True
        self.refresh.setEnabled(False)
        self.action.setEnabled(False)
        self.remove.setEnabled(False)
        self.source_hint.setText("Checking the pack catalogue…")
        self.empty.hide()
        source, url = self.source, self.ctx.cfg.content_manifest_url
        key = self.rows[self.menu.currentRow()].origin.id if self.menu.currentRow() >= 0 else ""

        def read(_: WorkerContext) -> tuple[Catalogue, list[Listing]]:
            try:
                if online:
                    cat = asyncio.run(catalogue.fetch_catalogue(self.cache, config_url=url))
                else:
                    cat = source or catalogue.offline_catalogue(self.cache, config_url=url)
            except (OSError, ValueError) as exc:
                cat = Catalogue(
                    catalogue.offline_manifest(self.cache, config_url=url),
                    catalogue.CatalogueSource.CACHED,
                    str(exc),
                )
            return cat, listings(self.cache, cat, self.kind, self.ctx.version)

        def done(result: tuple[Catalogue, list[Listing]]) -> None:
            self.source, self.rows = result
            self.loading = False
            self.refresh.setEnabled(True)
            origin = {
                catalogue.CatalogueSource.LIVE: "Latest catalogue",
                catalogue.CatalogueSource.CACHED: "Saved catalogue",
                catalogue.CatalogueSource.BUNDLED: "Built-in catalogue",
            }[self.source.source]
            if self.source.detail and self.source.source != catalogue.CatalogueSource.LIVE:
                origin = "Offline catalogue"
            self.source_hint.setText(
                f"{origin} · {self.source.detail}" if self.source.detail else origin
            )
            with QSignalBlocker(self.menu):
                self.menu.clear()
                selected = 0
                for index, row in enumerate(self.rows):
                    item = QListWidgetItem(f"{row.title}\n{row.state}")
                    item.setToolTip(item.text())
                    self.menu.addItem(item)
                    if row.origin.id == key:
                        selected = index
                self.menu.setCurrentRow(selected)
            self.display()
            if message:
                self.feedback.setText(message)
                if not self.rows:
                    self.source_hint.setText(f"{message}  ·  {self.source_hint.text()}")
            if (
                retry
                and self.rows
                and key == self.rows[self.menu.currentRow()].origin.id
                and self.action.isEnabled()
            ):
                self.action.setText(retry)

        def failed(exc: Exception) -> None:
            self.loading = False
            self.refresh.setEnabled(True)
            self.display()
            self.source_hint.setText(f"Could not read the catalogue. {exc}")

        job = self.ctx.tasks.start(read)
        job.result.connect(done)
        job.error.connect(failed)

    def display(self) -> None:
        i = self.menu.currentRow()
        row = self.rows[i] if 0 <= i < len(self.rows) else None
        self.progress.hide()
        self.cancel.hide()
        self.menu.setVisible(bool(row))
        self.empty.setVisible(not row)
        self.card.setVisible(bool(row))
        self.menu.setEnabled(True)
        self.back.setEnabled(True)
        self.action.setVisible(bool(row and row.status and row.state != "Available in library"))
        self.remove.setVisible(bool(row and row.removable))
        self.remove.setEnabled(not self.loading and bool(row and row.state != "Downloading"))
        self.credits.setVisible(bool(row))
        self.link.setVisible(bool(row))
        self.feedback.setText("")
        if row:
            o, pack = row.origin, row.status.pack if row.status else None
            self.title.setText(row.title)
            self.description.setText(pack.description if pack else "Saved in your library.")
            self.credits.setText(f"By {o.author}  ·  Licence · {o.license}\n{o.attribution}")
            self.link.set_value(o.source_url)
            self.feedback.setText(f"{row.state}  ·  Version {o.version}")
            self.action.setText(
                {"Downloaded": "Add to library", "Update available": "Download update"}.get(
                    row.state, "Download"
                )
            )
            self.action.setEnabled(
                not self.loading and row.state not in ("Incompatible", "Downloading")
            )

    def stop(self) -> None:
        if self.job:
            self.job.cancel()
            self.cancel.setEnabled(False)
            self.feedback.setText("Cancelling download…")

    def confirm_remove(self) -> None:
        row = self.rows[self.menu.currentRow()]
        if self.ctx.confirm(
            "Remove pack?", f"Remove {row.title} from your library and download cache?"
        ):
            self.run(True)

    def run(self, removing: bool) -> None:
        if self.job or self.loading:
            return
        row = self.rows[self.menu.currentRow()]
        self.menu.setEnabled(False)
        self.refresh.setEnabled(False)
        self.back.setEnabled(False)
        self.action.hide()
        self.remove.hide()
        self.cancel.setVisible(not removing)
        self.cancel.setEnabled(True)
        self.progress.show()
        self.progress.setRange(0, 0)
        self.feedback.setText("Removing…" if removing else "Starting download…")
        self.menu.currentItem().setText(
            f"{row.title}\n" + ("Removing" if removing else "Downloading")
        )

        def work(worker: WorkerContext) -> Changes:
            if removing:
                return libraries.remove_pack(self.cache, row.origin.id)
            return asyncio.run(
                libraries.make_available(
                    self.cache,
                    row.status,
                    app_version=self.ctx.version,
                    cancel=worker.cancel,
                    phase=worker.progress,
                    progress=worker.progress,
                )
            )

        def done(changes: Changes) -> None:
            self.job = None
            message = "Pack removed." if removing else "Available in library."
            if changes.retained:
                message += f" Kept {len(changes.retained)} personal or assigned items."
            self.load(message=message)

        def failed(exc: Exception) -> None:
            self.job = None
            cancelled = isinstance(exc, DownloadError) and exc.reason == DownloadFailure.CANCELLED
            self.load(
                message=(
                    "Download cancelled."
                    if cancelled
                    else "The pack cache is busy. Try again when the other operation finishes."
                    if isinstance(exc, CacheBusyError)
                    else download_message(exc)
                    if isinstance(exc, DownloadError)
                    else f"Invalid pack. {exc}"
                    if isinstance(exc, ValueError)
                    else str(exc)
                ),
                retry="Resume" if cancelled else "Retry",
            )

        self.job = self.ctx.tasks.start(work)
        self.job.progress.connect(self.advance)
        self.job.result.connect(done)
        self.job.error.connect(failed)

    def advance(self, event: str | Progress) -> None:
        if isinstance(event, str):
            self.feedback.setText(event.capitalize() + "…")
            self.menu.currentItem().setText(f"{self.title.text()}\n{event.capitalize()}")
            self.cancel.setEnabled(event != "installing" and not self.job.cancel_event.is_set())
            self.progress.setRange(0, 0)
        else:
            if event.total_bytes:
                self.progress.setRange(0, 100)
                self.progress.setValue(event.bytes_done * 100 // event.total_bytes)
            amount = byte_size(event.bytes_done)
            if event.total_bytes:
                amount += f" of {byte_size(event.total_bytes)}  ·  {self.progress.value()}%"
            self.feedback.setText(
                f"Downloading  ·  {amount}  ·  {byte_size(int(event.bytes_per_second))}/s"
            )
