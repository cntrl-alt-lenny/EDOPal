"""The inline preview, import progress, receipt and historical version review."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from studio import theme as t
from studio.pages.decks_browser import ListMessage, Rows
from studio.pages.decks_flow import GOAT_ROOM_SETTINGS, ImportReceipt
from studio.pages.decks_preview_data import GROUPS, PreviewSection
from studio.tasks import TaskRunner
from studio.widgets import (
    Button,
    ElidedLabel,
    Hero,
    ImageTileGrid,
    Notice,
    Panel,
    ProgressBlock,
    SegmentedControl,
    Tile,
    ToggleSwitch,
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


class PreviewView(Detail):
    def __init__(self, tasks: TaskRunner) -> None:
        super().__init__("Back to standings")
        self.title, self.meta = self.heading("", "")
        self.message = ListMessage()
        self.body.addWidget(self.message, 1)
        self.sections = SegmentedControl(
            [
                (name, "0 cards", symbol)
                for name, symbol in zip(GROUPS, ("deck", "cards", "sleeves"), strict=True)
            ]
        )
        self.body.addWidget(self.sections)

        self.content = Panel(t.LG)
        self.types = label("", "body", True)
        self.content.body.addWidget(self.types)
        collection = QHBoxLayout()
        collection.setSpacing(t.LG)
        self.gallery = ImageTileGrid(tasks)
        self.gallery.setAccessibleName("Cards with artwork on disk")
        self.gallery.setMinimumHeight(t.TILE_SIZE[1] + t.LG)
        self.gallery.viewport().setStyleSheet(f"background: {t.RAIL};")
        collection.addWidget(self.gallery, 3)
        self.names = Rows(("Card", "Copies"))
        self.names.setAccessibleName("Cards without local artwork")
        self.names.fixed_column(1, t.HUGE + t.MD)
        collection.addWidget(self.names, 2)
        self.content.body.addLayout(collection, 1)
        self.empty = label("There are no cards in this section.", "muted")
        self.content.body.addWidget(self.empty)
        self.body.addWidget(self.content, 1)

        footer = Panel(t.LG)
        row = QHBoxLayout()
        self.source = Button("Source decklist", "quiet", "link")
        self.save = Button("Add to EDOPro", "primary", "download")
        row.addWidget(self.source)
        row.addStretch()
        row.addWidget(self.save)
        self.goat = ToggleSwitch(
            "Use installed GOAT card versions",
            "Save a separate historical copy.",
            False,
        )
        footer.body.addWidget(self.goat)
        footer.body.addLayout(row)
        self.body.addWidget(footer)
        self.sections.changed.connect(self.show_section)
        self.data: tuple[PreviewSection, ...] = ()

    def start(self, event: Event, deck: Deck) -> None:
        self.title.set_value(deck.name)
        placement = f"  ·  Place {deck.place}" if deck.place else ""
        self.meta.set_value(f"{deck.builder}  ·  {event.name}{placement}")
        self.data = ()
        self.gallery.set_tiles(())
        self.sections.hide()
        self.content.hide()
        self.message.set_copy("Loading deck preview…", "Reading the published list.", False)
        self.message.show()

    def loaded(self, sections: tuple[PreviewSection, ...]) -> None:
        self.data = sections
        for button, section in zip(self.sections.buttons, sections, strict=True):
            count = f"{section.count} {'card' if section.count == 1 else 'cards'}"
            button.hint.setText(count)
            button.setAccessibleDescription(count)
        self.sections.select(0)
        self.show_section(0)
        self.sections.show()
        self.content.show()
        self.message.hide()

    def show_section(self, section: int) -> None:
        cards = self.data[section]
        self.types.setText(
            "   ·   ".join(f"{count} {kind.lower()}" for kind, count in cards.type_counts)
        )
        self.types.setVisible(bool(cards.cards))
        images = [
            Tile(f"{card.name} ×{card.copies}", card.artwork)
            for card in cards.cards
            if card.artwork is not None
        ]
        self.gallery.set_tiles(images)
        self.gallery.setVisible(bool(images))
        self.names.clear()
        for card in cards.cards:
            if card.artwork is None:
                self.names.row((card.name, str(card.copies)))
        self.names.setVisible(self.names.topLevelItemCount() > 0)
        self.empty.setVisible(not cards.cards)


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

        actions = QHBoxLayout()
        actions.setSpacing(t.SM)
        self.undo = Button("Undo this import")
        self.retry = Button("Retry", "primary")
        self.review = Button("Review GOAT matches")
        self.artwork = Button("Get HD artwork for these decks", "primary", "download")
        actions.addWidget(self.undo)
        actions.addWidget(self.review)
        actions.addStretch()
        actions.addWidget(self.retry)
        actions.addWidget(self.artwork)
        self.body.addLayout(actions)
        self.rows.currentItemChanged.connect(self._show_entry)
        self.receipt: ImportReceipt | None = None
        self.removed: set[Path] = set()

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
