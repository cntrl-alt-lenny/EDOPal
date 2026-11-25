"""HD library, sync controls and per-install history."""

from dataclasses import replace
from datetime import UTC, datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from edopal.core.install import RunState
from edopal.ui.widgets import PrimaryButton

from .controller import ViewState
from .gallery import BG, FG, GOLD, PANEL, SUB, Banner, Filmstrip, curated, font, load_banner, pix
from .library import Run

CENTER = Qt.AlignmentFlag.AlignCenter
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


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


class Switch(QCheckBox):
    def __init__(self, title, tip):
        super().__init__(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setAccessibleDescription(tip)
        self._style = (
            "QCheckBox {spacing:7px; color:#f4f3f0; font-size:12px;}"
            "QCheckBox::indicator {width:28px;height:16px;background:transparent;border:0;}"
        )

    def set_wide(self, wide):
        self.setStyleSheet(self._style.replace("12px", "15px") if wide else self._style)

    def paintEvent(self, event):
        super().paintEvent(event)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            y = self.height() / 2
            p.setPen(Qt.PenStyle.NoPen)
            on = self.isChecked()
            p.setBrush(QColor("#9d8251" if on else "#4c4c50"))
            p.drawRoundedRect(QRectF(0, y - 7.5, 28, 15), 7.5, 7.5)
            p.setBrush(QColor("#fff0d1" if on else "#c8c8cb"))
            p.drawEllipse(QRectF(15 if on else 2, y - 5.5, 11, 11))


class Metric(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("metric")
        box = layout(QVBoxLayout, self, 1)
        box.setAlignment(CENTER)
        self.percent = label("", 40, GOLD, True)
        self.summary = label("", 13)
        self.caption = label("cards with known HD art", 13, SUB)
        for w in (self.percent, self.summary, self.caption):
            w.setAlignment(CENTER)
            box.addWidget(w)

    def display(self, s):
        self.summary.setWordWrap(not s.known)
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
            self.summary.setWordWrap(True)
            self.summary.setText(
                {"checking": "Checking artwork…", "no-install": "Select a game folder", "error": "Coverage unavailable"}.get(
                    s.phase, "No known HD faces in this selection."
                )
            )


class Groups(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("groups")
        box = layout(QVBoxLayout, self, 2)
        box.setContentsMargins(17, 7, 17, 7)
        self.heading = label("Installed HD by group", 12, "#aaaaae")
        box.addWidget(self.heading)
        self.rows = []
        for name in ("Official", "Rush Duel", "Anime/Custom"):
            line = layout(QHBoxLayout)
            text = label(name)
            value = label("", 12, SUB)
            line.addWidget(text)
            line.addStretch()
            line.addWidget(value)
            box.addLayout(line)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            bar.setFixedHeight(3)
            bar.setAccessibleName(name)
            bar.setStyleSheet(
                f"QProgressBar {{border:0;border-radius:1px;background:#555150;}}"
                f"QProgressBar::chunk {{background:{GOLD};border-radius:1px;}}"
            )
            box.addWidget(bar)
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
        self.setAccessibleName("Sync History · times in UTC")
        for i in range(7):
            self.horizontalHeaderItem(i).setTextAlignment(RIGHT if i in (3, 4, 5) else LEFT)
        self.setStyleSheet(
            f"QTableWidget {{background:{BG};color:{FG};border:0;font-size:13px;}}"
            "QTableWidget::item {border:0;border-bottom:1px solid #363639;padding:0 8px;}"
            f"QHeaderView::section {{background:{PANEL};color:{SUB};border:0;padding:0 8px;font-size:12px;}}"
        )

    def display(self, history):
        self.clearSpans()
        self.setRowCount(max(1, len(history)))
        if not history:
            self.setSpan(0, 0, 1, 7)
            self.setItem(0, 0, QTableWidgetItem("Completed syncs will appear here."))
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


class HDCardPage(QWidget):
    def __init__(self, core, controller):
        super().__init__()
        self.core, self.controller = core, controller
        self._probing = False
        self._cancelling = False
        self.setObjectName("hdPage")
        self.setStyleSheet(
            f"QWidget#hdPage {{background:{BG};}} QFrame#metric,QFrame#groups {{background:{PANEL};border-radius:6px;}}"
        )
        box = self.box = layout(QVBoxLayout, self)
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
        self.banner = Banner()
        for w, weight in ((self.headline, 30), (self.groups, 34), (self.banner, 36)):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.panels.addWidget(w, weight)
        box.addLayout(self.panels)
        note = layout(QHBoxLayout, gap=6)
        self.about = link("i")
        self.about.setStyleSheet("border:1px solid #aaa9ac;border-radius:8px;color:#c4c3c3; padding:0; font-size:11px;")
        self.about.setFixedHeight(16)
        self.about.setAccessibleName("About HD coverage")
        self.about.setFixedWidth(16)
        self.about.clicked.connect(self._explain)
        self.note = label("", 12, SUB)
        note.addWidget(self.about)
        note.addWidget(self.note, 1)
        self.note.setMinimumHeight(32)
        box.addLayout(note)
        showcase = layout(QHBoxLayout, gap=20)
        self.show_title = label("HD Showcase", 22, FG, True)
        self.compare = label("Original 177 × 254    /    HD 813 × 1,185", 12, SUB)
        showcase.addWidget(self.show_title)
        showcase.addWidget(self.compare, 1)
        box.addLayout(showcase)
        box.addSpacing(5)
        self.rows = [Filmstrip(1), Filmstrip(-1)]
        self.curated = curated()
        box.addWidget(self.rows[0])
        box.addSpacing(7)
        box.addWidget(self.rows[1])
        box.addSpacing(10)
        opts = QFrame()
        opts.setObjectName("options")
        opts.setStyleSheet("QFrame#options {border-top:1px solid #363639;border-bottom:1px solid #363639;}")
        self.optbox = layout(QVBoxLayout, opts, 0)
        self.optbox.setContentsMargins(0, 3, 0, 4)
        self.options = layout(QHBoxLayout, gap=12)
        self.opt_title = label("Sync Options", 17, FG, True)
        self.deck = Switch("Only Cards in My Decks", "Use cards listed in this installation’s decks.")
        self.original = Switch("Original GOAT / Pre-Errata Wording", "Prefer the original wording on historical cards.")
        self.rush = Switch(
            "Rush Duel HD (community)", "Download English community renders, including Over Rush Rare extension prints."
        )
        self.undo = link("Undo Last Sync")
        self.effect = "Restores previous artwork and removes files added by the last sync. Keeps later edits."
        self.undo.setToolTip(self.effect)
        self.undo.setAccessibleDescription(self.effect)
        for w in (self.opt_title, self.deck, self.original, self.rush):
            self.options.addWidget(w)
        self.options.addStretch(1)
        self.options.addWidget(self.undo)
        self.optbox.addLayout(self.options)
        foot = layout(QHBoxLayout)
        self.credit = QLabel()
        self.credit.setOpenExternalLinks(True)
        self.credit.setStyleSheet(f"color:{SUB};font-size:10px;")
        self.credit.setText(
            f'Rush art by <a style="color:{GOLD}" href="https://github.com/Yoshi80/Rush-HD-Pictures">Yoshi80</a>'
            f' · Masaki2709 · KogaDiamond1080 · <a style="color:{GOLD}" '
            'href="https://github.com/Yoshi80/Rush-HD-ORR-Extension">ORR extension</a>'
        )
        self.undo_effect = label("Undo restores previous artwork and removes new files.", 10, SUB)
        foot.addWidget(self.credit)
        foot.addStretch()
        foot.addWidget(self.undo_effect)
        self.optbox.addLayout(foot)
        box.addWidget(opts)
        box.addSpacing(8)
        head = layout(QHBoxLayout)
        self.hist_title = label("Sync History", 22, FG, True)
        head.addWidget(self.hist_title)
        head.addStretch()
        head.addWidget(label("Times in UTC", 12, "#aaaaae"))
        box.addLayout(head)
        box.addSpacing(4)
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
        self._load_banner()
        self._size()

    def _load_banner(self, preview=False):
        self.core.jobs.run(
            lambda ctx: load_banner(self.core, ctx, preview),
            key="hdcards-banner",
            on_done=self.banner.display,
            on_error=lambda exc: self.banner.setToolTip("Card-of-the-day pack unavailable."),
        )

    def _explain(self):
        s = self.state
        QMessageBox.information(
            self,
            "About HD Coverage",
            f"{s.hd:,} installed · {s.known:,} known · {s.pending:,} awaiting evidence\n\n"
            "Each card counts once. Installed HD includes visible full faces at least 400 × 580 pixels. "
            "Known art includes installed faces, previously observed HD and individually verified sources. "
            "Unmeasured community listings stay pending. A failed transfer keeps its evidence.\n\n"
            f"{s.ready:,} ready to sync · {s.elsewhere:,} known elsewhere or previously installed. "
            "Source audit: 25 November 2026. Field artwork is counted separately.",
        )

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
        self.box.setContentsMargins(pad, 16 if not wide else 22, pad, 33 if wide else 24)
        h = 174 if wide else 108 if mode[2] else 125
        for w in (self.headline, self.groups, self.banner):
            w.setFixedHeight(h)
        self.panels.setStretch(0, 29 if wide else 30)
        self.panels.setStretch(2, 37 if wide else 35)
        style_text(self.headline.percent, 51 if wide else 40, GOLD, True)
        style_text(self.headline.summary, 16 if wide else 13)
        style_text(self.headline.caption, 16 if wide else 13, SUB)
        for text, value, bar in self.groups.rows:
            style_text(text, 15 if wide else 12)
            style_text(value, 15 if wide else 12, SUB)
            bar.setFixedHeight(4 if wide else 3)
        for r in self.rows:
            r.wide = wide
            r.setFixedHeight(145 if wide else 72 if mode[2] else 90)
        style_text(self.show_title, 26 if wide else 22, FG, True)
        style_text(self.hist_title, 26 if wide else 22, FG, True)
        self.note.setFixedHeight(43 if wide else 32)
        self.show_title.setMinimumHeight(44 if wide else 33)
        self.hist_title.setMinimumHeight(36 if wide else 30)
        self.opt_title.setVisible(not small)
        self.options.setSpacing(42 if wide else 5 if small else 25)
        self.opt_title.setFixedWidth(143 if wide else 105)
        for switch in (self.deck, self.original, self.rush):
            switch.set_wide(wide)
        style_text(self.opt_title, 20 if wide else 17, FG, True)
        style_text(self.groups.heading, 14 if wide else 12, "#aaaaae")
        style_text(self.compare, 14 if wide else 12, SUB)
        style_text(self.note, 14 if wide else 12, SUB)
        style_text(self.status, 13 if wide else 12, "#aaaaae")
        self.optbox.setContentsMargins(0, 10 if wide else 4, 0, 8 if wide else 4)
        self.table.horizontalHeader().setFixedHeight(34 if wide else 26)
        self.table.verticalHeader().setDefaultSectionSize(49 if wide else 39)

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
        if checked:
            self.headline.setToolTip(f"Last complete measurement · {checked}")
            if state.phase in ("checking", "error"):
                top += f"   ·   Last measured {checked}"
        self.status.setText(top)
        self.status.setToolTip(top)
        self.note.setText(
            f"{state.ready:,} ready to sync   ·   {state.elsewhere:,} available elsewhere or previously installed"
            f"   ·   {state.pending:,} awaiting HD evidence"
            if state.total
            else "Coverage includes installed HD faces and cards with verified HD artwork."
        )
        if prior is None or state.pairs is not prior.pairs:
            items = [(name, pix(a), pix(b)) for name, a, b in state.pairs] or self.curated
            for r in self.rows:
                r.items = items
                r.update()
            self.compare.setText(
                "Original and HD from your synced cards" if state.pairs else "Original 177 × 254    /    HD 813 × 1,185"
            )
        if prior is None or state.history != prior.history:
            self.table.display(state.history)

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
        self._load_banner(preview=True)
