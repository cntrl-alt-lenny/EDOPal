"""Review and publish new deck copies without editing the source deck."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QSizePolicy

from decks import Library, Preview, SavedDeck, preview_repair, preview_ydke
from decks.sharing import MAX_CODE
from studio import theme as t
from studio.pages.mydecks_logic import card_name
from studio.pages.mydecks_widgets import Contents, Screen, button
from studio.tasks import TaskRunner
from studio.widgets import Hero, Panel, label


class CopyReview(Screen):
    def __init__(
        self,
        lib: Library,
        runner: TaskRunner,
        back: Callable,
        save: Callable[[str, Preview], None],
        deck: SavedDeck | None = None,
    ) -> None:
        super().__init__("Repair a copy" if deck else "Import a sharing code", back)
        self.lib, self.runner, self.save = lib, runner, save
        self.reviewed: Preview | None = None
        self.contents: Contents | None = None
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
            self.message.setText(
                "Paste the sharing code from another player to review all three sections."
            )
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
        row = QHBoxLayout()
        row.addWidget(label("New deck name", "muted"))
        row.addWidget(self.name, 1)
        row.addWidget(self.save_btn)
        self.form.body.addLayout(row)
        self.note = label(
            ("Your original deck stays in its folder. " if deck else "")
            + "The new copy appears next time you start EDOPro. You can remove the copy after saving.",
            "muted",
            True,
        )
        self.form.body.addWidget(self.note)
        self.save_btn.setEnabled(False)
        self.name.textChanged.connect(self.enable_save)
        self.name.returnPressed.connect(self.submit)
        self.guide = Hero(
            "Sharing codes",
            "Share from a deck",
            "In My decks, open a saved deck and choose Copy code. Another player can paste that code here.",
            "deck",
        )
        self.body.addWidget(self.guide, 1)
        self.guide.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
            self.display(repair.copy)
            if not repair.changes:
                self.reviewed = None
                self.enable_save()

    def enable_save(self) -> None:
        self.save_btn.setEnabled(bool(self.reviewed and self.name.text().strip()))

    def submit(self) -> None:
        if self.save_btn.isEnabled() and self.reviewed:
            self.save(self.name.text(), self.reviewed)

    def invalidate(self) -> None:
        self.reviewed = None
        self.enable_save()
        if self.contents:
            self.body.removeWidget(self.contents)
            self.contents.hide()
            self.contents.deleteLater()
            self.contents = None
        self.guide.show()

    def decode(self) -> None:
        self.invalidate()
        try:
            reviewed = preview_ydke(self.code.text(), self.lib.catalog)
        except ValueError:
            self.message.setText(
                "This sharing code could not be read. Copy the entire code starting with ydke:// and try again."
            )
            return
        self.message.setText(
            "Review the cards below, then name your new copy."
            + (
                " Some printed numbers are missing from your game’s card data."
                if reviewed.unresolved
                else ""
            )
        )
        self.display(reviewed)
        self.name.setFocus()
        self.name.selectAll()

    def display(self, reviewed: Preview) -> None:
        self.reviewed = reviewed
        self.enable_save()
        self.contents = Contents(self.runner, self.lib.game, reviewed)
        self.guide.hide()
        self.body.insertWidget(self.body.count() - 1, self.contents, 1)
