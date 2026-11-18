"""Review and publish new deck copies without editing the source deck."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from decks import Library, Preview, SavedDeck, preview_repair, preview_ydke
from decks.sharing import MAX_CODE
from studio import theme as t
from studio.pages.mydecks_checks import ReadingPane
from studio.pages.mydecks_logic import card_name
from studio.pages.mydecks_widgets import Contents, Screen, button
from studio.tasks import Job, TaskRunner, WorkerContext
from studio.widgets import Panel, SectionHeader, label, margins

CODE_HELP = "Paste a sharing code to review the Main, Extra and Side decks."


class CopyReview(Screen):
    def __init__(
        self,
        lib: Library,
        runner: TaskRunner,
        back: Callable[[], object],
        save: Callable[[str, Preview], None],
        deck: SavedDeck | None = None,
    ) -> None:
        super().__init__("Repair a copy" if deck else "Import a sharing code", back)
        self.lib, self.runner, self.save = lib, runner, save
        self.reviewed: Preview | None = None
        self.contents: Contents | None = None
        self.decode_job: Job | None = None
        self.duplicate = ""
        self.root_names = {d.name.casefold() for d in lib.decks if d.folder == Path(".")}
        self.form = Panel(t.LG)
        self.body.addWidget(self.form)
        self.message = label("", "body", True)
        self.form.body.addWidget(self.message)
        if not deck:
            self.code = QLineEdit()
            self.code.setMaxLength(MAX_CODE + 1)
            self.code.setAccessibleName("Sharing code")
            self.code.setPlaceholderText("Paste a deck sharing code")
            self.preview_btn = button("Preview deck", self.decode, "secondary", "deck")
            self.message.setText(CODE_HELP)
            row = QHBoxLayout()
            row.addWidget(self.code, 1)
            row.addWidget(self.preview_btn)
            self.form.body.addLayout(row)
            self.code.returnPressed.connect(self.decode)
            self.code.textChanged.connect(self.invalidate)
            self.initial = self.code
        self.name = QLineEdit()
        self.name.setAccessibleName("New deck name")
        self.name.setPlaceholderText("New deck name")
        self.name.setText(f"{deck.name} repaired" if deck else "Imported deck")
        self.save_btn = button("Save new copy", self.submit, "primary", "download")
        self.review_area = QWidget()
        self.review_body = QVBoxLayout(self.review_area)
        margins(self.review_body, 0)
        self.body.addWidget(self.review_area, 1)
        self.actions = Panel(t.MD)
        self.body.addWidget(self.actions)
        row = QHBoxLayout()
        row.addWidget(label("New deck name", "muted"))
        row.addWidget(self.name, 1)
        row.addWidget(self.save_btn)
        self.actions.body.addLayout(row)
        self.note = label("", "muted", True)
        self.actions.body.addWidget(self.note)
        self.save_btn.setEnabled(False)
        self.name.textChanged.connect(self.enable_save)
        self.name.returnPressed.connect(self.submit)
        self.guide = SectionHeader(
            "Share from a deck",
            "In My decks, open a saved deck and choose Copy code. Another player can paste that code here.",
        )
        self.review_body.addWidget(self.guide, 0, Qt.AlignmentFlag.AlignTop)
        self.review_body.addStretch()
        self.enable_save()
        if deck:
            repair = preview_repair(deck.preview, lib.catalog)
            lines = [
                f"Replace printed number {r.before} with {card_name(repair.copy, r.after)} "
                f"({r.copies} {'copy' if r.copies == 1 else 'copies'})."
                for r in repair.changes
            ]
            lines += [
                f"Keep printed number {code}. {reason.replace('successor', 'replacement')}."
                for code, reason in repair.unresolved.items()
            ]
            self.message.setText(
                "\n".join(lines) or "Every card in this deck is already recognised."
            )
            if len(lines) > 2:
                report = ReadingPane("Suggested card replacements")
                report.setPlainText(self.message.text())
                report.setMaximumHeight(t.TILE_H + t.XL)
                self.message.hide()
                self.form.body.addWidget(report)
            self.display(repair.copy)
            if not repair.changes:
                self.reviewed = None
                self.enable_save()

    def enable_save(self) -> None:
        name = self.name.text().strip()
        self.save_btn.setEnabled(bool(self.reviewed and name))
        self.actions.setVisible(self.reviewed is not None)
        notes = []
        if self.reviewed and self.duplicate:
            notes.append(f"These cards are already saved in {self.duplicate}.")
        if self.reviewed and name.casefold() in self.root_names:
            notes.append("A deck has this name. The new copy will get a numbered filename.")
        self.note.setText(" ".join(notes) or "The new copy appears next time you start EDOPro.")

    def submit(self) -> None:
        if self.save_btn.isEnabled() and self.reviewed:
            self.save(self.name.text(), self.reviewed)

    def invalidate(self) -> None:
        self.leave()
        self.duplicate = ""
        self.reviewed = None
        self.enable_save()
        if self.contents:
            self.review_body.removeWidget(self.contents)
            self.contents.hide()
            self.contents.deleteLater()
            self.contents = None
        self.guide.show()
        self.preview_btn.setEnabled(True)
        self.message.setText(CODE_HELP)

    def leave(self) -> None:
        if self.decode_job is not None:
            self.decode_job.cancel()
            self.decode_job = None

    def decode(self) -> None:
        self.invalidate()
        code = self.code.text()
        if len(code) > MAX_CODE:
            self.message.setText(
                "This sharing code is too large. Check what you copied and try again."
            )
            return
        library = self.lib
        self.preview_btn.setEnabled(False)
        self.message.setText("Reading sharing code…")

        def work(worker: WorkerContext) -> tuple[Preview, str]:
            reviewed = preview_ydke(code, library.catalog)
            for deck in library.decks:
                if worker.cancel.is_set():
                    break
                if deck.preview and deck.preview.sections == reviewed.sections:
                    return reviewed, deck.name
            return reviewed, ""

        self.decode_job = job = self.runner.start(work)

        def ready(result: tuple[Preview, str]) -> None:
            if self.decode_job is not job:
                return
            reviewed, self.duplicate = result
            self.message.setText(
                "Review the cards below, then name your new copy."
                + (
                    " Some cards are missing from your game’s card data."
                    if reviewed.unresolved
                    else ""
                )
            )
            self.display(reviewed)
            if self.isVisible():
                self.name.setFocus()
                self.name.selectAll()

        def failed(_: Exception) -> None:
            if self.decode_job is job:
                self.message.setText(
                    "This sharing code could not be read. Copy the entire code starting with ydke:// and try again."
                )

        def finished() -> None:
            if self.decode_job is job:
                self.decode_job = None
                self.preview_btn.setEnabled(True)

        job.result.connect(ready)
        job.error.connect(failed)
        job.finished.connect(finished)

    def display(self, reviewed: Preview) -> None:
        self.reviewed = reviewed
        self.enable_save()
        self.contents = Contents(self.runner, self.lib.game, reviewed)
        self.guide.hide()
        self.review_body.insertWidget(0, self.contents, 1)
