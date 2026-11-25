"""Plugin settings. Opening this page only reads the selected installation."""

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label, rule
from edopal.ui.widgets import PrimaryButton, QuietButton

from . import HOW_TO, get_runtime
from .overlay import dock_rect
from .setup import PLUGIN_ID


class DuelPreview(QWidget):
    def __init__(self, tracker):
        super().__init__()
        assets = Path(__file__).parent
        self.tracker = tracker
        self.board = QPixmap(str(assets / "board.webp"))
        self.panel = QPixmap(str(assets / "panel.webp"))
        self.pill = QPixmap(str(assets / "pill.webp"))
        self.setAccessibleName("In-game overlay preview")
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tracker.changed.connect(self.update)

    def paintEvent(self, _event):
        if self.board.isNull():
            return
        size = self.board.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRect((self.width() - size.width()) // 2, 0, size.width(), size.height())
        hud = self.tracker.overlay
        collapsed = hud.panel.collapsed
        box = dock_rect(self.board.rect(), hud.side, collapsed, self.panel.height())
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.translate(rect.topLeft())
            painter.scale(size.width() / self.board.width(), size.height() / self.board.height())
            painter.drawPixmap(self.board.rect(), self.board)
            painter.drawPixmap(box, self.pill if collapsed else self.panel)


class TrackerPage(ShellPage):
    def __init__(self, core):
        super().__init__("Deck Tracker")
        self.core = core
        self.tracker = get_runtime(core)
        intro = label(HOW_TO, wrap=True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setText(HOW_TO.replace("EDOPal Relay", "<b>EDOPal Relay</b>"))
        self.body.addWidget(intro)
        self.body.addWidget(rule())
        middle = QHBoxLayout()
        middle.setSpacing(24)
        self.body.addLayout(middle, 1)
        preview = QVBoxLayout()
        preview.addWidget(label("In-Game Preview", "heading"))
        self.preview = DuelPreview(self.tracker)
        preview.addWidget(self.preview, 1)
        middle.addLayout(preview, 1)
        settings = QFrame()
        settings.setFixedWidth(292)
        controls = QVBoxLayout(settings)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(12)
        middle.addWidget(settings)
        controls.addWidget(label("Overlay", "heading"))
        controls.addWidget(label("Dock to Edge", "small"))
        self.side = QComboBox()
        self.side.setAccessibleName("Dock to edge")
        self.side.addItems(["Right", "Left"])
        self.side.setCurrentIndex(int(core.settings.get(PLUGIN_ID, "side") == "left"))
        self.side.currentTextChanged.connect(lambda text: self.tracker.option("side", text.lower()))
        controls.addWidget(self.side)
        self.click = QCheckBox("Pass Clicks to the Game")
        self.click.setChecked(bool(core.settings.get(PLUGIN_ID, "passthrough") or self.tracker.overlay.gamescope))
        self.click.setEnabled(not self.tracker.overlay.gamescope)
        self.click.toggled.connect(lambda v: self.tracker.option("passthrough", v))
        controls.addWidget(self.click)
        self.pill = QCheckBox("Collapse to Pill")
        self.pill.toggled.connect(lambda v: self.tracker.option("collapsed", v))
        controls.addWidget(self.pill)
        controls.addWidget(rule())
        controls.addWidget(label("Duel Server", "heading"))
        self.server = QComboBox()
        self.server.setAccessibleName("Duel server for EDOPal Relay")
        controls.addWidget(self.server)
        controls.addWidget(label("Launch EDOPro from EDOPal to start tracking online 1v1 duels.", "small", wrap=True))
        self.action = PrimaryButton("Set Up Relay")
        self.action.clicked.connect(lambda: self.tracker.prepare(server=self.server.currentText()))
        self.undo = QuietButton("Undo Setup", icon="undo")
        self.undo.clicked.connect(lambda: self.tracker.undo())
        buttons = QHBoxLayout()
        buttons.addWidget(self.action)
        buttons.addWidget(self.undo)
        buttons.addStretch()
        controls.addLayout(buttons)
        self.status = label("", "small", wrap=True)
        self.status.setStyleSheet(f"color:{t.GOLD};")
        controls.addWidget(self.status)
        controls.addStretch()
        self.server.currentIndexChanged.connect(self._controls)
        self.tracker.changed.connect(self._refresh)
        self.tracker.inspect()
        self._refresh()

    def _refresh(self):
        choices, chosen, _, _ = self.tracker.info
        if choices != getattr(self, "_choices", None):
            self._choices = choices
            self.server.blockSignals(True)
            self.server.clear()
            for server in choices:
                self.server.addItem(server.name, server)
            if chosen in choices:
                self.server.setCurrentIndex(choices.index(chosen))
            self.server.blockSignals(False)
        self.pill.blockSignals(True)
        self.pill.setChecked(self.tracker.overlay.panel.collapsed)
        self.pill.blockSignals(False)
        self._controls()

    def _controls(self):
        tracker = self.tracker
        choices, _, current, can_undo = tracker.info
        active = tracker.busy or bool(tracker.service)
        self.server.setEnabled(bool(choices) and not active)
        self.action.setText("Apply Server" if current else "Set Up Relay")
        self.action.setVisible(not current or self.server.currentData() != current)
        self.action.setEnabled(bool(choices) and not active)
        self.undo.setVisible(can_undo)
        self.undo.setEnabled(not active)
        message = tracker.message
        if not message:
            if current:
                message = "Ready for launch"
            elif choices:
                message = "Setup adds EDOPal Relay to your server list. You can undo it here."
            else:
                message = "Select an EDOPro installation with online duel servers."
        self.status.setText(message)
