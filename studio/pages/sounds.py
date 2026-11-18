"""Duel sound sets, event mixing and reversible changes to the selected game."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QWidget

import sounds
from music.settings import GameProcessUnknown
from studio import theme as t
from studio.context import AppContext, GameOpenError, Installation
from studio.pages.music_sounds_controls import AudioGameCheck, confirm_change
from studio.pages.sounds_auditions import SoundAuditions
from studio.pages.sounds_collections import SoundCollections
from studio.pages.sounds_event_controls import SoundEvents
from studio.pages.sounds_layout import SoundWorkspace
from studio.pages.sounds_model import (
    EVENT_COUNT,
    NEW_MIX,
    InstallationUnavailable,
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
    empty_layout,
)
from studio.tasks import Job


class SoundsPage(SoundWorkspace):
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
        self.closed = AudioGameCheck()
        self.closed.back.clicked.connect(self.clear_feedback)
        self.closed.proceed.clicked.connect(lambda: self.retry(confirmed_closed=True))
        self.closed.hide()
        for bordered in (self.bar, self.feedback, self.closed):
            bordered.setMaximumWidth(t.CONTENT_MAX - 2 * t.XXL)
        self.collections = SoundCollections(self)
        self.state = "overview"
        self.name_parent = "overview"
        self.install_path = context.installation.path
        self.snapshot: Snapshot | None = None
        self.scan_job: Job | None = None
        self.scan_error = ""
        self.generation = 0
        self.bundle_job: Job | None = None
        self.bundled: tuple[sounds.SoundSet, ...] | None = None
        self.draft = sounds.SoundSet(NEW_MIX)
        self.choice_names: dict[str, str] = {}
        self.selected_source = ""
        self.applied: sounds.SoundSet | None = None
        self.write_job: Job | None = None
        self.retry_action = ""
        self.audio = SoundAuditions(self)

        self.build_layout()
        self.animation = QPropertyAnimation(self)
        self.animation.setPropertyName(b"pos")
        self.animation.setDuration(t.MOTION_MS)
        self.cards: dict[str, SetCard] = {}

        self.overview.choose.clicked.connect(context.installations_requested.emit)
        self.overview.rescan.clicked.connect(self.check_again)
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
        self.closed.retry.clicked.connect(lambda: self.retry())
        self.feedback.dismiss.clicked.connect(self.clear_feedback)
        self.update_controls()

    def shown(self) -> None:
        if self.bundled is None and self.bundle_job is None:
            self.bundle_job = job = self.ctx.tasks.start(lambda _worker: sounds.bundled_sets())
            job.result.connect(self.show_bundles)
            job.error.connect(
                lambda exc: self.feedback.show_message(
                    "Could not read the built-in sound sets. Reopen Sounds to retry.",
                    details=str(exc),
                )
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
        self.applied = None
        self.scan_error = ""
        if self.install_path != installation.path:
            self.draft = sounds.SoundSet(NEW_MIX)
            self.selected_source = ""
            self.choice_names.clear()
            self.events.reset()
        self.install_path = installation.path
        self.stop_audio()
        self.clear_feedback()
        self.open_view("overview")
        self.views["overview"].verticalScrollBar().setValue(0)
        self.show_cards()
        if self.isVisible() and installation.ready:
            self.refresh()

    def check_again(self) -> None:
        install = self.ctx.installation
        if install.path and not install.ready and self.scan_error:
            if self.scan_job:
                return
            generation = self.generation

            def checked(result: Installation) -> None:
                if self.generation == generation and result.path:
                    self.ctx.set_installation(result)

            def finished() -> None:
                if self.generation == generation:
                    self.scan_job = None
                    self.update_controls()

            self.scan_job = job = self.ctx.tasks.start(lambda _: Installation.inspect(install.path))
            job.result.connect(checked)
            job.finished.connect(finished)
            self.update_controls()
        elif install.path and not install.ready:
            self.ctx.select_installation_requested.emit(install.path)
        else:
            self.refresh()

    def confirm(self, action: str, message: str) -> bool:
        return confirm_change(self, action, message)

    def refresh(self) -> None:
        install = self.ctx.installation
        if not install.ready or self.scan_job or self.write_job:
            return
        game, config_path, generation = (
            Path(install.path),
            self.ctx.cfg.config_path,
            self.generation,
        )
        if self.retry_action:
            self.clear_feedback()
        self.scan_error = ""

        def show(snapshot: Snapshot) -> None:
            if generation == self.generation:
                self.snapshot = snapshot
                if self.applied and not snapshot.matches(self.applied):
                    self.applied = None
                self.show_cards()
                self.sources_changed()

        def error(exc: BaseException) -> None:
            if generation == self.generation:
                if isinstance(exc, InstallationUnavailable):
                    self.ctx.set_installation(Installation(install.path))
                self.scan_error = str(exc) or "Could not read the selected folder."
                self.snapshot = None
                self.show_cards()

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
            if self.retry_action:
                self.clear_feedback()
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
        return True

    def clear_feedback(self) -> None:
        self.retry_action = ""
        self.closed.hide()
        self.feedback.show_message("")
        self.update_controls()

    def retry(self, *, confirmed_closed: bool = False) -> None:
        if self.retry_action:
            self.change_game(self.retry_action, retry=True, confirmed_closed=confirmed_closed)

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
        if self.draft != source.sound_set:
            self.applied = None
        self.draft = source.sound_set
        self.selected_source = source.key
        self.choice_names.clear()
        if self.state in ("saved", "downloads"):
            self.open_view("overview", self.bar.apply)
        self.sources_changed()

    def sources_changed(self) -> None:
        self.events.update()
        self.update_controls()

    def open_mixer(self) -> None:
        self.open_view("mix", self.mixer.back)
        self.events.update()

    def save_mix(self) -> None:
        self.collections.open_name("Save sound set", self.draft)

    def stop_audio(self) -> None:
        self.audio.stop()

    def update_status(self) -> None:
        snapshot = self.snapshot
        title = (
            installed_name(snapshot, self.sources()) if snapshot else "Choose your EDOPro folder"
        )
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
            editing
            and self.snapshot is not None
            and not self.scan_job
            and not self.ctx.busy
            and not self.retry_action
        )
        self.place_bar()
        self.stack.setEnabled(not applying)
        applied = self.draft == self.applied
        self.bar.apply.setText("Applied" if applied else "Apply sound effects")
        self.bar.apply.setEnabled(can_write and bool(self.draft.assignments) and not applied)
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
            f"{playable} of {EVENT_COUNT} sound slots have a clip"
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

    def change_game(
        self, action: str, *, retry: bool = False, confirmed_closed: bool = False
    ) -> None:
        snapshot = self.snapshot
        if (
            not snapshot
            or self.write_job
            or self.scan_job
            or self.ctx.busy
            or (self.retry_action and not retry)
        ):
            return
        if (
            action == "undo"
            and not retry
            and not self.confirm(
                "Undo last sound change",
                f"Put the previous sound files back in {self.ctx.installation.path}?",
            )
        ):
            return
        if (
            action == "restore"
            and not retry
            and not self.confirm(
                "Restore original sounds",
                "Put back the files from before the first recorded change to each sound in this EDOPro folder? You can undo the restore.",
            )
        ):
            return
        if action == "apply" and (not self.draft.assignments or self.draft == self.applied):
            return
        game = Path(self.ctx.installation.path)
        generation = self.generation
        config_path, draft = self.ctx.cfg.config_path, self.draft
        self.stop_audio()
        self.clear_feedback()
        self.feedback.show_message(
            "Updating sound effects… You can change folders when this finishes.", pending=True
        )
        self.ctx.set_busy(True)

        def error(exc: BaseException) -> None:
            if generation != self.generation:
                return
            if isinstance(
                exc, (GameProcessUnknown, GameOpenError)
            ) or "Close this EDOPro installation" in str(exc):
                self.retry_action = action
                self.feedback.hide()
                self.closed.explain(exc)
                self.closed.retry.setFocus()
            else:
                self.feedback.write_failed(action, exc)

        def finish() -> None:
            self.write_job = None
            self.ctx.set_busy(False)
            if not self.retry_action:
                self.refresh()
            self.update_controls()

        self.write_job = job = self.ctx.tasks.start(
            lambda _worker: write_game(
                game, config_path, action, draft, snapshot, confirmed_closed=confirmed_closed
            )
        )

        def done(_: object) -> None:
            if generation == self.generation:
                if action in ("apply", "undo", "restore"):
                    self.applied = draft if action == "apply" else None
                result = {
                    "apply": "Sounds applied.",
                    "undo": "Your previous sounds are back.",
                    "restore": "Original sounds restored.",
                    "enable": "Sound effects enabled.",
                    "disable": "Sound effects turned off.",
                }[action]
                self.feedback.show_message(result + " Restart EDOPro to load the changes.")

        job.result.connect(done)
        job.error.connect(error)
        job.finished.connect(finish)
        self.update_controls()
