"""Catalogue rows and the selected format's pool, history and host recipe."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from config import APP_NAME
from formats.catalogue import Catalogue, host_steps
from formats.installation import State
from studio import theme as t
from studio.context import Installation
from studio.widgets import Button, ElidedLabel, Panel, label, margins

DATA = Qt.ItemDataRole.UserRole


def era_name(era: str) -> str:
    return "Duel Monsters" if era == "DM" else era


class FormatRows(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(DATA)
        painter.save()
        box = option.rect.adjusted(t.XS, 0, -t.XS, 0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.ACTIVE))
            painter.drawRoundedRect(box, t.R_SMALL, t.R_SMALL)
        box.adjust(t.SM, 0, -t.SM, 0)
        painter.setFont(t.font(t.BODY if row else t.CAPTION, bold=not row or selected))
        painter.setPen(QColor(t.GOLD if selected or not row else t.INK))
        align = Qt.AlignmentFlag.AlignVCenter
        if row:
            stamp = row["date"][:7] if row["date"] else "Rolling"
            painter.setFont(t.font(t.SMALL))
            date_width = painter.fontMetrics().horizontalAdvance(stamp)
            painter.setPen(QColor(t.MUTED))
            painter.drawText(box, align | Qt.AlignmentFlag.AlignRight, stamp)
            box.adjust(0, 0, -date_width - t.SM, 0)
            painter.setPen(QColor(t.PALE if selected else t.INK))
            painter.setFont(t.font(t.BODY, bold=selected))
        text = painter.fontMetrics().elidedText(
            index.data(), Qt.TextElideMode.ElideRight, box.width()
        )
        painter.drawText(box, align | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class CatalogueList(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setItemDelegate(FormatRows(self))
        self.setAccessibleName("Formats by era")
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QListWidget {{ background: {t.RAIL}; padding: {t.XS}px; }}")

    def fill(self, rows: list[dict], states: dict[int, State], selected: int | None) -> None:
        self.clear()
        era = None
        chosen = None
        first = None
        for row in rows:
            if row["era"] != era:
                era = row["era"]
                group = QListWidgetItem(era_name(era).upper(), self)
                group.setFlags(Qt.ItemFlag.NoItemFlags)
                group.setSizeHint(QSize(0, t.XXL))
            state = states.get(row["id"])
            prefix = "✓  " if state == State.INSTALLED else "↻  " if state == State.OLD else ""
            item = QListWidgetItem(prefix + row["name"], self)
            item.setData(DATA, row)
            item.setToolTip(
                f"{row['name']} · {row['date'] or 'Rolling format'}"
                + (f" · {state}" if state else "")
            )
            item.setSizeHint(QSize(0, t.XXL))
            if first is None:
                first = item
            if selected == row["id"]:
                chosen = item
        if chosen or first:
            self.setCurrentItem(chosen or first)
            self.scrollToItem(chosen or first, QListWidget.ScrollHint.PositionAtCenter)


class Sheet(Panel):
    """A scrolling detail panel with actions addressable by keyboard and accessible name."""

    def __init__(self) -> None:
        super().__init__(t.LG)
        self.body.setSpacing(t.MD)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)

    def line(self, text: str, role: str = "body") -> None:
        self.body.addWidget(label(text, role, True))

    def action(
        self, name: str, title: str, callback: Callable, primary: bool = False, icon: str = ""
    ) -> Button:
        button = Button(title, "primary" if primary else "secondary", icon)
        button.setObjectName(name)
        button.clicked.connect(callback)
        self.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        return button

    def heading(self, row: dict, tag: str) -> None:
        self.line(
            f"{row['category']}   /   {era_name(row['era']).upper()}   /   {tag.upper()}",
            "goldKicker",
        )
        self.line(row["name"], "display")

    def metrics(self, row: dict) -> None:
        strip = QWidget()
        strip.setStyleSheet(f"background: {t.RAIL}; border-radius: {t.R_SMALL}px;")
        cells = QHBoxLayout(strip)
        margins(cells, t.MD)
        for title, value in zip(
            ("Card names", "Forbidden", "Limited", "Semi-limited"),
            (row["pool"], *row["limits"][:3]),
            strict=True,
        ):
            col = QVBoxLayout()
            col.setSpacing(t.XS)
            col.addWidget(label(f"{value:,}", "heading"))
            col.addWidget(label(title, "muted"))
            cells.addLayout(col, 1)
        self.body.addWidget(strip)


def pool_sheet(
    row: dict,
    recipe: dict,
    state: State,
    installation: Installation,
    write: Callable,
    choose: Callable,
    host: Callable,
    evidence: Callable,
    *,
    inline_copy: Callable | None = None,
) -> Sheet:
    panel = Sheet()
    panel.heading(row, "Card pool")
    when = (
        date.fromisoformat(row["date"]).strftime("%d %B %Y").lstrip("0")
        if row["date"]
        else "Rolling format"
    )
    panel.line(f"{when}    ·    {row['banlist']} banlist", "body")
    if reason := row.get("unsupported"):
        panel.line("List unavailable", "subheading")
        panel.line(reason)
    else:
        panel.metrics(row)
        review = row["validation"]
        detail = f" · {review['slots']:,} slots checked" if review["slots"] else ""
        panel.line(
            f"{row['limits'][3]:,} unlimited printings. All copy-limit counts include alternate artwork.",
            "muted",
        )
        evidence_row = QHBoxLayout()
        evidence_row.addWidget(label(review["label"] + detail, "subheading", True), 1)
        if review["decks"]:
            samples = Button("View decks", "quiet", "decks")
            samples.setObjectName("evidence")
            samples.setAccessibleName("View sampled tournament decks")
            samples.clicked.connect(evidence)
            evidence_row.addWidget(samples)
        panel.body.addLayout(evidence_row)
        if review["failures"]:
            names = sorted({f["name"] for s in review["samples"] for f in s["failures"]})
            panel.line("Sampled decks contain these exceptions: " + ", ".join(names), "muted")
        if not installation.ready:
            panel.line(
                "Choose your game folder to add this list."
                if not installation.path
                else "Open EDOPro once to finish downloading its card databases. Close it and select the folder again."
            )
            panel.action("choose", "Choose folder", choose, True, "folder")
        elif state == State.MODIFIED:
            panel.line("Your file is being kept", "subheading")
            panel.line(
                "A changed list or another file uses this name. Move your copy out of the game’s list folder to add a fresh list."
            )
            path = ElidedLabel()
            relative = Path("lflists") / row["file"]
            path.set_value(str(Path(installation.path) / relative), str(relative))
            panel.body.addWidget(path)
        else:
            if state == State.OLD:
                panel.line(
                    "Update available · This installation has an earlier list.", "subheading"
                )
            installed = state == State.INSTALLED
            panel.action(
                "single",
                "Remove list"
                if installed
                else "Update list"
                if state == State.OLD
                else "Add to EDOPro",
                lambda: write(installed),
                not installed,
                "check" if installed else "download",
            )
            if state == State.OLD:
                panel.action("singleRemove", "Remove earlier list", lambda: write(True))
        panel.line(f"Deck editor → list dropdown → {row['display']}")
        panel.line(
            "Changes appear next time you start EDOPro. Remove the list here to take it out of the dropdown.",
            "muted",
        )
        panel.line(
            f"For deck building, LAN play and hosting friends with the same {APP_NAME} version. Public servers recognise their own lists.",
            "muted",
        )
        if row["name"] == "Goat":
            panel.line("For public Goat rooms, choose EDOPro’s official 2005.4 GOAT list.")
        for note in row["notes"]:
            panel.line(note)
        panel.line("HISTORICAL CARD VERSIONS", "goldKicker")
        if row["category"] == "OCG":
            panel.line(
                "This pool uses current printings. Historical OCG card texts have not been verified."
            )
        else:
            panel.line(
                f"{row['historical']:,} cards use an older version. EDOPro treats older text as a separate card, so its modern printing may be missing from the editor."
            )
            if n := sum(row["fallback"].values()):
                panel.line(
                    f"{n:,} cards use current printings where the historical version is uncertain or unavailable.",
                    "muted",
                )
    panel.line("DUEL RULES", "goldKicker")
    panel.line(
        f"{recipe['base']}   ·   {recipe['fidelity']} historical rules. Set these separately when you create the room."
    )
    if inline_copy:
        panel.line("How to host", "heading")
        panel.line(" ".join(recipe["caveats"]))
        panel.action("copy", "Copy settings", inline_copy)
        for step in host_steps(row, recipe):
            panel.line(step)
    else:
        panel.action("host", "How to host", host, icon="arrow")
        if row.get("unsupported"):
            panel.line(" ".join(recipe["caveats"]))
    return panel


def sample_sheet(row: dict, back: Callable) -> Sheet:
    panel = Sheet()
    panel.action("back", "Back to format", back, icon="back")
    panel.heading(row, "Tournament evidence")
    review = row["validation"]
    panel.line(review["label"], "subheading")
    panel.line(
        f"{review['slots']:,} Main, Extra and Side Deck slots checked after matching local artwork and historical card versions."
    )
    event = None
    for sample in review["samples"]:
        if sample["event"] != event:
            event = sample["event"]
            panel.line(sample["event_name"], "heading")
        issues = sample["failures"]
        result = f"{len(issues)} rejected" if issues else "All accepted"
        panel.line(f"{sample['deck_type']}   ·   {sample['slots']} slots   ·   {result}")
        for name, reason in dict.fromkeys((f["name"], f["reason"]) for f in issues):
            panel.line(f"{name} — {reason}", "muted")
        if sample["deck_format"] != row["name"]:
            panel.line(
                f"The event is catalogued as {row['name']}. The deck itself is tagged {sample['deck_format']}.",
                "muted",
            )
    return panel


def host_sheet(row: dict, recipe: dict, back: Callable, copy: Callable) -> Sheet:
    panel = Sheet()
    panel.action("back", "Back to format", back, icon="back")
    panel.heading(row, "How to host")
    panel.line(f"{recipe['base']}   ·   {recipe['fidelity']} historical rules", "subheading")
    panel.line(" ".join(recipe["caveats"]))
    panel.action("copy", "Copy settings", copy)
    for step in host_steps(row, recipe):
        panel.line(step)
    return panel


def manage_sheet(
    catalogue: Catalogue,
    states: dict[int, State],
    region: str,
    era: str,
    back: Callable,
    confirm: Callable,
    change: Callable,
) -> Sheet:
    panel = Sheet()
    panel.action("back", "Back to format", back, icon="back")
    panel.line("GAME INSTALLATION", "goldKicker")
    panel.line("Manage lists", "display")
    panel.line(
        "Add card pools together for the next time you start EDOPro. Your decks and personal lists stay in place."
    )

    def scope(title: str, name: str, rows: list[dict], remove: bool = False) -> None:
        choices = [
            r
            for r in rows
            if "file" in r
            and states.get(r["id"])
            in ((State.INSTALLED, State.OLD) if remove else (State.ABSENT, State.OLD))
        ]

        def start() -> None:
            if not remove or confirm(
                "Remove installed format lists?",
                f"Take these {len(choices)} lists out of the deck editor? You can add them here again.",
            ):
                change(choices, remove)

        panel.action(name, f"{title} ({len(choices)})", start).setEnabled(bool(choices))

    if era:
        scope(f"Add this era · {era_name(era)}", "era", catalogue.select(region, era))
    scope(f"Add all {region}", "region", catalogue.select(region))
    scope("Add all formats", "all", catalogue.rows)
    scope("Remove all installed lists", "removeAll", catalogue.rows, True)
    panel.line(
        f"Only {APP_NAME} lists that still match their saved contents can be updated or removed.",
        "muted",
    )
    panel.line("THIS INSTALLATION", "goldKicker")
    for region in ("TCG", "OCG"):
        dated = [r for r in catalogue.select(region) if "file" in r]
        installed = [
            r["name"] for r in dated if states.get(r["id"]) in (State.INSTALLED, State.OLD)
        ]
        panel.line(f"{region}   ·   {len(installed)} of {len(dated)} installed", "subheading")
        if installed:
            panel.line(", ".join(installed), "muted")
    panel.line(
        f"For deck building, LAN play and hosting friends with the same {APP_NAME} version. Public servers recognise their own lists.",
        "muted",
    )
    return panel
