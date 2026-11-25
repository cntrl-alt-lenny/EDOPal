"""The full tracker panel and its desktop companion window."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from edopal.core.install import RunState
from edopal.core.storage import GameRunningError
from edopal.features.formats.deck import read_ydk
from edopal.features.formats.media import _face, _image
from edopal.features.hdcards.catalog import read_catalog
from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label, rule
from edopal.ui.widgets import PrimaryButton, QuietButton

from .model import DeckState
from .relay import RelayService, bind_loopback
from .setup import PLUGIN_ID, install_entry, servers


@dataclass
class Library:
    names: dict
    types: dict
    decks: dict
    deck_names: dict
    recent: str
    servers: list
    can_undo: bool


def _library(ctx, install, storage):
    catalog = read_catalog(install, cancelled=lambda: ctx.cancelled)
    decks = {}
    for path in sorted(install.path("deck").rglob("*.ydk")):
        if ctx.cancelled:
            break
        try:
            main, _, _ = read_ydk(path.read_bytes())
            decks[path.relative_to(install.path("deck")).with_suffix("").as_posix()] = tuple(map(int, main))
        except (OSError, ValueError):
            continue
    deck_names = {}
    for name, main_cards in decks.items():
        key = frozenset(Counter(main_cards).items())
        deck_names[key] = name if key not in deck_names else "Main Deck"
    recent = ""
    conf = install.path("config/system.conf")
    if conf.exists():
        for line in conf.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "lastdeck":
                recent = value.strip()
    return Library(
        {i: c.name for i, c in catalog.cards.items()},
        {i: c.type for i, c in catalog.cards.items()},
        decks,
        deck_names,
        recent,
        servers(install),
        bool(storage.last_receipt(install, PLUGIN_ID)),
    )


def _percent(value):
    if value is None:
        return "—"
    return f"{100 * value:.1f}".removesuffix(".0") + "%"


class DeckView(QWidget):
    def __init__(self, compact=False):
        super().__init__()
        self.compact = compact
        self.state = None
        self.names = {}
        self.icons = {}
        self.columns = 1
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        line = QHBoxLayout()
        self.name = label("Main Deck", "heading")
        self.name.setWordWrap(True)
        line.addWidget(self.name, 1)
        line.addWidget(label("Draws", "small"))
        self.draws = QSpinBox()
        self.draws.setRange(1, 60)
        self.draws.setValue(3)
        self.draws.setAccessibleName("Consecutive draws")
        self.draws.setFixedWidth(64)
        self.draws.valueChanged.connect(self.render)
        line.addWidget(self.draws)
        self.body.addLayout(line)
        self.summary = label("", "small")
        self.summary.setStyleSheet(f"font-size: 14px; color: {t.GOLD};")
        self.body.addWidget(self.summary)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(60 if compact else 76, 32 if compact else 40))
        self.table.verticalHeader().setDefaultSectionSize(40 if compact else 51)
        self.table.verticalHeader().hide()
        self.table.setAccessibleName("Remaining Main Deck cards and draw probabilities")
        self.table.setStyleSheet(f"""
            QTableWidget {{background:{t.CANVAS}; alternate-background-color:{t.PANEL}; border:0; font-size:15px;}}
            QTableWidget::item {{padding:3px 8px;}}
            QHeaderView::section {{background:{t.RAIL}; color:{t.MUTED}; border:0;
                padding:7px 8px; font-size:12px;}}
        """)
        self.body.addWidget(self.table, 1)
        self.footer = label("", "small")
        self.body.addWidget(self.footer)

    def show_state(self, state, name, names, icons, *, preview=False, status=""):
        self.state, self.names, self.icons = state, names, icons
        self.name.setText(name)
        if state is None:
            self.summary.setText(status or "Waiting for a submitted Main Deck")
        else:
            order = "Shuffled draw odds" if state.random_order else "Odds unknown · private order"
            if state.known:
                order = "Known top card" if 0 in state.known else "Known card positions"
                if not state.random_order:
                    order += " · other odds unknown"
            prefix = "Deck preview   ·   " if preview else ""
            self.summary.setText(f"{prefix}{state.total} cards left   ·   {order}")
        self.table.setVisible(state is not None)
        self.draws.setEnabled(state is not None)
        self.footer.setVisible(state is not None)
        self.render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 2 if not self.compact and self.width() >= 930 else 1
        if columns != self.columns:
            self.columns = columns
            self.render()

    def render(self):
        state = self.state
        if state is None:
            self.table.setRowCount(0)
            return
        n = self.draws.value()
        codes = sorted(state.remaining, key=lambda c: (-state.remaining[c], self.names.get(c, str(c)).casefold()))
        groups = self.columns
        rows = ceil(len(codes) / groups)
        self.table.clearContents()
        self.table.setColumnCount(groups * 4)
        self.table.setRowCount(rows)
        self.table.setHorizontalHeaderLabels(["Card", "Left", "Next", f"In {n}"] * groups)
        header = self.table.horizontalHeader()
        for i in range(groups * 4):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed if i % 4 else QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeaderItem(i).setTextAlignment(
                Qt.AlignmentFlag.AlignCenter if i % 4 else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            if i % 4:
                header.resizeSection(i, (44, 77, 77)[i % 4 - 1])
        for index, code in enumerate(codes):
            name = self.names.get(code, f"Card {code}")
            values = (
                name,
                str(state.remaining[code]),
                _percent(state.probability(code)),
                _percent(state.probability(code, n)),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if col == 2:
                        item.setForeground(QColor(t.GOLD))
                else:
                    item.setToolTip(name)
                    if icon := self.icons.get(code):
                        item.setIcon(icon)
                self.table.setItem(index % rows, 4 * (index // rows) + col, item)
        self.footer.setText(f"{len(codes)} card names   ·   In {n} draws = at least one copy")


class TrackerPage(ShellPage):
    def __init__(self, core):
        super().__init__("Deck Tracker")
        self.core = core
        self.install = self.library = self.service = self.snapshot = None
        self.icons = {}
        self.pending_art = set()
        self.busy = False
        self.preview = None
        self.generation = 0
        self.compact = QDialog(self, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.compact.setWindowTitle("EDOPal · Deck Tracker")
        self.compact.resize(490, 720)
        self.compact.setMinimumWidth(365)
        self.compact_view = DeckView(compact=True)
        QVBoxLayout(self.compact).addWidget(self.compact_view)
        pin = QuietButton("Compact Window", icon="external")
        pin.clicked.connect(self._open_compact)
        self.title_row.addWidget(pin)
        bar = QHBoxLayout()
        bar.addWidget(label("Duel Server", "small"))
        self.server_box = QComboBox()
        self.server_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.server_box.setMaximumWidth(320)
        self.server_box.setAccessibleName("Upstream duel server")
        bar.addWidget(self.server_box)
        self.start = PrimaryButton("Start Relay")
        self.start.clicked.connect(self._toggle)
        bar.addWidget(self.start)
        self.undo = QuietButton("Undo Setup", icon="undo")
        self.undo.clicked.connect(self._undo)
        bar.addWidget(self.undo)
        bar.addSpacing(8)
        self.body.addLayout(bar)
        self.status = label("", "small", wrap=True)
        bar.addWidget(self.status, 1)
        self.body.addWidget(rule())
        self.view = DeckView()
        self.body.addWidget(self.view, 1)
        self.view.draws.valueChanged.connect(self.compact_view.draws.setValue)
        self.compact_view.draws.valueChanged.connect(self.view.draws.setValue)
        self.timer = QTimer(self, interval=150)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._change_install(core.installs.current())
        self._display()

    def _open_compact(self):
        self.compact.show()
        self.compact.raise_()

    def _controls(self):
        active = self.service is not None
        self.start.setText("Stop Relay" if active else "Start Relay")
        self.start.setEnabled(not self.busy and (active or self.server_box.count() > 0))
        self.server_box.setEnabled(not active and not self.busy)
        self.undo.setVisible(bool(self.library and self.library.can_undo))
        self.undo.setEnabled(not active and not self.busy)

    def _change_install(self, install):
        if self.service:
            self.service.stop()
        self.generation += 1
        self.busy = False
        self.install = install
        self.snapshot = self.library = self.service = self.preview = None
        self.icons = {}
        self.pending_art = set()
        self.server_box.clear()
        self._controls()
        self._display()
        if not install:
            self.status.setText("Select an EDOPro installation to set up the relay.")
            return
        self.status.setText("Reading decks and duel servers…")

        def loaded(lib):
            self.library = lib
            for server in lib.servers:
                self.server_box.addItem(server.name, server)
            if lib.decks:
                name = lib.recent if lib.recent in lib.decks else next(iter(lib.decks))
                self.preview = (name, DeckState.start(lib.decks[name]))
            self.status.setText(
                "Start the relay, then choose EDOPal Relay in EDOPro’s Online lobby."
                if lib.servers
                else "No duel servers found in this installation’s configuration."
            )
            self._controls()
            self._display()

        storage = self.core.storage
        self._job(lambda ctx: _library(ctx, install, storage), loaded)

    def _job(self, fn, done, *, failed=None, discard=None):
        generation = self.generation

        def finished(value):
            if isValid(self) and generation == self.generation:
                done(value)
            elif discard:
                discard(value)

        def error(exc):
            if isValid(self) and generation == self.generation:
                self.busy = False
                self._controls()
                if failed:
                    failed(exc)
                else:
                    self.status.setText(str(exc))

        self.core.jobs.run(fn, on_done=finished, on_error=error)

    def _toggle(self):
        if self.service:
            if (
                self.service.latest[0].startswith("Listening")
                or QMessageBox.question(self, "Stop Relay", "Stopping the relay disconnects any duel using it. Stop now?")
                == QMessageBox.StandardButton.Yes
            ):
                self.service.stop()
                self.busy = True
                self._controls()
        else:
            self._start()

    def _start(self, confirmed=False):
        server = self.server_box.currentData()
        if not server or self.busy:
            return
        self.busy = True
        self._controls()
        self.status.setText("Starting the relay…")
        storage, install = self.core.storage, self.install

        def prepare(_ctx):
            sock = bind_loopback()
            try:
                install_entry(storage, install, server, sock.getsockname()[1], confirmed_closed=confirmed)
                return sock, bool(storage.last_receipt(install, PLUGIN_ID))
            except BaseException:
                sock.close()
                raise

        def started(result):
            sock, can_undo = result
            self.busy = False
            self.service = RelayService()
            self.service.start(sock, server, self.library.types)
            self.destroyed.connect(self.service.stop)
            QApplication.instance().aboutToQuit.connect(self.service.stop)
            self.library.can_undo = can_undo
            self._controls()
            self.snapshot = None
            self._tick()

        self._job(
            prepare,
            started,
            failed=lambda exc: self._setup_error(exc, confirmed, self._start),
            discard=lambda result: result[0].close(),
        )

    def _setup_error(self, exc, confirmed, retry):
        if isinstance(exc, GameRunningError) and not confirmed and self.core.installs.run_state() == RunState.UNKNOWN:
            if QMessageBox.question(self, "Confirm EDOPro Is Closed", "Is EDOPro closed for this configuration change?") == (
                QMessageBox.StandardButton.Yes
            ):
                retry(True)
                return
        self.status.setText(str(exc))

    def _undo(self, confirmed=False):
        self.busy = True
        self._controls()
        storage, install = self.core.storage, self.install

        def revert(_ctx):
            result = storage.undo_last(install, PLUGIN_ID, confirmed_closed=confirmed)
            return result, bool(storage.last_receipt(install, PLUGIN_ID))

        def done(outcome):
            result, can_undo = outcome
            self.busy = False
            self.library.can_undo = can_undo
            self._controls()
            self.status.setText(
                "Configuration changed since setup. Undo skipped the edited file."
                if result.conflicts
                else "Previous server configuration restored."
            )

        self._job(revert, done, failed=lambda exc: self._setup_error(exc, confirmed, self._undo))

    def _tick(self):
        install = self.core.installs.current()
        if install != self.install:
            self._change_install(install)
        if not self.service:
            return
        if not self.service.thread.is_alive():
            status = "Relay stopped" if self.service.stop_requested.is_set() else self.service.latest[0]
            self.service = self.snapshot = None
            self.busy = False
            self.status.setText(status)
            self._controls()
            self._display()
            return
        latest = self.service.latest
        if latest is not self.snapshot:
            self.snapshot = latest
            self.status.setText(latest[0])
            self._display()

    def _display(self):
        preview = self.snapshot is None and self.preview is not None
        names = self.library.names if self.library else {}
        if preview:
            name, state = self.preview
        else:
            state = self.snapshot[1] if self.snapshot else None
            name = "Main Deck"
            if state and self.library:
                name = self.library.deck_names.get(frozenset(state.starting.items()), "Main Deck")
        for view in (self.view, self.compact_view):
            view.show_state(
                state, name, names, self.icons, preview=preview, status=self.snapshot[0] if self.snapshot else ""
            )
        if state:
            self._load_art(set(state.remaining) - self.pending_art)

    def _load_art(self, codes):
        if not codes or not self.install:
            return
        self.pending_art.update(codes)
        install = self.install

        def load(ctx):
            return {code: _image(_face(install, code), (160, 90), crop=True) for code in codes if not ctx.cancelled}

        def loaded(thumbs):
            for code, data in thumbs.items():
                pixmap = QPixmap()
                if data and pixmap.loadFromData(data):
                    size = QSize(152, 80)
                    strip = pixmap.scaled(
                        size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
                    )
                    self.icons[code] = QIcon(strip.copy((strip.width() - 152) // 2, (strip.height() - 80) // 2, 152, 80))
            for view in (self.view, self.compact_view):
                view.render()

        self._job(load, loaded)
