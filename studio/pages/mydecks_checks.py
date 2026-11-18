"""Inline format checks and section-aware version comparisons."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from html import escape

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextBrowser,
    QVBoxLayout,
)

from decks import Library, SavedDeck, Verdict, compare, preflight, version_groups
from studio import theme as t
from studio.icons import icon
from studio.pages.mydecks_logic import (
    change_lines,
    deck_title,
    finding_text,
    folder_name,
    list_names,
    quantity,
)
from studio.pages.mydecks_widgets import Contents, Screen, choice, paint_text
from studio.tasks import Job, TaskRunner, WorkerContext
from studio.widgets import Hero, SectionHeader, label

STATUS = {"legal": t.GREEN, "illegal": t.RED, "could not verify": t.GOLD}


class VerdictRow(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(t.TILE_LARGE, t.HUGE + t.LG)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        name, folder, status, findings = index.data(Qt.ItemDataRole.UserRole)
        p.save()
        r = option.rect
        active = option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        p.fillRect(r, QColor(t.ACTIVE if active else t.PANEL))
        p.setPen(QColor(t.EDGE))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        x, y = r.left() + t.LG, r.top() + t.SM
        width = r.width() - t.RAIL_WIDTH - t.XXL
        paint_text(p, QRect(x, y, width, t.XL), name, t.INK, t.SUBTITLE, True)
        paint_text(p, QRect(x, y + t.XL, width, t.XL), folder, t.MUTED, t.SMALL)
        x = r.right() - t.RAIL_WIDTH
        icon("check" if status == "legal" else "warning", STATUS[status]).paint(
            p, x, y, t.ICON, t.ICON
        )
        x += t.XXL
        paint_text(
            p, QRect(x, y, t.TILE_LARGE, t.XL), status.capitalize(), STATUS[status], t.BODY, True
        )
        paint_text(
            p,
            QRect(x, y + t.XL, t.TILE_LARGE, t.XL),
            quantity(findings, "finding"),
            t.MUTED,
            t.SMALL,
        )
        icon("chevron", t.MUTED).paint(p, r.right() - t.XL, y + t.MD, t.LG, t.LG)
        p.restore()


class ReadingPane(QTextBrowser):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.setAccessibleName(name)
        self.setMinimumHeight(t.TILE_H)
        self.setOpenLinks(False)
        self.setTabChangesFocus(True)
        self.setStyleSheet(
            f"QTextBrowser {{ background: {t.PANEL}; border: 0; }}"
            f"QTextBrowser:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}"
        )
        self.document().setDefaultStyleSheet(
            f"p {{ color:{t.TEXT}; margin: {t.SM}px 0 {t.LG}px 0; }}"
            f"h3 {{ color:{t.GOLD}; font-size:{t.SUBTITLE}px; }}"
        )


class FormatCheck(Screen):
    def __init__(
        self,
        lib: Library,
        runner: TaskRunner,
        back: Callable,
        open_findings: Callable,
        deck: SavedDeck | None,
        selected: int = 0,
    ) -> None:
        super().__init__("Check a format", back)
        self.lib, self.deck = lib, deck
        self.runner, self.open_findings = runner, open_findings
        self.generation = 0
        self.job: Job | None = None
        self.verdicts: list[Verdict] = []
        if not lib.banlists:
            hero = Hero(
                "Forbidden lists",
                "No forbidden lists installed",
                "Open EDOPro to update its lists, then refresh your deck library.",
                "decks",
            )
            hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.summary = hero.headline
            self.body.addWidget(hero, 1)
            return
        self.body.addWidget(
            label(
                "Check standard deck sizes and card limits against a list installed in your game.",
                "body",
                True,
            )
        )
        panel = self.panel()
        top = QHBoxLayout()
        top.addWidget(label("Forbidden list", "subheading"))
        self.lists = choice("Installed forbidden list", list_names(lib))
        top.addWidget(self.lists, 1)
        self.filter = choice(
            "Show verdicts", ("All verdicts", "Legal", "Illegal", "Could not verify")
        )
        self.filter.setFixedWidth(t.TILE_LARGE + t.LG)
        self.filter.setVisible(deck is None)
        top.addWidget(self.filter)
        panel.body.addLayout(top)
        self.summary = label("", "subheading", True)
        panel.body.addWidget(self.summary)
        self.results = QListWidget()
        self.results.setAccessibleName("Deck verdicts. Open a deck to read its findings.")
        self.results.setObjectName("installList")
        self.results.setMinimumHeight(t.TILE_H)
        self.results.setMouseTracking(True)
        self.results.setItemDelegate(VerdictRow(self.results))
        self.details = ReadingPane("Findings")
        panel.body.addWidget(self.results, 1)
        panel.body.addWidget(self.details, 1)
        panel.body.addWidget(
            label("Room rules can change which cards you may play.", "muted", True)
        )
        self.results.setVisible(deck is None)
        self.details.setVisible(deck is not None)
        if deck and deck.preview:
            self.body.setStretchFactor(panel, 1)
            self.contents = Contents(runner, lib.game, deck.preview)
            self.body.addWidget(self.contents, 2)
        self.results.itemClicked.connect(self._open)
        self.results.itemActivated.connect(self._open)
        self.filter.currentIndexChanged.connect(self._filter)
        self.lists.setCurrentIndex(selected)
        self.lists.currentIndexChanged.connect(self.check)
        self.initial = self.lists
        self.check(self.lists.currentIndex())

    def _open(self, item: QListWidgetItem) -> None:
        row = self.results.row(item)
        if self.isVisible() and row >= 0:
            self.open_findings(self.lib.decks[row], self.lists.currentIndex())

    def _filter(self) -> None:
        selected = self.filter.currentText().lower()
        for i, verdict in enumerate(self.verdicts[: self.results.count()]):
            self.results.item(i).setHidden(
                self.filter.currentIndex() > 0 and verdict.status != selected
            )

    def check(self, index: int) -> None:
        self.generation += 1
        if self.job and self.job.active:
            self.job.cancel()
        generation = self.generation
        self.results.clear()
        self.details.clear()
        self.verdicts = []
        self.summary.setText("Checking decks…")
        lib, banlist = self.lib, self.lib.banlists[index]
        decks = (self.deck,) if self.deck else lib.decks

        def work(worker: WorkerContext) -> list[Verdict]:
            return [preflight(d, banlist, lib.catalog) for d in decks if not worker.cancel.is_set()]

        self.job = job = self.runner.start(work)

        def ready(verdicts: list[Verdict]) -> None:
            if generation != self.generation or len(verdicts) != len(decks):
                return
            self.verdicts = verdicts
            if self.deck and verdicts:
                verdict = verdicts[0]
                self.summary.setText(f"{verdict.status.capitalize()}  ·  {self.deck.name}")
                self.summary.setStyleSheet(f"color: {STATUS[verdict.status]};")
                self.details.setHtml(
                    "".join(
                        f"<p><b style='color:{t.GOLD if f.uncertain else t.RED}'>"
                        f"{'Needs verification' if f.uncertain else 'Rule violation'}</b><br>"
                        f"{escape(finding_text(f))}</p>"
                        for f in verdict.findings
                    )
                    or "<p>This deck passes all checks for the selected list.</p>"
                )
            else:
                counts = Counter(v.status for v in verdicts)
                self.summary.setText("   ·   ".join(f"{counts[s]} {s}" for s in STATUS))
                for d, v in zip(decks, verdicts, strict=True):
                    item = QListWidgetItem(
                        f"{deck_title(d)}\n{v.status.capitalize()}   ·   "
                        f"{quantity(len(v.findings), 'finding')}"
                    )
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        (d.name, folder_name(d), v.status, len(v.findings)),
                    )
                    item.setToolTip(str(d.path))
                    self.results.addItem(item)
                self._filter()

        job.result.connect(ready)
        job.error.connect(
            lambda exc: self.summary.setText(str(exc)) if self.generation == generation else None
        )

    def leave(self) -> None:
        self.generation += 1
        if self.job and self.job.active:
            self.job.cancel()


class Comparison(Screen):
    def __init__(self, lib: Library, runner: TaskRunner, back: Callable) -> None:
        super().__init__("Compare versions", back)
        self.body.addWidget(label("Read changes from the first deck to the second.", "body"))
        self.decks = tuple(sorted((d for d in lib.decks if d.preview), key=deck_title))
        self.live = True
        names = [deck_title(d) for d in self.decks]
        panel = self.panel()
        columns = QHBoxLayout()
        self.before = choice("First deck", names)
        self.after = choice("Second deck", names)
        for title, box in (("First deck", self.before), ("Second deck", self.after)):
            col = QVBoxLayout()
            col.addWidget(label(title, "muted"))
            col.addWidget(box)
            columns.addLayout(col, 1)
        panel.body.addLayout(columns)
        self.hint = label("", "muted", True)
        panel.body.addWidget(self.hint)
        panel.body.addWidget(SectionHeader("Card changes"))
        self.report = ReadingPane("Card changes")
        panel.body.addWidget(self.report, 1)
        self.after.setCurrentIndex(1)
        self.initial = self.before
        self.before.currentIndexChanged.connect(self.update_diff)
        self.after.currentIndexChanged.connect(self.update_diff)
        self.update_diff()
        decks = self.decks
        job = runner.start(lambda _: version_groups(decks))
        self.touched = False
        self.before.activated.connect(self._touched)
        self.after.activated.connect(self._touched)
        job.result.connect(self._suggest)

    def _touched(self) -> None:
        self.touched = True

    def _suggest(self, groups: tuple[tuple[SavedDeck, ...], ...]) -> None:
        if self.live and groups and not self.touched:
            first, second = groups[0][:2]
            self.before.setCurrentIndex(self.decks.index(first))
            self.after.setCurrentIndex(self.decks.index(second))
            self.hint.setText("Suggested pair with a similar name and card selection.")

    def update_diff(self) -> None:
        self.hint.clear()
        a, b = self.before.currentIndex(), self.after.currentIndex()
        if a < 0 or b < 0 or a == b:
            self.report.setPlainText("Choose two different decks to see their changes.")
            return
        before, after = self.decks[a].preview, self.decks[b].preview
        changes = compare(before.sections, after.sections)
        lines = change_lines(before, after, changes)
        html = []
        for kind, heading in (
            ("moved", "Moved between sections"),
            ("added", "Added"),
            ("removed", "Removed"),
            ("count changed", "Changed in number"),
        ):
            group = [line for c, line in zip(changes, lines, strict=True) if c.kind == kind]
            if group:
                html.append(f"<h3>{heading}</h3>" + "".join(f"<p>{escape(s)}</p>" for s in group))
        self.report.setHtml(
            "".join(html) or "<p>The same cards appear in the same quantities in each section.</p>"
        )

    def leave(self) -> None:
        self.live = False
