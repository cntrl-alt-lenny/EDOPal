from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QLineEdit, QWidget

from replays import ReplayEntry, ReplayNotes
from studio import theme as t
from studio.pages.health_layout import editor, row, technical_details
from studio.pages.replays_library import format_tags, notes_from_fields, players, watch_instructions
from studio.widgets import Button, ElidedLabel, Panel, label, margins


class ReplayForm(QWidget):
    def __init__(self, entry: ReplayEntry, can_undo: bool) -> None:
        super().__init__()
        self.entry = entry
        self.can_undo = can_undo
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        margins(self.columns, 0)
        self.columns.setSpacing(t.LG)
        self.recording = info = Panel(t.LG)
        self.columns.addWidget(info, 1)
        info.body.addWidget(label("RECORDING", "goldKicker"))
        info.body.addWidget(label(players(entry), "heading", True))
        if entry.error:
            info.body.addWidget(label("Replay could not be read", "subheading"))
            info.body.addWidget(
                label(
                    "Check that the file is complete, then refresh Replays before trying to watch it.",
                    "body",
                    True,
                )
            )
        else:
            info.body.addWidget(label("Watch in EDOPro", "subheading"))
            info.body.addWidget(label(watch_instructions(entry), "body", True))
        filename = ElidedLabel()
        filename.set_value(str(entry.path), entry.path.name)
        info.body.addWidget(filename)
        self.reveal = Button("Show file", "secondary", "folder")
        info.body.addWidget(self.reveal, 0, Qt.AlignmentFlag.AlignLeft)
        self.export = Button("Export replay and notes", "secondary", "download")
        if entry.error:
            technical_details(info.body, entry.error)
            self.export.hide()
            self.export.setParent(info)
        else:
            info.body.addSpacing(t.SM)
            info.body.addWidget(label("Share a copy", "subheading"))
            info.body.addWidget(
                label(
                    "This ZIP contains the replay, including player names, plus your saved label, tags and notes. Review them before sharing.",
                    "body",
                    True,
                )
            )
            info.body.addWidget(self.export, 0, Qt.AlignmentFlag.AlignLeft)
        info.body.addStretch()
        self.editor = notes = Panel(t.LG)
        self.columns.addWidget(notes, 1)
        notes.setVisible(bool(entry.replay_id))
        notes.body.addWidget(label("Label and notes", "heading"))
        self.name = QLineEdit(entry.annotation.label)
        self.tags = QLineEdit(format_tags(entry.annotation.tags))
        for text, control, hint in (
            ("Label", self.name, "Give this duel a name"),
            ("Tags", self.tags, "Separate tags with commas"),
        ):
            control.setAccessibleName(text)
            control.setPlaceholderText(hint)
            notes.body.addWidget(label(text, "small"))
            notes.body.addWidget(control)
        notes.body.addWidget(label("Notes", "small"))
        self.notes = editor("Replay notes")
        self.notes.setPlaceholderText("Which turn would you watch again?")
        self.notes.setPlainText(entry.annotation.notes)
        self.notes.setMinimumHeight(t.BUTTON_H + t.SM)
        notes.body.addWidget(self.notes, 1)
        buttons = row()
        self.footer = QWidget(self)
        self.footer.setLayout(buttons)
        self.footer.hide()
        self.undo = Button("Undo last replay edit", "quiet", "back")
        self.undo.setToolTip("Restore the previous label, tags and notes.")
        buttons.addWidget(self.undo)
        buttons.addStretch()
        self.save = Button("Save replay details", "primary", "check")
        buttons.addWidget(self.save)
        for field in (self.name, self.tags, self.notes):
            field.textChanged.connect(self._changed)
            field.setEnabled(bool(entry.replay_id))
        self._changed()

    def arrange(self, width: int) -> None:
        stacked = width < t.COMPACT_WIDTH - t.RAIL_WIDTH
        self.notes.setMaximumHeight(t.TILE_H if stacked else t.HERO_MAX)
        self.columns.setDirection(
            QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        )
        first = self.editor if stacked and not self.entry.error else self.recording
        if self.columns.itemAt(0).widget() is not first:
            self.columns.removeWidget(first)
            self.columns.insertWidget(0, first, 1)

    def draft(self) -> ReplayNotes:
        return notes_from_fields(self.name.text(), self.notes.toPlainText(), self.tags.text())

    def _changed(self) -> None:
        dirty = self.draft() != self.entry.annotation
        self.undo.setVisible(self.can_undo and not dirty)
        self.save.setText("Saved" if self.can_undo and not dirty else "Save replay details")
        self.save.setEnabled(bool(self.entry.replay_id and dirty))
        self.export.setEnabled(bool(self.entry.replay_id and not self.entry.error and not dirty))
        self.export.setToolTip("Save your edits before exporting." if dirty else "")
