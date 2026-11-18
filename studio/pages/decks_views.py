"""The inline preview, import progress, receipt and historical version review."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QVBoxLayout, QWidget

from studio import theme as t
from studio.pages.decks_browser import ListMessage, Rows
from studio.pages.decks_contents import DeckContents
from studio.pages.decks_flow import GOAT_ROOM_SETTINGS, ImportReceipt
from studio.pages.decks_handoff import HD_ARTWORK
from studio.pages.decks_preview_data import PreviewSection
from studio.tasks import TaskRunner
from studio.widgets import (
    Button,
    ElidedLabel,
    Hero,
    Notice,
    Panel,
    ProgressBlock,
    glyph,
    label,
)
from tournament_decks import Deck, Event


class Detail(QWidget):
    def __init__(self, back: str) -> None:
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(t.XXL, t.XXL, t.XXL, t.XL)
        self.body.setSpacing(t.MD)
        self.back = Button(back, "quiet", "back")
        self.body.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)

    def heading(self, title: str, detail: str) -> tuple[ElidedLabel, ElidedLabel]:
        name = ElidedLabel()
        name.setProperty("role", "display")
        name.set_value(title)
        meta = ElidedLabel()
        meta.setProperty("role", "body")
        meta.set_value(detail)
        self.body.addWidget(name)
        self.body.addWidget(meta)
        return name, meta


class PreviewView(Panel):
    def __init__(self, tasks: TaskRunner) -> None:
        super().__init__(t.MD)
        self.setAccessibleName("Tournament deck preview")
        self.body.setSpacing(t.XS)
        event_row = QHBoxLayout()
        self.event = ElidedLabel()
        self.event.setProperty("role", "muted")
        event_row.addWidget(self.event, 1)
        self.source = Button("Source", "quiet", "link")
        self.source.setAccessibleName("Open the published decklist")
        self.source.setMinimumHeight(t.XXL)
        event_row.addWidget(self.source)
        self.body.addLayout(event_row)
        heading = QHBoxLayout()
        self.title = ElidedLabel()
        self.title.setProperty("role", "heading")
        heading.addWidget(self.title, 3)
        self.meta = ElidedLabel()
        self.meta.setProperty("role", "body")
        heading.addWidget(self.meta, 2)
        self.body.addLayout(heading)
        self.message = ListMessage()
        self.body.addWidget(self.message, 1)
        self.contents = DeckContents(tasks, inset=0)
        self.body.addWidget(self.contents, 1)
        self.reset("Choose a tournament", "Published decklists will appear here.")

    def reset(self, title: str, description: str) -> None:
        self.event.hide()
        self.title.set_value("Deck preview")
        self.meta.hide()
        self.source.hide()
        self.show_message(title, description)

    def start(self, event: Event, deck: Deck) -> None:
        self.event.set_value(event.name)
        self.event.show()
        self.title.set_value(deck.name)
        placement = f"Place {deck.place}" if deck.place else "Placement unlisted"
        self.meta.set_value(f"{placement}  ·  Pilot {deck.builder}")
        self.meta.show()
        self.source.show()

    def show_message(self, title: str, description: str, retry: bool = False) -> None:
        self.contents.hide()
        self.message.set_copy(title, description, retry)
        self.message.show()

    def loaded(self, sections: tuple[PreviewSection, ...]) -> None:
        self.contents.set_sections(sections)
        self.contents.show()
        self.message.hide()


class ImportView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        body = QVBoxLayout(self)
        body.setContentsMargins(t.XXL, t.XXL, t.XXL, t.XXL)
        body.setSpacing(t.LG)
        body.addStretch()
        body.addWidget(
            Hero(
                "Tournament decks",
                "Saving decks",
                "You can change folders when the save finishes.",
                "decks",
            )
        )
        self.progress = ProgressBlock()
        self.progress.cancel.setText("Cancel import")
        body.addWidget(self.progress)
        body.addStretch()


class ReceiptView(Detail):
    def __init__(self) -> None:
        super().__init__("Back to standings")
        self.title, self.meta = self.heading("Decks saved", "")
        metrics = QHBoxLayout()
        metrics.setSpacing(t.MD)
        self.totals = []
        self.captions = []
        for caption, symbol in (
            ("Added", "check"),
            ("Already saved", "deck"),
            ("Skipped", "warning"),
        ):
            panel = Panel(t.LG)
            row = QHBoxLayout()
            value = label("0", "metric")
            row.addWidget(value)
            row.addStretch()
            row.addWidget(glyph(symbol, t.ICON_BIG))
            panel.body.addLayout(row)
            caption_label = label(caption, "body")
            panel.body.addWidget(caption_label)
            metrics.addWidget(panel, 1)
            self.totals.append(value)
            self.captions.append(caption_label)
        self.body.addLayout(metrics)

        self.notice = Notice()
        self.body.addWidget(self.notice)
        self.rows = Rows(("Deck", "Player", "Result"))
        self.rows.setAccessibleName("Deck save results")
        self.body.addWidget(self.rows, 1)
        self.selected_detail = label("", "small", True)
        self.selected_file = ElidedLabel()
        self.body.addWidget(self.selected_detail)
        self.body.addWidget(self.selected_file)
        self.notes = label("", "muted", True)
        self.body.addWidget(self.notes)
        self.undo_note = label(
            "Undo removes new copies from this import and keeps earlier copies and edits.",
            "muted",
            True,
        )
        self.body.addWidget(self.undo_note)

        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.actions.setSpacing(t.SM)
        maintenance = QHBoxLayout()
        next_steps = QHBoxLayout()
        self.undo = Button("Undo this import")
        self.retry = Button("Retry")
        self.review = Button("Review GOAT matches")
        self.artwork = Button(HD_ARTWORK, "secondary", "download")
        self.open_saved = Button("Open in My decks", "primary", "arrow")
        maintenance.addWidget(self.undo)
        maintenance.addWidget(self.review)
        maintenance.addStretch()
        next_steps.addStretch()
        next_steps.addWidget(self.retry)
        next_steps.addWidget(self.artwork)
        next_steps.addWidget(self.open_saved)
        self.actions.addLayout(maintenance)
        self.actions.addLayout(next_steps, 1)
        self.body.addLayout(self.actions)
        self.rows.currentItemChanged.connect(self._show_entry)
        self.receipt: ImportReceipt | None = None
        self.removed: set[Path] = set()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.actions.setDirection(
            QBoxLayout.Direction.LeftToRight
            if self.width() >= t.HERO_MAX + 2 * t.TILE_LARGE
            else QBoxLayout.Direction.TopToBottom
        )

    def _show_entry(self) -> None:
        current = self.rows.currentIndex().row()
        if self.receipt is None or current < 0:
            self.selected_detail.clear()
            self.selected_file.set_value("")
            return
        entry = self.receipt.entries[current]
        if entry.saved:
            saved = entry.saved
            kept = ", ".join(path.name for path in saved.kept)
            if saved.path in self.removed:
                detail = "Removed by undo."
            elif kept:
                detail = f"Earlier copies kept ({len(saved.kept)})."
            else:
                detail = "Saved in your deck folder."
            self.selected_detail.setText(detail)
            self.selected_file.set_value(
                f"{saved.path.name}   ·   Kept {kept}" if kept else saved.path.name
            )
        else:
            self.selected_detail.setText(
                "" if entry.reason == self.receipt.blocked else entry.reason
            )
            self.selected_file.set_value("")
        self.selected_detail.setVisible(bool(self.selected_detail.text()))
        self.selected_file.setVisible(bool(self.selected_file.value))

    def show_receipt(self, receipt: ImportReceipt, ready: bool, can_undo: bool) -> None:
        self.receipt = receipt
        self.removed.clear()
        if receipt.stopped:
            title = "Import stopped"
        elif not receipt.paths:
            title = "No decks saved"
        elif receipt.skipped:
            title = "Some decks saved"
        else:
            title = "Decks saved"
        self.title.set_value(title)
        self.meta.set_value(
            "Find these decks the next time you start EDOPro."
            if receipt.paths
            else "Choose Back to return to your tournament."
        )
        counts = (len(receipt.created), len(receipt.paths) - len(receipt.created), receipt.skipped)
        self.captions[0].setText("Added")
        for value, count in zip(self.totals, counts, strict=True):
            value.setText(str(count))

        self.rows.clear()
        for entry in receipt.entries:
            if entry.saved:
                reason = "Added" if entry.saved.added else "Already saved"
                if entry.saved.kept:
                    reason += " · earlier copies kept"
            else:
                reason = "Skipped"
            row = self.rows.row((entry.deck.name, entry.deck.builder, reason))
            row.setForeground(2, QColor(t.GREEN if entry.saved else t.RED))
            if entry.reason:
                row.setToolTip(2, entry.reason)
        if self.rows.topLevelItemCount():
            self.rows.setCurrentItem(self.rows.topLevelItem(0))

        notes = []
        if receipt.matched:
            notes.append(f"Matched {receipt.matched} card copies to printings installed in EDOPro.")
        if receipt.unresolved:
            codes = ", ".join(str(code) for code in receipt.unresolved)
            notes.append(f"Update your EDOPro cards before playing. Missing card IDs ({codes}).")
        elif receipt.paths and not receipt.indexed:
            notes.append("Open EDOPro to download its cards before playing.")
        if receipt.historical and receipt.paths:
            notes.append(f"Historical versions used for {receipt.historical_changes} card copies.")
            notes.append(GOAT_ROOM_SETTINGS)
        if receipt.paths and not ready:
            notes.append("Open EDOPro once to download its cards before getting HD artwork.")
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        self.notice.show_message(receipt.blocked)
        self.undo.setVisible(can_undo)
        self.undo_note.setVisible(can_undo)
        self.retry.setVisible(bool(receipt.blocked))
        self.artwork.setVisible(bool(receipt.paths) and ready)
        self.review.setVisible(bool(receipt.review))

    def mark_removed(self, paths: set[Path]) -> None:
        self.removed = paths.copy()
        if self.receipt:
            for i, entry in enumerate(self.receipt.entries):
                if entry.saved and entry.saved.path in paths:
                    self.rows.topLevelItem(i).setText(2, "Removed by undo")
            if all(path in paths for path in self.receipt.paths):
                self.title.set_value("Import undone")
                self.meta.set_value("Earlier copies and personal edits were kept.")
                self.captions[0].setText("Removed")
                self.notes.hide()
                self.review.hide()
            else:
                self.totals[0].setText(str(len(self.receipt.created) - len(paths)))
        self._show_entry()


class GoatReviewView(Detail):
    def __init__(self) -> None:
        super().__init__("Back to save results")
        self.title, self.meta = self.heading("GOAT card versions", "")
        room = Panel(t.LG)
        room.body.addWidget(label("Hosting a GOAT game", "subheading"))
        room.body.addWidget(label(GOAT_ROOM_SETTINGS, "body", True))
        self.body.addWidget(room)
        self.rows = Rows(("Card", "Deck", "Result"))
        self.rows.setAccessibleName("Historical card matches")
        self.body.addWidget(self.rows, 1)

    def populate(self, receipt: ImportReceipt) -> None:
        self.meta.set_value(
            f"{receipt.historical_changes} card copies switched to historical versions"
        )
        self.rows.clear()
        for match in receipt.review:
            result = (
                "Historical version"
                if match.input_id != match.saved_id
                else {
                    "identity unavailable": "Kept · card could not be identified",
                    "forbidden": "Kept · forbidden in GOAT",
                    "over copy limit": "Kept · over the copy limit",
                    "ambiguous versions": "Kept · several possible versions",
                    "no verified version": "Kept · no verified version",
                }.get(match.reason, "Kept published version")
            )
            self.rows.row((match.card, match.deck.name, result))
