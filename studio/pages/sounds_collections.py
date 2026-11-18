"""Background library and download operations for the Sounds page."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog

import sounds
from content.cache import PackCache
from content.catalogue import CatalogueSource, PackState, fetch_catalogue, local_catalogue
from content.download import DownloadError, DownloadFailure
from sounds.archive import SET_SUFFIX, SavedSet
from studio.pages.sounds_library_views import DownloadCard, DownloadsView, LibraryView
from studio.pages.sounds_model import (
    EVENT_COUNT,
    Downloadable,
    OnlineSets,
    catalogue_message,
    coverage,
    get_pack,
    import_message,
    library_for,
    online_sets,
    saved_sources,
)
from studio.pages.sounds_views import NameView, add_section, button_row, empty_layout
from studio.tasks import Job, WorkerContext
from studio.widgets import Button

if TYPE_CHECKING:
    from studio.pages.sounds import SoundsPage

SET_FILTER = f"Sound sets (*{SET_SUFFIX})"


class SoundCollections:
    def __init__(self, page: "SoundsPage") -> None:
        self.page = page
        self.library = LibraryView()
        self.downloads = DownloadsView()
        self.name = NameView()
        self.saved: tuple[SavedSet, ...] | None = None
        self.online: OnlineSets | None = None
        self.local_job: Job | None = None
        self.catalogue_job: Job | None = None
        self.download_job: Job | None = None
        self.fetched = False
        self.edit_set: sounds.SoundSet | None = None
        self.rename_id: str | None = None
        self.name_action = ""
        self.library.import_button.clicked.connect(self.import_set)
        self.downloads.refresh.clicked.connect(self.refresh_catalogue)
        self.downloads.browse_local.clicked.connect(self.page.escape)
        self.name.submit.clicked.connect(self.save_name)
        self.pack_cards: dict[str, DownloadCard] = {}

    def update_library(self, rows: tuple[SavedSet, ...]) -> None:
        self.page.stop_audio()
        self.saved = rows
        empty_layout(self.library.cards)
        if not rows:
            self.library.hint.show_message(
                "Save a mix or import a sound set to start your collection."
            )
        elif any(row.error for row in rows):
            self.library.hint.show_message(
                "A saved set could not be read. Delete it or import a fresh copy."
            )
        else:
            self.library.hint.hide()
        cards = []
        for source in saved_sources(rows):
            card = self.page.set_card(source)
            actions = [
                Button("Rename", "quiet"),
                Button("Duplicate", "quiet"),
                Button("Export", "quiet"),
                Button("Delete", "quiet"),
            ]
            callbacks = (
                partial(self.open_name, "Rename set", source.sound_set, source.key),
                partial(self.open_name, "Duplicate set", source.sound_set),
                partial(self.export, source.sound_set),
                partial(self.delete, source.key, source.sound_set.name),
            )
            for button, callback in zip(actions, callbacks, strict=True):
                button.clicked.connect(callback)
            card.body.addLayout(button_row(*actions))
            cards.append(card)
        for row in rows:
            if row.error:
                remove = Button("Delete unreadable set", "secondary")
                remove.clicked.connect(partial(self.delete, row.id, "this unreadable set"))
                cards.append(remove)
        add_section(self.library.cards, cards)
        self.page.sources_changed()

    def open_library(self) -> None:
        if self.saved is None and self.local_job is None:
            self.local_work("Reading saved sets…", lambda library: None, "")
        self.page.open_view("saved", self.library.import_button)

    def local_work(
        self,
        pending: str,
        operation: Callable[[sounds.SoundLibrary], object],
        success: str,
        explain: Callable[[BaseException], str] | None = None,
        completed: Callable[[], None] | None = None,
    ) -> None:
        if self.local_job:
            return
        cfg = self.page.ctx.cfg
        config_path, explicit = cfg.config_path, bool(cfg.cli.config)

        def work(_worker: WorkerContext) -> tuple[SavedSet, ...]:
            library = library_for(config_path, explicit)
            operation(library)
            return library.list()

        def show(rows: tuple[SavedSet, ...]) -> None:
            self.update_library(rows)
            if completed:
                completed()
            self.page.feedback.show_message(success)

        def error(exc: BaseException) -> None:
            hint = explain(exc) if explain else f"Could not complete that set action. {exc}"
            self.page.feedback.show_message(hint)

        def finish() -> None:
            self.local_job = None
            self.page.update_controls()

        self.page.feedback.show_message(pending, pending=True)
        self.local_job = job = self.page.ctx.tasks.start(work)
        job.result.connect(show)
        job.error.connect(error)
        job.finished.connect(finish)
        self.page.update_controls()

    def open_name(self, action: str, sound_set: sounds.SoundSet, set_id: str | None = None) -> None:
        if self.local_job:
            return
        self.page.name_parent = self.page.state
        self.name_action, self.edit_set, self.rename_id = action, sound_set, set_id
        self.name.heading.setText(action)
        self.name.submit.setText(action)
        hints = {
            "Save mix": "Give this mix a name so you can find it again.",
            "Duplicate set": "Give the copy a name. The original set stays in your library.",
            "Rename set": "Choose a new name for this saved set.",
        }
        self.name.description.setText(hints[action])
        playable, silent = coverage(sound_set)
        chants = len(sound_set.assignments) - playable - silent
        summary = f"{playable} of {EVENT_COUNT} duel events have sound"
        if silent:
            summary += f" · {silent} silent"
        if chants:
            summary += f" · {chants} card chant choices"
        self.name.summary.setText(summary + ".")
        name = sound_set.name + " copy" if action == "Duplicate set" else sound_set.name
        self.name.name.setText(name[:120] if action != "Save mix" else "")
        self.page.clear_feedback()
        self.page.open_view("name", self.name.name)
        self.name.name.selectAll()

    def save_name(self) -> None:
        if self.edit_set is None:
            return
        name = self.name.name.text().strip()
        if not name:
            self.page.feedback.show_message("Enter a name for this set.")
            self.name.name.setFocus()
            return
        updated, set_id = replace(self.edit_set, name=name), self.rename_id

        def open_saved() -> None:
            if self.page.state == "name":
                self.open_library()

        self.local_work(
            "Saving set…",
            lambda library: library.save(updated, set_id=set_id),
            f"Saved “{name}” to your sets.",
            completed=open_saved,
        )

    def import_set(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.page, "Import a sound set", "", SET_FILTER)
        if path:
            self.local_work(
                "Checking the sound set…",
                lambda library: library.import_set(Path(path)),
                "Set imported. You can play or use it from Saved sets.",
                partial(import_message, clip=False),
            )

    def export(self, sound_set: sounds.SoundSet) -> None:
        if self.local_job:
            return
        path, _ = QFileDialog.getSaveFileName(
            self.page, "Export a sound set", "Sound set" + SET_SUFFIX, SET_FILTER
        )
        if path:
            target = Path(path if path.lower().endswith(SET_SUFFIX) else path + SET_SUFFIX)

            # The native picker confirms the path it returns. Adding a missing suffix can
            # produce a different existing file, so let the worker check that path first.
            def write(_library: sounds.SoundLibrary) -> None:
                if target != Path(path) and target.exists():
                    raise ValueError("That name is taken. Choose another file name.")
                sounds.export_set(sound_set, target)

            self.local_work(
                "Exporting set…", write, "Set exported. Share that file with another player."
            )

    def delete(self, set_id: str, name: str) -> None:
        if self.local_job or not self.page.ctx.confirm(
            "Delete saved set?",
            f"Delete “{name}” from your saved sets? You can import an exported copy again.",
        ):
            return
        self.local_work(
            "Deleting set…",
            lambda library: library.delete(set_id),
            "Set deleted from your library.",
        )

    def load_catalogue(self, *, network: bool) -> None:
        if self.catalogue_job or self.download_job:
            return
        cfg, version = self.page.ctx.cfg, self.page.ctx.version
        url = cfg.content_manifest_url
        if network:
            self.downloads.empty.hide()
            self.downloads.browse_local.hide()
            self.downloads.hint.show_message("Checking for downloadable sets…")
            self.fetched = True

        def work(_worker: WorkerContext) -> OnlineSets:
            cache = PackCache.for_config(cfg)
            catalogue = (
                asyncio.run(fetch_catalogue(cache, config_url=url))
                if network
                else local_catalogue(cache, config_url=url)
            )
            return online_sets(cache, version, catalogue)

        def error(_exc: BaseException) -> None:
            self.downloads.hint.show_message("Could not load the catalogue. Try refreshing it.")

        def finish() -> None:
            self.catalogue_job = None
            self.downloads.refresh.setEnabled(True)
            if not network and self.page.state == "downloads" and not self.fetched:
                self.refresh_catalogue()

        self.catalogue_job = job = self.page.ctx.tasks.start(work)
        self.downloads.refresh.setEnabled(False)
        job.result.connect(self.show_online)
        job.error.connect(error)
        job.finished.connect(finish)

    def refresh_catalogue(self) -> None:
        self.load_catalogue(network=True)

    def open_downloads(self) -> None:
        self.page.open_view("downloads", self.downloads.refresh)
        if not self.fetched:
            self.refresh_catalogue()

    def show_online(self, online: OnlineSets) -> None:
        self.page.stop_audio()
        self.online = online
        message = catalogue_message(online)
        self.downloads.hint.show_message(message if online.packs else "")
        self.downloads.empty.headline.setText(
            "No sets available yet"
            if online.catalogue.source is CatalogueSource.LIVE
            else "Catalogue offline"
        )
        self.downloads.empty.description.setText("Built-in and saved sounds are still available.")
        self.downloads.empty.setVisible(not online.packs)
        self.downloads.browse_local.setVisible(not online.packs)
        empty_layout(self.downloads.cards)
        self.pack_cards = {}
        for item in online.packs:
            card = self.pack_cards[item.pack.id] = DownloadCard(item, partial(self.download, item))
            card.progress.cancel.clicked.connect(self.cancel_download)
            if item.sound_set:
                self.add_download_controls(card, item)
        add_section(self.downloads.cards, self.pack_cards.values())
        self.page.sources_changed()

    def add_download_controls(self, card: DownloadCard, item: Downloadable) -> None:
        sound_set = item.sound_set
        play = Button("Play sampler")
        play.setEnabled(any(sound_set.assignments.values()))
        play.clicked.connect(partial(self.page.audio.play, play, sound_set))
        use = Button("Use set", "quiet")
        use.clicked.connect(partial(self.page.use_download, item))
        card.buttons.addWidget(play)
        card.buttons.addWidget(use)
        playable, silent = coverage(sound_set)
        count = f"{playable} of {EVENT_COUNT} events with sound"
        if silent:
            count += f"  ·  {silent} silent"
        if item.status.state is PackState.UPDATE_AVAILABLE:
            count = "Cached version  ·  " + count
        card.status.setText(card.status.text() + "  ·  " + count)

    def download(self, item: Downloadable) -> None:
        if self.download_job or self.catalogue_job:
            return
        cfg, version = self.page.ctx.cfg, self.page.ctx.version
        card = self.pack_cards[item.pack.id]
        card.begin()
        for item_card in self.pack_cards.values():
            item_card.download.setEnabled(False)
        self.downloads.refresh.setEnabled(False)

        def work(worker: WorkerContext) -> Downloadable:
            return asyncio.run(
                get_pack(
                    PackCache.for_config(cfg), item.pack, version, worker.progress, worker.cancel
                )
            )

        def result(downloaded: Downloadable) -> None:
            online = self.online
            self.show_online(
                replace(
                    online,
                    packs=tuple(
                        downloaded if row.pack.id == item.pack.id else row for row in online.packs
                    ),
                )
            )

        def error(exc: BaseException) -> None:
            if isinstance(exc, DownloadError) and exc.reason == DownloadFailure.CANCELLED:
                card.interrupted("Download paused. You can resume it here.", "Resume download")
            else:
                hints = {
                    DownloadFailure.DISK_SPACE: "There isn’t enough disk space. Free some space and retry.",
                    DownloadFailure.INTEGRITY: "The file didn’t pass verification. Try downloading it again.",
                    DownloadFailure.NOT_FOUND: "This set is no longer available. Refresh the catalogue.",
                }
                hint = hints.get(
                    getattr(exc, "reason", None), "Could not download this set. Please retry."
                )
                if isinstance(exc, ValueError):
                    hint = "The downloaded file could not be opened as a sound set."
                card.interrupted(hint, "Retry download")

        def finish() -> None:
            self.download_job = None
            self.downloads.refresh.setEnabled(True)
            for item_card in self.pack_cards.values():
                item_card.download.setEnabled(True)

        self.download_job = job = self.page.ctx.tasks.start(work)
        job.progress.connect(card.advance)
        job.result.connect(result)
        job.error.connect(error)
        job.finished.connect(finish)
        card.progress.cancel.setFocus()

    def cancel_download(self) -> None:
        if self.download_job:
            self.download_job.cancel()
            for card in self.pack_cards.values():
                if card.progress.isVisible():
                    card.progress.cancel.setEnabled(False)
                    card.progress.phase.setText("Pausing download…")
