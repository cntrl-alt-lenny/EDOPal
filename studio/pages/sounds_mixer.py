"""The event mixer and its inline sound picker."""

from collections.abc import Callable, Iterable
from functools import partial

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

import sounds
from studio import theme as t
from studio.pages.card_views import line
from studio.pages.sounds_model import display_text
from studio.pages.sounds_views import InlineView, button_row, elided
from studio.widgets import Button, Panel, label, margins


class EventRow(QWidget):
    def __init__(self, name: str, description: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        margins(row, 0)
        row.setSpacing(t.MD)
        event = QVBoxLayout()
        event.setSpacing(t.XS)
        event.addWidget(label(name, "subheading", True))
        event.addWidget(label(description, "muted", True))
        row.addLayout(event, 5)
        self.choice = elided("body")
        row.addWidget(self.choice, 3)
        self.duration = label("", "muted")
        self.duration.setMinimumWidth(t.HUGE)
        row.addWidget(self.duration)
        self.play = Button("Play", "secondary")
        self.play.setAccessibleName(f"Play {name}")
        row.addWidget(self.play)
        self.edit = Button("Choose sound", "quiet")
        self.edit.setAccessibleName(f"Choose sound for {name}")
        row.addWidget(self.edit)

    def set_choice(self, name: str, duration: float | None, playable: bool) -> None:
        self.choice.set_value(name, display_text(name))
        self.duration.setText(f"{duration:.2f} s" if duration is not None else "—")
        self.play.setEnabled(playable)


class MixerView(InlineView):
    def __init__(self, chants: bool = False) -> None:
        title, hint = (
            (
                "Card chants",
                "A chant plays for a specific card when it is activated, attacks or is summoned.",
            )
            if chants
            else (
                "Mix your own",
                "Choose a sound for each event. Use ↑ and ↓ to move and Enter or Space to listen.",
            )
        )
        super().__init__(title, hint)
        self.rows: dict[str, EventRow] = {}
        self.scroller: QScrollArea | None = None
        self.status = label("", "body", True)
        self.status.hide()
        self.body.addWidget(self.status)
        self.chants = Button("Card chants", "secondary", "sounds")
        if chants:
            self.number = QLineEdit()
            self.number.setPlaceholderText("Card number")
            self.number.setAccessibleName("Number printed on the card")
            self.number.setMinimumHeight(t.BUTTON_H)
            self.number.setMaxLength(10)
            form = Panel(t.LG)
            form.body.addWidget(
                label("Add a chant using the number printed on the card.", "body", True)
            )
            self.forms = {kind: Button(kind.title()) for kind in sounds.CHANT_TYPES}
            row = button_row(*self.forms.values())
            row.insertWidget(0, self.number, 1)
            form.body.addLayout(row)
            self.body.addWidget(form)
        else:
            self.actions.addWidget(self.chants)
        self.groups = QWidget()
        self.groups_body = QVBoxLayout(self.groups)
        margins(self.groups_body, 0)
        self.groups_body.setSpacing(t.LG)
        self.body.addWidget(self.groups)
        self.body.addStretch(1)

    def build(
        self, keys: Iterable[str], play: Callable[[str], None], edit: Callable[[str], None]
    ) -> None:
        for index in reversed(range(self.groups_body.count())):
            widget = self.groups_body.takeAt(index).widget()
            widget.hide()
            widget.deleteLater()
        self.rows.clear()
        groups: dict[str, Panel] = {}
        for key in keys:
            event = sounds.event_for(key)
            group = event.group
            name = event.name
            if group == "Chants":
                _, family, number = key.split("/")
                group = f"{family.title()} chants"
                name = f"Card {number}"
            if group not in groups:
                panel = groups[group] = Panel(t.LG)
                panel.body.setSpacing(t.SM)
                panel.body.addWidget(label(group, "goldKicker"))
                self.groups_body.addWidget(panel)
            panel = groups[group]
            panel.body.addWidget(line())
            row = self.rows[key] = EventRow(name, event.description)
            panel.body.addWidget(row)
            row.play.clicked.connect(partial(play, key))
            row.edit.clicked.connect(partial(edit, key))
            row.play.installEventFilter(self)
            row.edit.installEventFilter(self)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        if (
            isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
        ):
            rows = list(self.rows.values())
            current = next(i for i, row in enumerate(rows) if watched in (row.play, row.edit))
            offset = -1 if event.key() == Qt.Key.Key_Up else 1
            row = rows[max(0, min(len(rows) - 1, current + offset))]
            focus = row.play if row.play.isEnabled() else row.edit
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
            if self.scroller:
                self.scroller.ensureWidgetVisible(row)
            return True
        return super().eventFilter(watched, event)


class PickerView(InlineView):
    def __init__(self) -> None:
        super().__init__("Choose a sound")
        self.current = Panel(t.LG)
        self.current_name = elided("subheading")
        self.current.body.addWidget(label("CURRENT CHOICE", "goldKicker"))
        self.current.body.addWidget(self.current_name)
        self.body.addWidget(self.current)
        self.keep = Button("Keep game sound", "secondary")
        self.silence = Button("Silence this event", "secondary")
        self.import_clip = Button("Import audio", "secondary", "folder")
        actions = button_row(self.keep, self.silence, self.import_clip)
        actions.addStretch(1)
        self.body.addLayout(actions)
        self.body.addWidget(label("Sounds from your sets", "heading"))
        self.options = QVBoxLayout()
        self.options.setSpacing(t.SM)
        self.body.addLayout(self.options)
        self.body.addStretch(1)

    def clear_options(self) -> None:
        while self.options.count():
            widget = self.options.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def add_option(
        self,
        name: str,
        category: str,
        clip: sounds.Clip | None,
        use: Callable[[], None],
    ) -> Button:
        panel = Panel(t.MD)
        row = QHBoxLayout()
        row.setSpacing(t.MD)
        title = elided("subheading")
        title.set_value(name, display_text(name))
        row.addWidget(title, 1)
        detail = "Silent" if clip is None else f"{clip.duration:.2f} s"
        row.addWidget(label(f"{category}  ·  {detail}", "muted"))
        preview = Button("Play", "quiet")
        preview.setAccessibleName(f"Play sound from {display_text(name)}")
        preview.setEnabled(clip is not None)
        row.addWidget(preview)
        choose = Button("Use sound")
        choose.setAccessibleName(f"Use sound from {display_text(name)}")
        choose.clicked.connect(use)
        row.addWidget(choose)
        panel.body.addLayout(row)
        self.options.addWidget(panel)
        return preview
