"""Duel sound sets, event mixing and reversible changes to the selected game."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

import sounds
from studio import theme as t
from studio.context import AppContext, GameOpenError, Installation
from studio.pages.base import Page
from studio.pages.card_views import scroll
from studio.pages.sounds_auditions import SoundAuditions
from studio.pages.sounds_collections import SoundCollections
from studio.pages.sounds_event_controls import SoundEvents
from studio.pages.sounds_model import (
    EVENT_COUNT,
    NEW_MIX,
    Downloadable,
    FilesChanged,
    Snapshot,
    Source,
    coverage,
    installed_name,
    saved_sources,
    scan,
    write_game,
)
from studio.pages.sounds_views import (
    ActionBar,
    Feedback,
    Overview,
    SetCard,
    add_section,
    empty_layout,
)
from studio.tasks import Job
from studio.widgets import GameClosedBlock, margins


class SoundsPage(Page):
    id, title, icon = "sounds", "Sounds", "sounds"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.overview = Overview()
        self.events = SoundEvents(self)
        self.mixer, self.chants, self.picker = (
            self.events.mixer,
            self.events.chants,
            self.events.picker,
        )
        self.bar = ActionBar()
        self.feedback = Feedback()
        self.closed = GameClosedBlock()
        self.closed.hide()
        for bordered in (self.bar, self.feedback, self.closed):
            bordered.setMaximumWidth(t.CONTENT_MAX - 2 * t.XXL)
        self.collections = SoundCollections(self)
        self.state = "overview"
        self.name_parent = "overview"
        self.snapshot: Snapshot | None = None
        self.scan_job: Job | None = None
        self.scan_error = False
        self.generation = 0
        self.bundle_job: Job | None = None
        self.bundled: tuple[sounds.SoundSet, ...] | None = None
        self.draft = sounds.SoundSet(NEW_MIX)
        self.choice_names: dict[str, str] = {}
        self.selected_source = ""
        self.write_job: Job | None = None
        self.retry_action = ""
        self.audio = SoundAuditions(self)

        self.stack = QStackedWidget()
        widgets = {
            "overview": self.overview,
            "mix": self.mixer,
            "chants": self.chants,
            "event": self.picker,
            "saved": self.collections.library,
            "downloads": self.collections.downloads,
            "name": self.collections.name,
        }
        self.views = {key: scroll(widget) for key, widget in widgets.items()}
        self.mixer.scroller, self.chants.scroller = self.views["mix"], self.views["chants"]
        for key, view in self.views.items():
            self.stack.addWidget(view)
            if key != "overview":
                widgets[key].back.clicked.connect(self.escape)
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(0)
        self.messages = QHBoxLayout()
        self.messages.setContentsMargins(t.XXL, 0, t.XXL, 0)
        self.messages.addStretch()
        notices = QVBoxLayout()
        notices.setSpacing(t.XS)
        notices.addWidget(self.feedback)
        notices.addWidget(self.closed)
        self.messages.addLayout(notices, 1)
        self.messages.addStretch()
        body.addLayout(self.messages)
        body.addWidget(self.stack, 1)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(t.XXL, t.XS, t.XXL, t.LG)
        self.footer.addStretch()
        self.footer.addWidget(self.bar, 1)
        self.footer.addStretch()
        body.addLayout(self.footer)
        self.animation = QPropertyAnimation(self)
        self.animation.setPropertyName(b"pos")
        self.animation.setDuration(t.MOTION_MS)
        self.cards: dict[str, SetCard] = {}

        self.overview.choose.clicked.connect(context.installations_requested.emit)
        self.overview.rescan.clicked.connect(self.refresh)
        self.overview.toggle.clicked.connect(self.toggle_effects)
        self.overview.mix.clicked.connect(self.open_mixer)
        self.overview.saved.clicked.connect(self.collections.open_library)
        self.overview.downloads.clicked.connect(self.collections.open_downloads)
        self.overview.chants.clicked.connect(self.events.open_chants)
        self.mixer.chants.clicked.connect(self.events.open_chants)
        self.bar.save.clicked.connect(self.save_mix)
        for button, action in (
            (self.bar.apply, "apply"),
            (self.bar.undo, "undo"),
            (self.bar.restore, "restore"),
        ):
            button.clicked.connect(partial(self.change_game, action))
        self.closed.retry.clicked.connect(self.retry)
        self.feedback.dismiss.clicked.connect(self.clear_feedback)
        self.update_controls()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        extra = int(event.size().width() > t.CONTENT_MAX)
        for row in (self.footer, self.messages):
            row.setStretch(0, extra)
            row.setStretch(1, 100 if extra else 1)
            row.setStretch(2, extra)
        self.place_bar()
        self.size_overview()

    def place_bar(self) -> None:
        inline = self.state == "overview" and self.height() > t.TALL_HEIGHT + t.HERO_MIN
        if inline and self.bar.parentWidget() is not self.overview:
            self.overview.wide_actions.addWidget(self.bar)
        elif not inline and self.bar.parentWidget() is self.overview:
            self.footer.insertWidget(1, self.bar, 1)
        self.bar.setVisible(self.state in ("overview", "mix", "chants"))

    def size_overview(self) -> None:
        compact = self.width() < t.COMPACT_WIDTH - t.RAIL_COMPACT
        if self.overview.compact_tools != compact:
            self.overview.place_tools(compact)
        columns = 1 if compact else max(1, len(self.cards))
        if self.overview.columns != columns:
            self.overview.columns = columns
            add_section(self.overview.cards, self.cards.values(), columns)
            for col in range(3):
                self.overview.cards.setColumnStretch(col, int(col < columns))
        self.overview.compact_banner = self.height() < t.TALL_HEIGHT - t.HUGE and (
            self.write_job or self.feedback.isVisible() or (self.snapshot and self.snapshot.undo)
        )
        self.overview.size_banner()

    def shown(self) -> None:
        if self.bundled is None and self.bundle_job is None:
            self.bundle_job = job = self.ctx.tasks.start(lambda _worker: sounds.bundled_sets())
            job.result.connect(self.show_bundles)
            job.error.connect(
                lambda exc: self.feedback.show_message(f"Could not open built-in sounds. {exc}")
            )
            job.finished.connect(self.finish_bundles)
        if self.collections.saved is None and self.collections.local_job is None:
            self.collections.local_work("Reading saved sets…", lambda library: None, "")
        if self.collections.online is None:
            self.collections.load_catalogue(network=False)
        self.refresh()
        focus = self.overview.choose if self.overview.choose.isVisible() else self.overview.mix
        if self.state == "overview":
            focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def hidden(self) -> bool:
        self.stop_audio()
        return self.write_job is None

    def request_close(self) -> bool:
        if not self.hidden():
            return False
        self.audio.player.close()
        return True

    def installation_changed(self, installation: Installation) -> None:
        self.generation += 1
        if self.scan_job:
            self.scan_job.cancel()
        self.scan_job = None
        self.snapshot = None
        self.scan_error = False
        self.draft = sounds.SoundSet(NEW_MIX)
        self.selected_source = ""
        self.choice_names.clear()
        self.events.reset()
        self.stop_audio()
        self.clear_feedback()
        self.open_view("overview")
        self.views["overview"].verticalScrollBar().setValue(0)
        self.show_cards()
        if self.isVisible() and installation.ready:
            self.refresh()

    def refresh(self) -> None:
        install = self.ctx.installation
        if not install.ready or self.scan_job or self.write_job:
            return
        game, config_path, generation = (
            Path(install.path),
            self.ctx.cfg.config_path,
            self.generation,
        )
        self.scan_error = False

        def show(snapshot: Snapshot) -> None:
            if generation == self.generation:
                self.snapshot = snapshot
                self.show_cards()
                self.sources_changed()

        def error(exc: BaseException) -> None:
            if generation == self.generation:
                self.scan_error = True
                self.snapshot = None
                self.show_cards()
                self.feedback.show_message(
                    "Could not read the game’s sounds. Check its folder and retry."
                )
                self.feedback.copy.setToolTip(str(exc))

        def finish() -> None:
            if generation == self.generation:
                self.scan_job = None
                self.update_controls()

        self.scan_job = job = self.ctx.tasks.start(lambda _worker: scan(game, config_path))
        job.result.connect(show)
        job.error.connect(error)
        job.finished.connect(finish)
        self.update_controls()

    def open_view(self, key: str, focus: QWidget | None = None) -> None:
        if key != self.state:
            self.stop_audio()
            self.animation.stop()
            view = self.views[key]
            self.stack.setCurrentWidget(view)
            self.state = key
            destination = QPoint()
            self.animation.setTargetObject(view)
            self.animation.setStartValue(destination + QPoint(t.LG, 0))
            self.animation.setEndValue(destination)
            self.animation.start()
        self.update_controls()
        if focus and focus.isEnabled():
            focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def escape(self) -> bool:
        if self.write_job:
            return True
        if self.closed.isVisible():
            self.clear_feedback()
            return True
        if self.state == "event":
            if self.events.clip_job:
                self.events.clip_job.cancel()
                self.events.clip_job = None
                self.clear_feedback()
            self.events.return_to_event()
        elif self.state == "name":
            self.open_view(self.name_parent)
        elif self.state == "chants":
            self.events.close_chants()
        elif self.state != "overview":
            focus = {
                "mix": self.overview.mix,
                "saved": self.overview.saved,
                "downloads": self.overview.downloads,
            }
            self.open_view("overview", focus.get(self.state))
        elif self.audio.preview is not None:
            self.stop_audio()
        else:
            return False
        return True

    def clear_feedback(self) -> None:
        self.retry_action = ""
        self.closed.hide()
        self.feedback.show_message("")

    def retry(self) -> None:
        if self.retry_action:
            self.change_game(self.retry_action, retry=True)

    def show_bundles(self, sets: tuple[sounds.SoundSet, ...]) -> None:
        self.bundled = sets
        self.show_cards()
        self.sources_changed()

    def finish_bundles(self) -> None:
        self.bundle_job = None

    def sources(self) -> tuple[Source, ...]:
        entries: list[Source] = []
        if self.snapshot:
            entries.append(Source("installed", self.snapshot.sound_set, "Installed"))
        entries.extend(
            Source(f"builtin:{item.name}", item, "Built in") for item in self.bundled or ()
        )
        entries.extend(saved_sources(self.collections.saved or ()))
        if self.collections.online:
            entries.extend(
                Source(f"pack:{item.pack.id}", item.sound_set, "Downloaded")
                for item in self.collections.online.packs
                if item.sound_set
            )
        return tuple(entries)

    def set_card(self, source: Source) -> SetCard:
        use = partial(self.use_set, source) if source.category != "Installed" else None
        card = SetCard(source, use)
        card.play.clicked.connect(partial(self.audio.play, card.play, source.sound_set))
        return card

    def show_cards(self) -> None:
        self.stop_audio()
        empty_layout(self.overview.cards)
        self.cards = {
            source.key: self.set_card(source)
            for source in self.sources()
            if source.category in ("Installed", "Built in")
        }
        self.overview.columns = 0
        self.update_controls()

    def use_set(self, source: Source) -> None:
        if self.write_job:
            return
        self.clear_feedback()
        self.draft = source.sound_set
        self.selected_source = source.key
        self.choice_names.clear()
        if self.state in ("saved", "downloads"):
            self.open_view("overview", self.bar.apply)
        self.sources_changed()

    def use_download(self, item: Downloadable) -> None:
        if item.sound_set:
            self.use_set(Source(f"pack:{item.pack.id}", item.sound_set, "Downloaded"))

    def sources_changed(self) -> None:
        self.events.update()
        self.update_controls()

    def open_mixer(self) -> None:
        self.open_view("mix", self.mixer.back)
        self.events.update()

    def save_mix(self) -> None:
        self.collections.open_name("Save mix", self.draft)

    def stop_audio(self) -> None:
        self.audio.stop()

    def update_status(self) -> None:
        snapshot = self.snapshot
        title = installed_name(snapshot, self.sources()) if snapshot else "Choose an installation"
        self.overview.show_status(self.ctx.installation, snapshot, title, self.scan_error)
        count = (
            sum(row.key.startswith("chant/") and bool(row.paths) for row in snapshot.rows)
            if snapshot
            else 0
        )
        self.overview.chants.setText(f"Card chants · {count}" if snapshot else "Card chants")
        self.chants.status.setText(
            (f"{count} installed." if count else "No card chants are installed.")
            if snapshot
            else "Choose an installation to check for card chants."
        )
        self.chants.status.show()

    def update_controls(self) -> None:
        self.update_status()
        applying = self.write_job is not None
        editing = not (applying or self.events.clip_job or self.collections.local_job)
        can_write = (
            editing and self.snapshot is not None and not self.scan_job and not self.ctx.busy
        )
        self.place_bar()
        self.stack.setEnabled(not applying)
        self.bar.apply.setEnabled(can_write and bool(self.draft.assignments))
        self.bar.save.setEnabled(editing and bool(self.draft.assignments))
        self.overview.toggle.setEnabled(can_write)
        self.overview.rescan.setEnabled(not self.scan_job)
        self.picker.setEnabled(not self.events.clip_job)
        self.collections.library.setEnabled(not self.collections.local_job)
        self.collections.name.setEnabled(not self.collections.local_job)
        self.bar.undo.setEnabled(can_write and self.snapshot.undo)
        self.bar.restore.setEnabled(can_write and self.snapshot.restore)
        has_history = bool(self.snapshot and (self.snapshot.undo or self.snapshot.restore))
        self.bar.history.setVisible(has_history)
        self.bar.undo.setVisible(bool(self.snapshot and self.snapshot.undo))
        self.bar.undo_help.setVisible(self.bar.undo.isVisibleTo(self.bar.history))
        self.bar.restore.setVisible(bool(self.snapshot and self.snapshot.restore))
        self.bar.restore_help.setVisible(self.bar.restore.isVisibleTo(self.bar.history))
        self.bar.name.set_value(self.draft.name if self.draft.assignments else "No set selected")
        playable, silent = coverage(self.draft)
        detail = (
            f"{playable} of {EVENT_COUNT} events with sound"
            + (f"  ·  {silent} silent" if silent else "")
            if self.draft.assignments
            else "Pick a set or open the mixer to choose individual sounds."
        )
        chants = len(self.draft.assignments) - playable - silent
        if chants:
            detail += f"  ·  {chants} chant choices"
        self.bar.detail.setText(detail)
        self.bar.restart.setVisible(bool(self.draft.assignments and self.snapshot))
        for key, card in self.cards.items():
            if card.use is not None:
                card.select(key == self.selected_source)
        self.size_overview()

    def toggle_effects(self) -> None:
        if self.snapshot:
            self.change_game("enable" if self.snapshot.settings.enabled is False else "disable")

    def change_game(self, action: str, *, retry: bool = False) -> None:
        snapshot = self.snapshot
        if not snapshot or self.write_job or self.scan_job or self.ctx.busy:
            return
        if (
            action == "restore"
            and not retry
            and not self.ctx.confirm(
                "Restore original sounds?",
                "Put back the files from before the first recorded change to each sound? You can undo the restore.",
            )
        ):
            return
        if action == "apply" and not self.draft.assignments:
            return
        game = Path(self.ctx.installation.path)
        config_path, draft = self.ctx.cfg.config_path, self.draft
        self.stop_audio()
        self.clear_feedback()
        self.feedback.show_message(
            "Updating the selected game. You can switch folders when this finishes.", pending=True
        )
        self.ctx.set_busy(True)

        def error(exc: BaseException) -> None:
            if isinstance(exc, GameOpenError) or "Close this EDOPro installation" in str(exc):
                self.retry_action = action
                self.feedback.hide()
                self.closed.show_reason(str(exc))
            elif isinstance(exc, FilesChanged):
                self.feedback.show_message(
                    "The game’s sounds changed outside the app. Your files were kept. Check the installed sounds before applying again."
                )
            elif "changed after installation" in str(exc):
                self.feedback.show_message(
                    f"A sound changed outside the app. {exc} Put back that sound’s previous file to use your saved sound history."
                )
            elif action in ("enable", "disable"):
                self.feedback.show_message(
                    "Could not change the effects switch. Use EDOPro’s audio settings to change it."
                )
            else:
                self.feedback.show_message(f"Could not update the game’s sounds. {exc}")

        def finish() -> None:
            self.write_job = None
            self.ctx.set_busy(False)
            if not self.retry_action:
                self.refresh()
            self.update_controls()

        self.write_job = job = self.ctx.tasks.start(
            lambda _worker: write_game(game, config_path, action, draft, snapshot)
        )
        job.result.connect(
            lambda _: self.feedback.show_message(
                {
                    "apply": "Sounds applied.",
                    "undo": "Your previous sounds are back.",
                    "restore": "Original sounds restored.",
                    "enable": "Sound effects enabled.",
                    "disable": "Sound effects turned off.",
                }[action]
                + " You’ll hear the change next time you start EDOPro."
            )
        )
        job.error.connect(error)
        job.finished.connect(finish)
        self.update_controls()
