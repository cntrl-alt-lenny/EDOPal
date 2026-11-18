"""Downloadable music and covers. All catalogue and library I/O runs on workers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

import config
from content.cache import CacheBusyError, PackCache
from content.catalogue import Catalogue, CatalogueSource, PackState, PackStatus, fetch_catalogue
from content.download import DownloadError, DownloadFailure, Progress
from content.libraries import make_available, remove_pack
from content.media import Changes, Origin
from music.packs import pack_media
from studio import theme as t
from studio.tasks import Job, WorkerContext
from studio.widgets import Button, Panel, ProgressBlock, label

from .music_albums import credit_text
from .music_sounds_controls import can_resume, pack_size, partial_archives
from .music_widgets import RecordHero, action

if TYPE_CHECKING:
    from .music import MusicPage


@dataclass(frozen=True)
class Entry:
    origin: Origin
    status: PackStatus | None
    installed: bool
    games: tuple[str, ...]
    paused: bool = False

    @property
    def state(self) -> str:
        if not self.status:
            return "Available in library"
        state = self.status.state
        if state == PackState.INCOMPATIBLE:
            return f"Requires {config.APP_NAME} {self.status.pack.min_app_version}"
        if state == PackState.DOWNLOADING:
            return "Downloading in another window"
        if self.installed:
            return "Available in library"
        if self.paused:
            return "Paused"
        if self.games or state == PackState.UPDATE_AVAILABLE:
            return "Update available"
        return "Downloaded" if state == PackState.DOWNLOADED else "Not downloaded"


def entries(cache: PackCache, catalogue: Catalogue, version: str) -> list[Entry]:
    # Read the music index once, including editions withdrawn from the online list.
    installed: dict[str, dict[Origin, set[str]]] = {}
    for row in pack_media(cache.root.parent / "soundtracks"):
        if not row.retained and row.path.is_file():
            installed.setdefault(row.origin.id, {}).setdefault(row.origin, set()).add(row.item.game)
    result = []
    partials = partial_archives(cache)
    for kind in ("music", "covers"):
        for status in catalogue.list_packs(cache, app_version=version, kind=kind):
            pack = status.pack
            editions = installed.pop(pack.id, {})
            origin = Origin.from_pack(pack)
            current = any(
                o.version == origin.version and o.sha256 == origin.sha256 for o in editions
            )
            games = tuple(sorted({g for keys in editions.values() for g in keys}))
            result.append(Entry(origin, status, current, games, can_resume(status, partials)))
    for editions in installed.values():
        for origin, keys in editions.items():
            result.append(Entry(origin, None, True, tuple(sorted(keys))))
    return result


def failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, DownloadError):
        if exc.reason == DownloadFailure.CANCELLED:
            return "Paused", "Resume download"
        title = {
            DownloadFailure.NOT_FOUND: "Pack unavailable",
            DownloadFailure.DISK_SPACE: "Insufficient disk space",
            DownloadFailure.INTEGRITY: "Verification failed",
            DownloadFailure.INCOMPATIBLE: "Incompatible pack",
        }.get(exc.reason, "Download interrupted")
    elif isinstance(exc, CacheBusyError):
        title = "Pack is busy"
    elif isinstance(exc, ValueError):
        title = "Invalid pack"
    else:
        title = "Could not change this pack"
    return f"{title}. {exc}", "Retry"


class MusicPacks:
    def __init__(self, page: MusicPage) -> None:
        self.page = page
        self.cache = PackCache.for_config(page.ctx.cfg)
        self.job: Job | None = None
        self.catalogue: Catalogue | None = None
        self.items: list[Entry] = []
        self.dirty = False
        self.message = ""
        self.outcomes: dict[Origin, tuple[str, str]] = {}
        self.buttons: list[Button] = []
        self.cards: dict[str, Panel] = {}
        self.progress: ProgressBlock | None = None

    def open(self, *, online: bool = True) -> None:
        self.draw()
        if self.job:
            self.enable(False)
        else:
            self.load(online=online)

    def back(self) -> None:
        if self.progress:
            return
        if self.dirty or self.page.data is None:
            self.dirty = False
            self.page.refresh(self.page.imports.manage)
        else:
            self.page.imports.manage()

    def load(self, *, online: bool = True) -> None:
        if self.job:
            return
        cache, version = self.cache, self.page.ctx.version
        url, catalogue = self.page.ctx.cfg.content_manifest_url, self.catalogue
        self.banner.setText("Checking the catalog…" if online else "Reading installed packs…")
        self.enable(False)

        def read(_: WorkerContext) -> tuple[Catalogue, list[Entry]]:
            source = (
                asyncio.run(fetch_catalogue(cache, config_url=url))
                if online or catalogue is None
                else catalogue
            )
            return source, entries(cache, source, version)

        def loaded(result: tuple[Catalogue, list[Entry]]) -> None:
            self.catalogue, self.items = result
            self.message = ""

        job = self.job = self.page.ctx.tasks.start(read)
        job.result.connect(loaded)
        job.error.connect(self.load_error)
        job.finished.connect(self.loaded)

    def load_error(self, error: BaseException) -> None:
        self.message = "Could not read the catalog. Check your connection and refresh the catalog."

    def loaded(self) -> None:
        self.job = None
        if self.page.state == "packs":
            self.draw()

    def enable(self, enabled: bool) -> None:
        for button in self.buttons:
            button.setEnabled(enabled)

    def draw(self) -> None:
        p = self.page
        scroll = p.scroll.verticalScrollBar()
        previous = scroll.value() if p.state == "packs" else 0
        layout = p.screen("packs", self.back, "Get more music", "YOUR LIBRARY")
        self.buttons = []
        self.cards.clear()
        top = QHBoxLayout()
        self.banner = label(self.message, "body", True)
        if not self.message:
            offline = self.catalogue and self.catalogue.source != CatalogueSource.LIVE
            if offline:
                self.banner.setText(
                    "Catalog offline. Your installed music and covers are available."
                )
            else:
                self.banner.setText(
                    "Add tracks and covers to your library. Choose playlists separately."
                )
        refresh = action("Refresh catalog", self.load)
        self.buttons.append(refresh)
        if self.catalogue and not self.items and not self.message:
            offline = self.catalogue.source != CatalogueSource.LIVE
            empty = RecordHero(
                "Music and covers",
                "Catalog offline" if offline else "No packs available yet",
                "You can still add your own tracks and use the music in your library."
                if offline
                else "Add your own tracks, or check back here for music and covers.",
                "music",
            )
            self.banner.deleteLater()
            self.banner = empty.description
            top.addStretch()
            top.addWidget(refresh)
            layout.addLayout(top)
            layout.addWidget(empty)
            local = action("Add local music", self.back)
            layout.addWidget(local, 0, Qt.AlignmentFlag.AlignLeft)
            self.buttons.append(local)
        else:
            top.addWidget(self.banner, 1)
            top.addWidget(refresh)
            layout.addLayout(top)
        for item in self.items:
            pack = item.status.pack if item.status else None
            key = item.origin.id
            panel = Panel(t.LG)
            panel.body.addWidget(
                label(
                    f"{pack.kind.title()}  ·  {pack.version}  ·  {pack_size(pack.size_bytes)}"
                    if pack
                    else f"Installed pack  ·  {item.origin.version}",
                    "goldKicker",
                )
            )
            panel.body.addWidget(label(pack.title if pack else key, "subheading", True))
            if pack:
                panel.body.addWidget(label(pack.description, "body", True))
            panel.body.addWidget(label(credit_text(item.origin), "muted", True))
            # Replace only the actions with progress. Credits stay in the same place.
            actions = QWidget()
            actions.setObjectName("packActions")
            action_body = QVBoxLayout(actions)
            action_body.setContentsMargins(0, 0, 0, 0)
            note, retry = self.outcomes.get(item.origin, ("", ""))
            status = label("Paused" if retry == "Resume download" else item.state, "body", True)
            action_body.addWidget(status)
            if note and note not in ("Paused", "Added to your library."):
                action_body.addWidget(label(note, "notice", True))
            controls = QHBoxLayout()
            controls.addStretch()
            if item.games or (item.status and item.status.cached):
                remove = action("Remove pack", partial(self.change, item, True))
                controls.addWidget(remove)
                self.buttons.append(remove)
            if item.games:
                album = action("View in collection", partial(self.album, item.games[0]))
                controls.addWidget(album)
                self.buttons.append(album)
            if (
                item.status
                and not item.installed
                and item.status.state
                not in (
                    PackState.INCOMPATIBLE,
                    PackState.DOWNLOADING,
                )
            ):
                caption = retry or {
                    "Paused": "Resume download",
                    "Downloaded": "Add to library",
                    "Update available": "Update pack",
                }.get(item.state, "Download")
                add = action(caption, partial(self.change, item, False), "secondary", "download")
                controls.addWidget(add)
                self.buttons.append(add)
            action_body.addLayout(controls)
            panel.body.addWidget(actions)
            layout.addWidget(panel)
            self.cards[key] = panel
            panel.setObjectName(key)
        layout.addStretch()
        layout.activate()
        scroll.setValue(previous)

    def album(self, game: str) -> None:
        self.dirty = False
        self.page.refresh(
            lambda: self.page.albums.open_game(game, back=partial(self.open, online=False))
        )

    def change(self, item: Entry, remove: bool) -> None:
        if self.job:
            return
        p = self.page
        key = item.origin.id
        if remove and not p.confirm(
            "Remove pack",
            "Remove this pack from your library and downloads? Applied playlists keep their tracks. You can download it again if it is still available.",
        ):
            return
        panel = self.cards[key]
        panel.findChild(QWidget, "packActions").hide()
        progress = self.progress = ProgressBlock()
        p.back_button.setEnabled(False)
        panel.body.addWidget(progress)
        progress.begin("Removing…" if remove else "Downloading…")
        progress.cancel.setText("Pause download")
        progress.cancel.setVisible(not remove)
        self.enable(False)
        cache, version = self.cache, p.ctx.version

        def work(worker: WorkerContext) -> Changes:
            if remove:
                return remove_pack(cache, key)
            assert item.status is not None
            return asyncio.run(
                make_available(
                    cache,
                    item.status,
                    app_version=version,
                    progress=worker.progress,
                    phase=worker.progress,
                    cancel=worker.cancel,
                )
            )

        def report(value: str | Progress) -> None:
            if isinstance(value, Progress):
                total = value.total_bytes or item.status.pack.size_bytes
                progress.advance_to(value.bytes_done, total)
                progress.counts.setText(
                    f"{pack_size(value.bytes_done)} / {pack_size(total)}  ·  {pack_size(value.bytes_per_second)}/s"
                )
            else:
                progress.begin(value.capitalize() + "…")
                progress.cancel.setVisible(value != "installing")

        def success(changes: Changes) -> None:
            self.dirty = True
            message = "Pack removed." if remove else "Added to your library."
            if changes.retained:
                message += " Kept your edited files: " + ", ".join(changes.retained)
            self.outcomes[item.origin] = (message, "")
            if remove and item.status is None:
                p.ctx.notify(message)

        def finished() -> None:
            self.job = None
            self.progress = None
            p.back_button.setEnabled(True)
            self.load(online=False)

        job = self.job = p.ctx.tasks.start(work)
        progress.cancel.clicked.connect(job.cancel)
        progress.cancel.clicked.connect(lambda: progress.cancel.setEnabled(False))
        job.progress.connect(report)
        job.result.connect(success)
        job.error.connect(lambda error: self.outcomes.update({item.origin: failure(error)}))
        job.finished.connect(finished)
        p.scroll.ensureWidgetVisible(progress)
        if not remove:
            progress.cancel.setFocus(Qt.FocusReason.OtherFocusReason)
