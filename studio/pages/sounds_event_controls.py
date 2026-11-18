"""Edit one duel event or card chant at a time."""

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog

import sounds
from studio.pages.sounds_mixer import MixerView, PickerView
from studio.pages.sounds_model import EVENT_KEYS, NEW_MIX, chants_for, choice_for, import_message
from studio.tasks import Job
from studio.widgets import label

if TYPE_CHECKING:
    from studio.pages.sounds import SoundsPage


class SoundEvents:
    def __init__(self, page: "SoundsPage") -> None:
        self.page = page
        self.mixer = MixerView()
        self.chants = MixerView(chants=True)
        self.picker = PickerView()
        self.event_view = self.mixer
        self.event = ""
        self.chants_parent = "overview"
        self.extra_chants: set[str] = set()
        self.clip_job: Job | None = None
        self.mixer.build(EVENT_KEYS, self.play_event, self.open_event)
        for kind, button in self.chants.forms.items():
            button.clicked.connect(lambda _checked=False, family=kind: self.add_chant(family))
        self.picker.keep.clicked.connect(self.keep)
        self.picker.silence.clicked.connect(lambda: self.set_event(None, "Silent"))
        self.picker.import_clip.clicked.connect(self.import_clip)

    def reset(self) -> None:
        self.extra_chants.clear()
        if self.clip_job:
            self.clip_job.cancel()
            self.clip_job = None

    def show_choice(self, key: str) -> tuple[str, float | None, sounds.Clip | None]:
        return choice_for(key, self.page.draft, self.page.snapshot, self.page.choice_names)

    def update(self) -> None:
        keys = set(chants_for(self.page.draft, self.page.sources()))
        if self.page.snapshot:
            keys.update(row.key for row in self.page.snapshot.rows if row.key.startswith("chant/"))
        keys = tuple(sorted(keys | self.extra_chants))
        if keys != tuple(self.chants.rows):
            self.chants.build(keys, self.play_event, self.open_event)
        for view in (self.mixer, self.chants):
            for key, row in view.rows.items():
                name, duration, clip = self.show_choice(key)
                row.set_choice(name, duration, clip is not None)
        if self.page.state == "event" and not self.clip_job:
            self.fill_picker()

    def open_chants(self) -> None:
        self.chants_parent = self.page.state
        self.update()
        self.page.open_view("chants", self.chants.back)

    def close_chants(self) -> None:
        button = (
            self.page.overview.chants if self.chants_parent == "overview" else self.mixer.chants
        )
        self.page.open_view(self.chants_parent, button)

    def add_chant(self, kind: str) -> None:
        try:
            text = self.chants.number.text().strip()
            key = sounds.chant_key(kind, int(text)) if text.isascii() and text.isdecimal() else ""
            if not key:
                raise ValueError
        except ValueError:
            self.page.feedback.show_message("Enter the number printed at the bottom of the card.")
            self.chants.number.setFocus()
            return
        self.extra_chants.add(key)
        self.update()
        self.open_event(key)

    def open_event(self, key: str) -> None:
        if self.clip_job:
            return
        self.event_view = self.chants if self.page.state == "chants" else self.mixer
        self.event = key
        self.fill_picker()
        self.page.open_view("event", self.picker.back)

    def return_to_event(self) -> None:
        row = self.event_view.rows.get(self.event)
        key = "mix" if self.event_view is self.mixer else "chants"
        self.page.open_view(key, row.edit if row else self.event_view.back)
        if row:
            self.page.views[key].ensureWidgetVisible(row)

    def fill_picker(self) -> None:
        self.page.stop_audio()
        self.picker.clear_options()
        key = self.event
        event = sounds.event_for(key)
        self.picker.heading.setText(event.name)
        self.picker.description.setText(event.description)
        self.picker.description.show()
        name, duration, _ = self.show_choice(key)
        self.picker.current_name.set_value(
            name, f"{name}  ·  {duration:.2f} s" if duration else name
        )
        shown = False
        for source in self.page.sources():
            sound_set = source.sound_set
            if key not in sound_set.assignments:
                continue
            shown = True
            clip = sound_set.pick(key)
            name = sound_set.name
            button = self.picker.add_option(
                name,
                source.category,
                clip,
                lambda _checked=False, choice=clip, title=name: self.set_event(choice, title),
            )
            button.clicked.connect(
                lambda _checked=False, play=button, source_set=sound_set: self.page.audio.play(
                    play, source_set, key
                )
            )
        if not shown:
            self.picker.options.addWidget(
                label("Your sets have no sound for this event yet.", "muted", True)
            )

    def play_event(self, key: str) -> None:
        _, _, clip = self.show_choice(key)
        view = self.chants if key.startswith("chant/") else self.mixer
        if clip is not None:
            self.page.audio.play(view.rows[key].play, sounds.SoundSet(NEW_MIX, {key: clip}), key)

    def keep(self) -> None:
        self.commit(self.page.draft.without_assignment(self.event))
        self.page.choice_names.pop(self.event, None)
        self.page.sources_changed()
        self.return_to_event()

    def set_event(self, clip: sounds.Clip | None, name: str) -> None:
        self.commit(self.page.draft.with_assignment(self.event, clip))
        self.page.choice_names[self.event] = name
        self.page.sources_changed()
        self.return_to_event()

    def commit(self, updated: sounds.SoundSet) -> None:
        page = self.page
        page.stop_audio()
        page.clear_feedback()
        if updated.assignments != page.draft.assignments:
            if page.selected_source:
                for key, clip in page.draft.assignments.items():
                    if clip is not None:
                        page.choice_names.setdefault(key, page.draft.name)
                # Mixed copies must keep any credits inherited from the source set.
                updated = sounds.SoundSet(
                    NEW_MIX, {key: updated.pick(key) for key in updated.assignments}
                )
            else:
                updated = sounds.SoundSet(NEW_MIX, updated.assignments, updated.credits)
            page.draft = updated
            page.selected_source = ""

    def import_clip(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self.page,
            "Choose a short sound effect",
            "",
            "Audio files (*.wav *.mp3 *.ogg *.flac);;All files (*)",
        )
        if not name:
            return
        path, key, generation = Path(name), self.event, self.page.generation
        self.page.stop_audio()
        self.page.feedback.show_message("Checking the audio…", pending=True)

        def current() -> bool:
            return (
                job is self.clip_job
                and self.page.state == "event"
                and self.event == key
                and generation == self.page.generation
            )

        def show(clip: sounds.Clip) -> None:
            if current():
                self.set_event(clip, path.name)

        def error(exc: BaseException) -> None:
            if current():
                self.page.feedback.show_message(import_message(exc, clip=True))

        def finish() -> None:
            if self.clip_job is job:
                self.clip_job = None
                self.page.update_controls()

        self.clip_job = job = self.page.ctx.tasks.start(lambda _worker: sounds.load_clip(path))
        job.result.connect(show)
        job.error.connect(error)
        job.finished.connect(finish)
        self.page.update_controls()
