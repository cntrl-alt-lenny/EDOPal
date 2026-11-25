"""HD library, sync controls and per-install history."""

from dataclasses import replace
from datetime import UTC, datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from edopal.core.install import RunState
from edopal.ui.widgets import PrimaryButton

from .controller import ViewState
from .gallery import BG, FG, GOLD, PANEL, SUB, Filmstrip, curated, font, showcase_rows
from .library import Run

CENTER = Qt.AlignmentFlag.AlignCenter
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
PANEL_HEIGHT = 236


def layout(kind, parent=None, gap=0):
    box = kind(parent)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(gap)
    return box


def label(text="", size=12, color=FG, heading=False):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    style_text(w, size, color, heading)
    return w


def style_text(w, size, color=FG, heading=False):
    w.setFont(font(size, heading))
    family = "Outfit" if heading else "Inter"
    w.setStyleSheet(
        f'font-family:"{family}"; font-size:{size}px; font-weight:{600 if heading else 500};'
        f"color:{color}; background:transparent; border:0;"
    )


def link(title):
    button = QPushButton(title)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        f"QPushButton {{color:{GOLD}; background:transparent; border:0; padding:2px; font-size:12px;}}"
        "QPushButton:hover, QPushButton:focus {text-decoration:underline;}"
        "QPushButton:disabled {color:#aaa9ac;}"
    )
    return button


def sync_icon():
    im = QPixmap(40, 40)
    im.fill(Qt.GlobalColor.transparent)
    with QPainter(im) as p:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(2, 2)
        p.setPen(QPen(QColor("#251f15"), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(3, 3, 14, 14), 30 * 16, 132 * 16)
        p.drawArc(QRectF(3, 3, 14, 14), 210 * 16, 132 * 16)
        for points in (((3, 3), (3, 8), (8, 8)), ((17, 17), (17, 12), (12, 12))):
            p.drawPolyline([QPointF(*a) for a in points])
    return QIcon(im)


def panel_layout(panel, title):
    box = layout(QVBoxLayout, panel)
    box.setContentsMargins(16, 12, 16, 12)
    header = layout(QHBoxLayout)
    heading = label(title, 17, FG, True)
    heading.setFixedHeight(26)
    header.addWidget(heading)
    header.addStretch()
    box.addLayout(header)
    box.addSpacing(6)
    return box, header


class Switch(QCheckBox):
    def __init__(self, title, description):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(description)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(25)
        copy = layout(QHBoxLayout, self)
        copy.setContentsMargins(0, 0, 40, 0)
        title_label = label(title, 13)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        copy.addWidget(title_label)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            x = self.width() - 32
            y = self.height() / 2
            accent = self.hasFocus() or (self.isEnabled() and self.underMouse())
            p.setPen(QPen(QColor(GOLD), 1) if accent else Qt.PenStyle.NoPen)
            on = self.isChecked()
            p.setBrush(QColor("#9d8251" if on else "#4c4c50"))
            p.drawRoundedRect(QRectF(x, y - 8, 30, 16), 8, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setOpacity(1 if self.isEnabled() else 0.55)
            p.setBrush(QColor("#fff0d1" if on else "#c8c8cb"))
            p.drawEllipse(QRectF(x + (16 if on else 3), y - 5.5, 11, 11))


class SyncPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("syncPanel")
        box, header = panel_layout(self, "Sync")
        self.undo = link("Undo Last Sync")
        effect = "Restores previous artwork and removes files added by the last sync. Keeps later edits."
        self.undo.setToolTip(effect)
        self.undo.setAccessibleDescription(effect)
        header.addWidget(self.undo)

        facts = layout(QGridLayout)
        facts.setHorizontalSpacing(8)
        facts.setColumnStretch(1, 1)
        self.counts = {}
        for i, (key, title, tip) in enumerate(
            (
                ("ready", "Ready To Sync", "Verified HD artwork is available from an enabled source and can be installed."),
                (
                    "elsewhere",
                    "Available Elsewhere Or Previously Installed",
                    "Verified HD art has been seen elsewhere or was previously installed. Sync can’t install it "
                    "with the current sources and options.",
                ),
                (
                    "pending",
                    "Awaiting HD Evidence",
                    "These cards have no verified HD source yet. Unmeasured community listings stay in this group.",
                ),
            )
        ):
            count = label("", 13, GOLD if key == "ready" else FG)
            count.setAlignment(RIGHT)
            text = label(title, 13)
            count.setToolTip(tip)
            text.setToolTip(tip)
            info = link("i")
            info.setAccessibleName(f"About {title}")
            info.setAccessibleDescription(tip)
            info.setToolTip(tip)
            info.setFixedSize(16, 16)
            info.setStyleSheet(
                "QPushButton {border:1px solid #828185;border-radius:8px;color:#c4c3c3;"
                " background:transparent;padding:0;font-size:11px;}"
                f"QPushButton:hover, QPushButton:focus {{border-color:{GOLD};color:{GOLD};}}"
            )
            info.clicked.connect(
                lambda checked=False, w=info: QToolTip.showText(
                    w.mapToGlobal(w.rect().bottomLeft()), w.toolTip(), w, w.rect()
                )
            )
            facts.setRowMinimumHeight(i, 22)
            fact = layout(QHBoxLayout, gap=7)
            fact.addWidget(text)
            fact.addWidget(info, alignment=CENTER)
            fact.addStretch()
            facts.addWidget(count, i, 0)
            facts.addLayout(fact, i, 1)
            self.counts[key] = count
        box.addLayout(facts)
        box.addSpacing(6)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background:#3b3b3e;border:0;")
        box.addWidget(rule)
        box.addSpacing(4)

        self.deck = Switch("Only Cards In My Decks", "Use cards listed in this installation’s decks.")
        self.original = Switch("Original GOAT / Pre-Errata Wording", "Prefer the original wording on historical cards.")
        self.rush = Switch("Rush Duel HD (Community)", "English Rush art and Over Rush Rare prints.")
        for switch in (self.deck, self.original, self.rush):
            box.addWidget(switch)

        credit = QLabel()
        credit.setOpenExternalLinks(True)
        credit.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByKeyboard | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        credit.setWordWrap(True)
        style_text(credit, 11, SUB)
        credit.setText(
            f'Rush art by <a style="color:{GOLD}" href="https://github.com/Yoshi80/Rush-HD-Pictures">Yoshi80</a>'
            f' · Masaki2709 · KogaDiamond1080 · <a style="color:{GOLD}" '
            'href="https://github.com/Yoshi80/Rush-HD-ORR-Extension">ORR Extension</a>'
        )
        box.addWidget(credit)
        box.addStretch()

    def display(self, state):
        for key, count in self.counts.items():
            count.setText(f"{getattr(state, key):,}" if state.total else "—")


class Metric(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("metric")
        box, _ = panel_layout(self, "HD Coverage")
        metric = layout(QVBoxLayout, gap=1)
        metric.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.percent = label("", 40, GOLD, True)
        self.summary = label("", 13)
        self.caption = label("cards with known HD art", 13, SUB)
        for w in (self.percent, self.summary, self.caption):
            w.setAlignment(CENTER)
            w.setWordWrap(True)
            metric.addWidget(w)
        box.addLayout(metric, 1)

    def display(self, s):
        self.caption.setVisible(bool(s.known))
        if s.known:
            fraction = s.hd / s.known
            # A rounded 100% can hide both missing prints and outstanding discovery.
            text = f"{fraction:.1%}"
            if text == "100.0%" and (s.pending or s.hd < s.known):
                text = f"{s.hd:,} HD" if s.hd == s.known else ">99.9%"
            self.percent.setText(text)
            self.summary.setText(f"HD installed for {s.hd:,} of {s.known:,}")
        else:
            self.percent.setText("—")
            self.summary.setText(
                {"checking": "Checking artwork…", "no-install": "Select a game folder", "error": "Coverage unavailable"}.get(
                    s.phase, "No known HD faces in this selection."
                )
            )


class Groups(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("groups")
        box, _ = panel_layout(self, "Installed HD By Group")
        self.rows = []
        for name in ("Official", "Rush Duel", "Anime/Custom"):
            group = layout(QVBoxLayout, gap=8)
            group.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            line = layout(QHBoxLayout)
            text = label(name)
            value = label("", 12, SUB)
            line.addWidget(text)
            line.addStretch()
            line.addWidget(value)
            group.addLayout(line)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            bar.setFixedHeight(3)
            bar.setAccessibleName(name)
            bar.setStyleSheet(
                f"QProgressBar {{border:0;border-radius:1px;background:#555150;}}"
                f"QProgressBar::chunk {{background:{GOLD};border-radius:1px;}}"
            )
            group.addWidget(bar)
            box.addLayout(group, 1)
            self.rows.append((text, value, bar))

    def display(self, values):
        for (text, label, bar), (hd, total) in zip(self.rows, values, strict=True):
            label.setText(f"{hd / total:.1%}" if total else "—")
            bar.setValue(round(1000 * hd / total) if total else 0)
            tip = f"{text.text()} · HD installed for {hd:,} of {total:,} cards with known HD art"
            for w in (text, label, bar):
                w.setToolTip(tip)


class History(QTableWidget):
    def __init__(self):
        super().__init__(0, 7)
        self.setHorizontalHeaderLabels(("Date", "Time", "Type", "Cards Downloaded", "Failed", "Duration", "Result"))
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setMinimumSectionSize(24)
        self.horizontalHeader().setHighlightSections(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.setAccessibleName("Sync History · times in UTC")
        for i in range(7):
            self.horizontalHeaderItem(i).setTextAlignment(RIGHT if i in (3, 4, 5) else LEFT)
        self.setStyleSheet(
            f"QTableWidget {{background:{BG};color:{FG};border:0;font-size:13px;}}"
            "QTableWidget::item {border:0;border-bottom:1px solid #363639;padding:0 8px;}"
            f"QHeaderView::section {{background:{PANEL};color:{SUB};border:0;padding:0 8px;font-size:12px;}}"
        )

    def display(self, history):
        self.setRowCount(len(history))
        for row, run in enumerate(history):
            for col, text in enumerate(run.cells()):
                item = QTableWidgetItem(("•  " if col == 6 else "") + text)
                item.setTextAlignment(RIGHT if col in (3, 4, 5) else LEFT)
                if col == 6:
                    item.setForeground(QColor("#bad1b7" if text == "Complete" else GOLD))
                self.setItem(row, col, item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.viewport().width()
        edges = [round(width * f) for f in (0, 0.155, 0.27, 0.408, 0.603, 0.705, 0.838, 1)]
        for i in range(7):
            self.setColumnWidth(i, edges[i + 1] - edges[i])


class FirstSync(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("firstSync")
        self.setMinimumHeight(64)
        box = layout(QHBoxLayout, self, 24)
        box.setContentsMargins(18, 12, 18, 12)
        copy = layout(QVBoxLayout, gap=4)
        copy.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.title = label("", 16, FG, True)
        self.description = label("", 12, SUB)
        self.description.setWordWrap(True)
        copy.addWidget(self.title)
        copy.addWidget(self.description)
        box.addLayout(copy, 2)
        self.metrics = QWidget()
        metrics = layout(QHBoxLayout, self.metrics, 20)
        self.cards = self._metric(metrics, "Cards in Game")
        self.check = self._metric(metrics, "Last Artwork Check")
        box.addWidget(self.metrics, 2)

    @staticmethod
    def _metric(row, title):
        column = layout(QVBoxLayout, gap=4)
        column.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        column.addWidget(label(title, 12, SUB))
        value = label("", 14)
        value.setWordWrap(True)
        column.addWidget(value)
        row.addLayout(column, 1)
        return value

    def display(self, state, checked):
        title, description = {
            "no-install": ("No Game Folder Selected", "Choose your EDOPro folder to see its sync history."),
            "checking": ("Checking Artwork", "Looking for installed cards and field artwork."),
            "syncing": ("First Sync in Progress", "This run will appear in Sync History when it finishes."),
        }.get(state.phase, ("No Syncs Yet", "Start a sync to record downloads and any failed transfers here."))
        self.title.setText(title)
        self.description.setText(description)
        self.metrics.setVisible(bool(state.total or checked))
        self.cards.setText(f"{state.total:,}" if state.total else "Pending")
        self.check.setText(checked or "Pending")


class HDCardPage(QWidget):
    def __init__(self, core, controller):
        super().__init__()
        self.core, self.controller = core, controller
        self._probing = False
        self._cancelling = False
        self.setObjectName("hdPage")
        self.setStyleSheet(
            f"QWidget#hdPage,QWidget#hdContent {{background:{BG};}} "
            f"QFrame#metric,QFrame#groups,QFrame#syncPanel,QFrame#firstSync {{background:{PANEL};border-radius:6px;}}"
        )
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        contents = QWidget()
        contents.setObjectName("hdContent")
        box = self.box = layout(QVBoxLayout, contents)
        scroll.setWidget(contents)
        layout(QVBoxLayout, self).addWidget(scroll)
        self.top = layout(QHBoxLayout, gap=8)
        self.title = label("HD Cards", 38, FG, True)
        self.top.addWidget(self.title)
        self.top.addStretch()
        self.failed = label("", 13, SUB)
        self.retry = link("Retry Failed")
        self.sync = PrimaryButton("Sync HD Cards")
        self.sync.setIcon(sync_icon())
        self.sync.setIconSize(QSize(16, 16))
        self.sync.setStyleSheet("padding: 0 6px; font-size:13px;")
        self.sync.setFixedSize(146, 36)
        self.top.addWidget(self.failed)
        self.top.addWidget(self.retry)
        self.top.addSpacing(10)
        self.top.addWidget(self.sync)
        box.addLayout(self.top)
        info = layout(QHBoxLayout, gap=8)
        self.status = label("", 12, "#aaaaae")
        self.new_action = link("Sync New Cards")
        self.cancel = link("Cancel")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("HD card sync progress")
        self.progress.setFixedSize(90, 5)
        for w in (self.status, self.new_action, self.progress, self.cancel):
            info.addWidget(w)
        info.addStretch()
        box.addLayout(info)
        box.addSpacing(10)
        self.panels = layout(QHBoxLayout, gap=12)
        self.headline = Metric()
        self.groups = Groups()
        self.sync_panel = SyncPanel()
        self.undo = self.sync_panel.undo
        self.deck, self.original, self.rush = self.sync_panel.deck, self.sync_panel.original, self.sync_panel.rush
        for w in (self.headline, self.groups, self.sync_panel):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            w.setFixedHeight(PANEL_HEIGHT)
            self.panels.addWidget(w)
        box.addLayout(self.panels)
        box.addSpacing(8)
        showcase = layout(QHBoxLayout, gap=20)
        self.show_title = label("HD Showcase", 22, FG, True)
        self.compare = label("Original 177 × 254    /    HD 813 × 1,185", 12, SUB)
        showcase.addWidget(self.show_title)
        showcase.addWidget(self.compare, 1)
        box.addLayout(showcase)
        box.addSpacing(5)
        self.rows = [Filmstrip(1, 1), Filmstrip(2, -1), Filmstrip(3, 1)]
        self.curated = curated()
        strips = layout(QVBoxLayout, gap=7)
        for row in self.rows:
            strips.addWidget(row)
        box.addLayout(strips)
        box.addSpacing(12)
        head = layout(QHBoxLayout)
        self.hist_title = label("Sync History", 22, FG, True)
        head.addWidget(self.hist_title)
        head.addStretch()
        self.history_clock = label("Times in UTC", 12, "#aaaaae")
        head.addWidget(self.history_clock)
        box.addLayout(head)
        box.addSpacing(4)
        self.first_sync = FirstSync()
        box.addWidget(self.first_sync, 1)
        self.table = History()
        box.addWidget(self.table, 1)
        self.sync.clicked.connect(lambda: self._request_write())
        self.new_action.clicked.connect(lambda: self._request_write(new_only=True))
        self.retry.clicked.connect(lambda: self._request_write(retry_failed=True))
        self.undo.clicked.connect(lambda: self._request_write(undo=True))
        self.cancel.clicked.connect(self._cancel)
        for w in (self.deck, self.original, self.rush):
            w.clicked.connect(
                lambda: controller.set_options(self.deck.isChecked(), self.original.isChecked(), self.rush.isChecked())
            )
        controller.subscribe(self.render_state)
        self._size()

    def _cancel(self):
        self._cancelling = True
        self.cancel.setEnabled(False)
        self.cancel.setText("Cancelling…")
        self.controller.cancel()

    def _request_write(self, new_only=False, undo=False, retry_failed=False):
        game = self.core.installs.current()
        if self._probing or not game:
            return
        self._probing = True
        self.render_state(self.state)
        service = self.core.installs

        def proceed(status):
            self._probing = False
            self.render_state(self.state)
            if game != service.current():
                return
            if status == RunState.RUNNING:
                self.core.notify("Close EDOPro before changing card artwork.", "warning")
                return
            if status == RunState.UNKNOWN:
                choice = QMessageBox.question(
                    self,
                    "Is EDOPro closed?",
                    "EDOPal could not check the game process. Is this copy of EDOPro closed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes or game != service.current():
                    return
            if undo:
                if status == RunState.UNKNOWN:
                    self.controller.undo(confirmed_closed=True)
                else:
                    self.controller.undo()
            elif retry_failed:
                self.controller.start_sync(retry_failed=True, confirmed_closed=status == RunState.UNKNOWN)
            else:
                self.controller.start_sync(new_only=new_only, confirmed_closed=status == RunState.UNKNOWN)

        def fail(exc):
            self._probing = False
            self.render_state(self.state)
            self.core.notify(str(exc), "error")

        self.core.jobs.run(lambda ctx: service.run_state(), on_done=proceed, on_error=fail)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size()

    def _size(self):
        wide = self.width() > 1350 and self.height() >= 830
        small = self.width() < 900
        mode = wide, small, self.height() < 680
        if getattr(self, "mode", None) == mode:
            return
        self.mode = mode
        pad = 36 if wide else 16 if small else 32
        self.box.setContentsMargins(pad, 22 if wide else 16, pad, 33 if wide else 18)
        weights = (27, 34, 39) if wide else (26, 25, 49) if small else (27, 27, 46)
        for i, weight in enumerate(weights):
            self.panels.setStretch(i, weight)
        style_text(self.headline.percent, 51 if wide else 40, GOLD, True)
        style_text(self.headline.summary, 16 if wide else 13)
        style_text(self.headline.caption, 16 if wide else 13, SUB)
        for text, value, bar in self.groups.rows:
            style_text(text, 15 if wide else 12)
            style_text(value, 15 if wide else 12, SUB)
            bar.setFixedHeight(4 if wide else 3)
        for r in self.rows:
            r.wide = wide
            r.setFixedHeight(137 if wide else 72 if mode[2] else 86)
        self.first_sync.setMinimumHeight(84 if wide else 64)
        style_text(self.show_title, 26 if wide else 22, FG, True)
        style_text(self.hist_title, 26 if wide else 22, FG, True)
        self.show_title.setMinimumHeight(44 if wide else 32)
        self.hist_title.setMinimumHeight(36 if wide else 29)
        style_text(self.compare, 14 if wide else 12, SUB)
        style_text(self.status, 13 if wide else 12, "#aaaaae")
        self.table.horizontalHeader().setFixedHeight(34 if wide else 26)
        self.table.verticalHeader().setDefaultSectionSize(35 if wide else 27)

    def render_state(self, state):
        prior = getattr(self, "state", None)
        self.state = state
        busy = state.phase in ("checking", "syncing", "undoing")
        can_write = not busy and state.phase != "no-install" and not self._probing
        self.sync.setEnabled(can_write and (bool(state.total) or state.phase == "error"))
        self.undo.setEnabled(can_write and state.can_undo)
        for w, checked in ((self.deck, state.deck_only), (self.original, state.original), (self.rush, state.rush)):
            w.setChecked(checked)
            w.setEnabled(not busy)
        self.headline.display(state)
        self.groups.display(state.groups)
        self.sync_panel.display(state)
        self.failed.setText(f"{len(state.failures):,} failed · retried twice ·")
        self.failed.setVisible(bool(state.failures))
        self.retry.setVisible(bool(state.failures))
        self.retry.setEnabled(can_write)
        self.new_action.setVisible(bool(state.need) and not busy)
        self.new_action.setEnabled(can_write)
        self.cancel.setVisible(state.phase == "syncing")
        self.progress.setVisible(state.phase == "syncing")
        if state.phase != "syncing":
            self._cancelling = False
        self.cancel.setEnabled(not self._cancelling)
        self.cancel.setText("Cancelling…" if self._cancelling else "Cancel")
        self.progress.setRange(0, state.count)
        self.progress.setValue(state.done)
        summary = (
            f"{state.new:,} new {'card' if state.new == 1 else 'cards'} · {state.need:,} need HD art"
            if state.new
            else "No new cards since last check"
        )
        top = f"{state.installed_fields:,} / {state.fields:,} field artworks   ·   {summary}"
        if state.phase == "no-install":
            top = "Select your EDOPro game folder to check artwork."
        elif state.phase == "done":
            top += (
                f"   ·   {'Cancelled' if state.cancelled else 'Sync complete'} · {state.downloaded:,} files downloaded"
                f" · {state.unavailable:,} unavailable" + (f" · {state.errors:,} failed" if state.errors else "")
            )
        elif state.phase in ("checking", "undoing", "error"):
            top = state.detail
        elif state.phase == "syncing":
            top = f"Syncing · {state.done:,} of {state.count:,}   ·   {state.detail}"
        elif state.detail:
            top += "   ·   " + state.detail
        checked = datetime.fromtimestamp(state.checked, UTC).strftime("%d %b %Y, %H:%M UTC") if state.checked else ""
        self.headline.setToolTip(
            "Each card counts once. Installed HD faces measure at least 400 × 580 pixels. "
            "Known HD art includes installed faces, previously observed HD and verified sources."
            + (f"\n\nLast complete measurement · {checked}" if checked else "")
        )
        if checked and state.phase in ("checking", "error"):
            top += f"   ·   Last measured {checked}"
        self.status.setText(top)
        self.status.setToolTip(top)
        if prior is None or state.pairs is not prior.pairs:
            for row, items in zip(self.rows, showcase_rows(state.pairs, self.curated), strict=True):
                row.items = items
                row.update()
            self.compare.setText(
                "Original and HD comparisons" if state.pairs else "Original 177 × 254    /    HD 813 × 1,185"
            )
        if prior is None or state.history != prior.history:
            self.table.display(state.history)
            self.table.setVisible(bool(state.history))
            self.history_clock.setVisible(bool(state.history))
            self.first_sync.setVisible(not state.history)
        if not state.history:
            self.first_sync.display(state, checked)

    def preview_state(self, name):
        sample = ViewState(
            total=25066,
            hd=20384,
            known=20668,
            ready=87,
            elsewhere=197,
            pending=4398,
            fields=593,
            installed_fields=593,
            groups=((15045, 15148), (691, 869), (4648, 4651)),
            can_undo=True,
            original=True,
            failures=frozenset((i, "face") for i in range(6)),
            history=(
                Run(1795035960, "Full", 1204, 6, 165, "6 Skipped"),
                Run(1794850920, "Deck-only", 34, 0, 12, "Complete"),
                Run(1777198740, "Retry", 4, 0, 8, "Complete"),
            ),
        )
        states = {
            "idle": sample,
            "new-cards": replace(sample, new=12, need=9),
            "syncing": replace(sample, phase="syncing", done=624, count=1207, detail="3.2 cards/s"),
            "done": replace(sample, phase="done", downloaded=1204, unavailable=3),
            "no-install": ViewState(phase="no-install"),
        }
        self.render_state(states[name])
