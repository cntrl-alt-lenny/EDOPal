"""The matchup guide and a puzzle's spoiler-safe journal."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QWidget

from config import APP_NAME
from practice.bots import Bot, rule_name
from practice.progress import STATES, Progress
from practice.puzzles import Puzzle
from studio import theme as t
from studio.pages.mydecks_logic import finding_text
from studio.pages.mydecks_widgets import choice
from studio.pages.practice_widgets import across, column, scroll, section
from studio.widgets import Button, Panel, label, margins


def rules(bot: Bot) -> str:
    return ", ".join(map(rule_name, bot.rules)) if bot.rules else "Rules not stated"


def bot_guide(bot: Bot, index: int, list_name: str, runtime: str, distance: str = "") -> QWidget:
    widget, layout = column()
    how = Panel(t.XL)
    unreadable = (
        bot.preview is not None
        and index >= 0
        and bot.fit(index) == "Cannot tell"
        and not bot.checks[index].findings
    )
    chosen_list = f" “{list_name}”" if list_name and not unreadable else ""
    rule = f" ({rules(bot)})" if bot.rules else ""
    host = (
        f"2. Use Open Formats for the {list_name} list and its room settings, then select OK."
        if distance
        else f"2. Choose a Rule{rule} and the Forbidden List{chosen_list}, then select OK."
    )
    section(
        how,
        "Play in EDOPro",
        "1. Open LAN + AI and choose Host.\n\n"
        f"{host}\n\n"
        f"3. In the room, open Local AI. Pick “{bot.name}” and choose Add AI player.\n\n"
        "4. Choose your own deck, mark yourself Ready and select Start.",
    )
    if runtime:
        how.body.addWidget(label(runtime, "notice", True))
    verdict = Panel(t.XL)
    section(
        verdict, "Bundled format" if distance else "Room list", list_name or "No installed list"
    )
    verdict.body.addWidget(label(bot.fit(index), "heading"))
    if distance:
        verdict.body.addWidget(label(distance, "body", True))
    if bot.fit(index) == "Does not fit":
        verdict.body.addWidget(
            label(
                "This room can refuse the deck. Try another opponent or check another list before hosting.",
                "body",
                True,
            )
        )
    if bot.file:
        verdict.body.addWidget(label(f"Matched deck · {bot.file}", "muted", True))
    findings = bot.checks[index].findings if 0 <= index < len(bot.checks) else ()
    reasons = [finding_text(f) for f in findings]
    verdict.body.addWidget(
        label(
            "\n\n".join(reasons)
            or bot.problem
            or (
                "The deck meets this list’s limits and the standard deck checks."
                if bot.fit(index) == "Fits"
                else "The selected list could not be fully read."
                if list_name
                else "Choose an installed list to check the deck."
            ),
            "body",
            True,
        )
    )
    layout.addWidget(verdict)
    layout.addWidget(how)
    layout.addStretch()
    return scroll(widget)


class Journal(QWidget):
    save_requested = Signal(object)
    retry_requested = Signal()
    undo_requested = Signal()

    def __init__(self, puzzle: Puzzle, progress: Progress, *, issue: str = "") -> None:
        super().__init__()
        outer = QHBoxLayout(self)
        margins(outer, 0)
        outer.setSpacing(t.LG)
        left, reading = column()
        info = Panel(t.LG)
        section(info, "Objective", puzzle.error or puzzle.objective or "Not stated")
        if puzzle.setup:
            section(info, "From the starting setup", puzzle.setup)
        section(
            info,
            "Starting life points",
            f"You · {puzzle.life[0] or 'Not stated'}    Opponent · {puzzle.life[1] or 'Not stated'}",
        )
        section(info, "Difficulty", puzzle.difficulty or "Not stated")
        reading.addWidget(info)
        spoiler = Panel(t.LG)
        self.hint = self._reveal(spoiler, "hints", "\n\n".join(puzzle.hints))
        self.solution = self._reveal(spoiler, "solution", puzzle.solution)
        reading.addWidget(spoiler)
        how = Panel(t.LG)
        section(
            how,
            "Play in EDOPro",
            "Rescan the library after EDOPro updates this puzzle."
            if puzzle.error
            else "1. Choose Puzzles on the main menu.\n\n2. Open "
            + (
                "the puzzle list"
                if "/" not in puzzle.path
                else " → ".join(puzzle.path.split("/")[:-1])
            )
            + f".\n\n3. Select “{puzzle.path.split('/')[-1]}” and choose Load Puzzle.",
        )
        reading.addWidget(how)
        reading.addStretch()
        outer.addWidget(scroll(left), 1)
        right, journal = column()
        outer.addWidget(right, 1)
        editor = Panel(t.LG)
        journal.addWidget(scroll(editor), 1)
        editor.body.addWidget(label("Practice journal", "heading"))
        if issue:
            editor.body.addWidget(label(issue, "body", True))
            self.retry = Button("Retry journal", "primary")
            self.retry.clicked.connect(self.retry_requested)
            editor.body.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)
            editor.body.addStretch()
            return
        editor.body.addWidget(
            label(f"Saved in {APP_NAME}. You can change or clear every entry.", "muted", True)
        )
        editor.body.addWidget(label("Progress", "subheading"))
        self.state = choice("Puzzle progress", STATES)
        editor.body.addWidget(self.state)
        editor.body.addWidget(label("Last played", "subheading"))
        self.played = QLineEdit()
        self.played.setPlaceholderText("YYYY-MM-DD")
        self.played.setAccessibleName("Last played, year-month-day")
        self.played.setClearButtonEnabled(True)
        today = Button("Today")
        today.clicked.connect(lambda: self.played.setText(date.today().isoformat()))
        editor.body.addLayout(across(self.played, today))
        editor.body.addWidget(label("Personal note", "subheading"))
        self.note = QPlainTextEdit()
        self.note.setAccessibleName("Personal note")
        self.note.setPlaceholderText("What would you try next time?")
        self.note.setTabChangesFocus(True)
        self.note.setMinimumHeight(t.TILE_H)
        self.note.setStyleSheet(
            f"QPlainTextEdit {{ background: {t.RAISED}; border: {t.HAIR}px solid {t.EDGE}; border-radius: {t.R_SMALL}px; padding: {t.SM}px; }} QPlainTextEdit:focus {{border: {t.FOCUS}px solid {t.PALE};}}"
        )
        editor.body.addWidget(self.note, 1)
        self.message = label("", "muted", True)
        journal.addWidget(self.message)
        self.message.hide()
        self.save = Button("Save progress", "primary", "check")
        self.undo = Button("Undo last change")
        self.undo.setToolTip("Put back the previous status, date and personal note.")
        self.undo.clicked.connect(self.undo_requested)
        journal.addLayout(across(self.save, self.undo))
        self.undo.hide()
        self.save.clicked.connect(lambda: self.save_requested.emit(self.value()))
        self.set_value(progress)
        self.note.textChanged.connect(self._edited)
        self.state.currentIndexChanged.connect(self._edited)
        self.played.textChanged.connect(self._edited)

    def _edited(self) -> None:
        self.save.setText("Save progress")
        self.save.setEnabled(True)

    def saved(self) -> None:
        self.save.setText("Saved")
        self.save.setEnabled(False)

    @staticmethod
    def _reveal(panel: Panel, name: str, value: str) -> Button:
        button = Button(f"Show {name}" if value else f"{name.title()} · Not stated")
        button.setEnabled(bool(value))
        button.setCheckable(True)
        copy = label("", "body", True)
        copy.hide()

        def toggle(on: bool) -> None:
            copy.setText(value if on else "")
            copy.setVisible(on)
            button.setText(f"{'Hide' if on else 'Show'} {name}")

        button.toggled.connect(toggle)
        panel.body.addWidget(button)
        panel.body.addWidget(copy)
        return button

    def value(self) -> Progress:
        return Progress(
            self.state.currentText(), self.note.toPlainText(), self.played.text().strip()
        )

    def set_value(self, value: Progress) -> None:
        self.state.setCurrentText(value.status)
        self.note.setPlainText(value.note)
        self.played.setText(value.played)
