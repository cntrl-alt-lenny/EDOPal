"""HD Cards page and deterministic, offline states for the preview renderer."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from edopal.core.install import RunState
from edopal.ui import theme as t
from edopal.ui.widgets import PageHeader, PrimaryButton, QuietButton, page_layout

if TYPE_CHECKING:
    from edopal.core import Core
    from edopal.features.hdcards.controller import ViewState

LEFT = Qt.AlignmentFlag.AlignLeft
CENTER = Qt.AlignmentFlag.AlignCenter
NO_PEN = Qt.PenStyle.NoPen
NO_BRUSH = Qt.BrushStyle.NoBrush


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    return label


def _color(base: str, alpha: int) -> QColor:
    color = QColor(base)
    color.setAlpha(alpha)
    return color


def _icon(name: str, color: str) -> QIcon:
    """Small outline symbols that retain contrast on the gold and charcoal surfaces."""
    paths = {
        "download": [[(12, 3), (12, 15)], [(7, 10), (12, 15), (17, 10)], [(4, 15), (4, 20), (20, 20), (20, 15)]],
        "scan": [
            [(4, 9), (4, 4), (9, 4)],
            [(15, 4), (20, 4), (20, 9)],
            [(20, 15), (20, 20), (15, 20)],
            [(9, 20), (4, 20), (4, 15)],
            [(7, 12), (17, 12)],
        ],
        "spark": [[(12, 2), (14.5, 9.5), (22, 12), (14.5, 14.5), (12, 22), (9.5, 14.5), (2, 12), (9.5, 9.5), (12, 2)]],
    }
    pix = QPixmap(48, 48)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    p.setPen(QPen(QColor(color), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    for points in paths[name]:
        p.drawPolyline([QPointF(x, y) for x, y in points])
    p.end()
    return QIcon(pix)


class _Hero(QFrame):
    """A scalable, foil-engraved card fan using the app's own gold insignia."""

    def __init__(self):
        super().__init__()
        self.mark = QPixmap(str(t.ASSETS / "logo-mark.png"))
        self.title = _label("Card artwork", "heroTitle")
        self.coverage = _label("", "body")
        self.coverage.setWordWrap(True)
        self.coverage.setStyleSheet(f"color:{t.TEXT}; font-size:{t.SUBTITLE}px;")
        self.sync = PrimaryButton("Sync HD cards")
        self.sync.setIcon(_icon("download", t.ON_GOLD))
        self.sync.setIconSize(QSize(t.ICON, t.ICON))
        self.sync.setFixedHeight(t.BUTTON_H)
        self.text_box = QWidget()
        col = QVBoxLayout(self.text_box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(_label("HD COVERAGE", "goldKicker"))
        col.addSpacing(t.XS)
        col.addWidget(self.title)
        col.addSpacing(t.XS)
        col.addWidget(self.coverage)
        self.meter = QProgressBar()
        self.meter.setTextVisible(False)
        self.meter.setAccessibleName("HD artwork coverage")
        self.meter.setFixedHeight(t.XS)
        self.meter.setMaximumWidth(288)
        col.addSpacing(t.SM)
        col.addWidget(self.meter)
        col.addSpacing(t.LG)
        col.addWidget(self.sync, 0, LEFT)
        self.row = QHBoxLayout(self)
        self.row.addWidget(self.text_box, 3, Qt.AlignmentFlag.AlignVCenter)
        self.row.addStretch(2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 840
        if self.title.property("compact") != compact:
            self.title.setProperty("compact", compact)
            self.title.style().unpolish(self.title)
            self.title.style().polish(self.title)
        self.row.setContentsMargins(t.XXL, t.MD, t.XXL, t.MD)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        box = QRectF(0.5, 0.5, w - 1, h - 1)
        clip = QPainterPath()
        clip.addRoundedRect(box, t.R_PANEL, t.R_PANEL)
        p.setClipPath(clip)
        p.fillPath(clip, QColor(t.HERO_DARK))
        light = QRadialGradient(QPointF(w * 0.77, h * 0.23), w * 0.64)
        light.setColorAt(0, QColor(t.HERO_LIGHT))
        light.setColorAt(1, QColor(t.HERO_DARK))
        p.fillPath(clip, QBrush(light))
        # Fine concentric lines are printed behind the cards, like a foil security seal.
        x, y = w * 0.79, h * 0.55
        p.setBrush(NO_BRUSH)
        for radius in (0.55, 0.58, 0.84, 0.855, 1.14):
            p.setPen(QPen(_color(t.BRONZE, 42), 1))
            p.drawEllipse(QPointF(x, y), h * radius, h * radius)
        scale = min(h / 285, w / 975)
        p.translate(x, y)
        p.scale(scale, scale)
        for dx, dy, angle, front in ((-66, 10, -19, False), (-2, -5, -4, False), (55, -2, 14, True)):
            p.save()
            p.translate(dx, dy)
            p.rotate(angle)
            self._card(p, front)
            p.restore()
        p.resetTransform()
        p.setClipping(False)
        p.setBrush(NO_BRUSH)
        p.setPen(QPen(_color(t.BRONZE, 94), 1))
        p.drawPath(clip)

    def _card(self, p: QPainter, front: bool):
        rect = QRectF(-68, -96, 136, 192)
        p.setPen(NO_PEN)
        p.setBrush(_color(t.RAIL, 72))
        p.drawRoundedRect(rect.translated(3, 5), 6, 6)
        face = QLinearGradient(rect.topLeft(), rect.bottomRight())
        face.setColorAt(0, QColor(t.HERO_LIGHT if front else t.HERO_DARK))
        face.setColorAt(0.48, QColor(t.RAIL))
        face.setColorAt(1, QColor(t.HERO_LIGHT if front else t.HERO_DARK))
        p.setBrush(QBrush(face))
        p.setPen(QPen(QColor(t.BRONZE), 1.3))
        p.drawRoundedRect(rect, 6, 6)
        p.setBrush(NO_BRUSH)
        for inset, alpha in ((5, 200), (8, 90)):
            p.setPen(QPen(_color(t.GOLD, alpha), 0.7))
            p.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), 2, 2)
        # A guilloché rosette surrounds the central insignia on each printing.
        p.setPen(QPen(_color(t.BRONZE, 140 if front else 92), 0.65))
        for _ in range(24):
            p.drawEllipse(QPointF(0, -24), 15, 32)
            p.rotate(15)
        for corner in (-1, 1):
            p.save()
            p.scale(1, corner)
            p.setPen(QPen(_color(t.GOLD, 154), 0.7))
            for offset in (0, 4):
                p.drawPolyline([QPointF(-54, -76 + offset), QPointF(0, -58 + offset), QPointF(54, -76 + offset)])
            p.drawLine(QPointF(-27, -78), QPointF(27, -78))
            p.setBrush(QColor(t.GOLD))
            p.drawPolygon([QPointF(0, -77), QPointF(3, -72), QPointF(0, -67), QPointF(-3, -72)])
            p.restore()
        p.setPen(QPen(QColor(t.BRONZE), 1.1))
        p.setBrush(QColor(t.RAIL))
        p.drawEllipse(QPointF(0, 0), 30, 30)
        p.setPen(QPen(_color(t.GOLD, 150), 0.65))
        p.drawEllipse(QPointF(0, 0), 26, 26)
        p.drawPixmap(QRectF(-22, -22, 44, 44), self.mark, QRectF(self.mark.rect()))


class _Toggle(QuietButton):
    def __init__(self, title: str, detail: str):
        super().__init__("")
        self.setProperty("role", "action")
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
        self.title = _label(title, "body")
        self.detail = _label(detail, "muted")
        for label in (self.title, self.detail):
            label.setWordWrap(True)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.LG, t.MD, 64, t.MD)
        layout.setSpacing(t.XS)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(74)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.width() - 53, self.height() / 2 - 10, 37, 20)
        p.setPen(QPen(QColor(t.GOLD if self.isChecked() else t.MUTED), 1))
        p.setBrush(QColor(t.GOLD if self.isChecked() else t.EDGE))
        p.drawRoundedRect(rect, 10, 10)
        p.setPen(NO_PEN)
        p.setBrush(QColor(t.ON_GOLD if self.isChecked() else t.TEXT))
        p.drawEllipse(rect.center() + QPointF(8 if self.isChecked() else -8, 0), 6, 6)


class _Status(QFrame):
    def __init__(self):
        super().__init__()
        self.setProperty("role", "panel")
        self._symbol_name = ""
        self.symbol = QLabel()
        self.symbol.setAlignment(CENTER)
        self.symbol.setFixedSize(44, 44)
        self.symbol.setStyleSheet(f"background:{t.ACTIVE}; border-radius:11px;")
        self.head = _label("", "subheading")
        self.body = _label("", "body")
        self.body.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(5)
        content.addWidget(self.head)
        content.addWidget(self.body)
        content.addWidget(self.progress)
        self.action = QPushButton()
        self.action.setMinimumSize(80, t.BUTTON_H)
        row = QHBoxLayout(self)
        row.setContentsMargins(t.XL, t.LG, t.XL, t.LG)
        row.setSpacing(t.LG)
        row.addWidget(self.symbol)
        row.addLayout(content, 1)
        row.addWidget(self.action)

    def display(self, icon: str, title: str, body: str, action: str = "", *, progress=False):
        if self._symbol_name != icon:
            glyph = t.icon(icon, t.GOLD) if icon in ("check", "undo", "warning") else _icon(icon, t.GOLD)
            self.symbol.setPixmap(glyph.pixmap(22, 22))
            self._symbol_name = icon
        self.head.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.action.setText(action)
        self.action.setVisible(bool(action))
        self.progress.setVisible(progress)


class HDCardPage(QWidget):
    def __init__(self, core: Core, controller):
        super().__init__()
        self.core, self.controller = core, controller
        self.setObjectName("canvas")
        self._probing = False
        self.hero = _Hero()
        self.activity = _Status()
        self.notice = _Status()
        self.undo = QuietButton("Undo last sync", icon="undo")
        self.deck = _Toggle("Only cards in my decks", "Use the deck lists in your game folder.")
        self.original = _Toggle("Original GOAT / Pre-Errata wording", "Prefer the original text on historical cards.")
        self.options = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.options.setSpacing(t.MD)
        self.options.addWidget(self.deck, 1)
        self.options.addWidget(self.original, 1)
        heading = QHBoxLayout()
        heading.addWidget(_label("Sync options", "subheading"))
        heading.addStretch()
        heading.addWidget(self.undo)
        self.layout_box = page_layout(self)
        self.layout_box.addWidget(PageHeader("Card library", "HD Cards"))
        self.layout_box.addWidget(self.hero, 4)
        updates = QHBoxLayout()
        updates.setSpacing(t.MD)
        updates.addWidget(self.activity, 1)
        updates.addWidget(self.notice, 1)
        self.layout_box.addLayout(updates)
        self.layout_box.addLayout(heading)
        self.layout_box.addLayout(self.options)
        self.layout_box.addStretch(1)
        self.hero.sync.clicked.connect(lambda: self._request_write())
        self.notice.action.clicked.connect(lambda: self._request_write(new_only=True))
        self.activity.action.clicked.connect(self._cancel)
        self.undo.clicked.connect(lambda: self._request_write(undo=True))
        for switch in (self.deck, self.original):
            switch.clicked.connect(lambda: controller.set_options(self.deck.isChecked(), self.original.isChecked()))
        controller.subscribe(self.render_state)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.options.setDirection(QBoxLayout.Direction.TopToBottom if w < 680 else QBoxLayout.Direction.LeftToRight)
        self.hero.setMaximumHeight(max(180, min(410, round(h * 0.40))))

    def _cancel(self):
        self.activity.action.setEnabled(False)
        self.activity.action.setText("Cancelling…")
        self.controller.cancel()

    def _request_write(self, new_only=False, undo=False):
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
            else:
                self.controller.start_sync(new_only=new_only, confirmed_closed=status == RunState.UNKNOWN)

        def fail(exc):
            self._probing = False
            self.render_state(self.state)
            self.core.notify(str(exc), "error")

        self.core.jobs.run(lambda ctx: service.run_state(), on_done=proceed, on_error=fail)

    def render_state(self, state: ViewState):
        prior = getattr(self, "state", None)
        self.state = state
        phase = state.phase
        installed = phase != "no-install"
        busy = phase in ("checking", "syncing", "undoing")
        can_write = installed and not busy and not self._probing
        self.hero.sync.setEnabled(can_write and (state.total > 0 or phase == "error"))
        self.undo.setEnabled(can_write and state.can_undo)
        self.deck.setChecked(state.deck_only)
        self.original.setChecked(state.original)
        self.deck.setEnabled(not busy)
        self.original.setEnabled(not busy)
        if not installed:
            coverage = "Select your EDOPro game folder in the header to check artwork coverage."
        elif state.total:
            fields = f"{state.fields:,} field spell" + ("" if state.fields == 1 else "s")
            coverage = f"{state.hd:,} of {state.total:,} cards in HD · {fields}"
        elif phase == "checking":
            coverage = "Checking artwork coverage…"
        elif phase == "error":
            coverage = "Artwork coverage unavailable."
        else:
            coverage = state.detail if phase == "idle" and state.detail else "No cards found in this installation."
        self.hero.coverage.setText(coverage)
        self.hero.meter.setVisible(bool(state.total))
        if state.total:
            percent = 1000 * state.hd // state.total
            self.hero.title.setText(f"{percent / 10:g}%")
            self.hero.meter.setRange(0, state.total)
            self.hero.meter.setValue(state.hd)
        else:
            self.hero.title.setText("Card artwork")
        show_notice = (
            installed and (state.new > 0 or (phase == "idle" and state.total > 0)) and phase not in ("syncing", "checking")
        )
        self.notice.setVisible(show_notice)
        if show_notice:
            noun = "card" if state.new == 1 else "cards"
            verb = "needs" if state.need == 1 else "need"
            summary = f"{state.new:,} new {noun} · {state.need:,} {verb} HD art" if state.new else "No new cards."
            self.notice.display(
                "spark" if state.new else "scan", "New since last check", summary, "Sync" if state.need else ""
            )
            self.notice.action.setEnabled(can_write)
        undo_summary = phase == "idle" and state.total > 0 and bool(state.detail)
        self.activity.setVisible(undo_summary or phase not in ("idle", "no-install"))
        if phase == "syncing":
            cancelling = prior and prior.phase == "syncing" and not self.activity.action.isEnabled()
            count = f"{state.done:,} of {state.count:,}" if state.count else ""
            detail = " · ".join(filter(None, (count, state.detail))) or "Checking card artwork…"
            self.activity.display(
                "download", "Syncing HD cards", detail, "Cancelling…" if cancelling else "Cancel", progress=True
            )
            self.activity.action.setEnabled(not cancelling)
            self.activity.progress.setRange(0, state.count)
            self.activity.progress.setValue(state.done)
            self.activity.progress.setAccessibleName("HD card sync progress")
        elif phase == "done":
            values = [f"{state.downloaded:,} downloaded", f"{state.unavailable:,} unavailable"]
            if state.errors:
                values.append(f"{state.errors:,} failed")
            if state.shadowed:
                values.append(f"{state.shadowed:,} shadowed")
            self.activity.display(
                "check", "Sync cancelled" if state.cancelled else "Sync complete", state.detail or " · ".join(values)
            )
        elif phase == "checking":
            self.activity.display("scan", "Checking your library", state.detail or "Reading card databases and artwork.")
        elif undo_summary:
            self.activity.display("undo", "Undo complete", state.detail)
        elif phase == "undoing":
            self.activity.display("undo", "Undoing last sync", state.detail or "Restoring previous artwork.")
        elif phase == "error":
            self.activity.display("warning", "Artwork needs attention", state.detail)

    def preview_state(self, name: str):
        from edopal.features.hdcards.controller import ViewState

        baseline = ViewState(total=25_066, hd=24_812, fields=593, can_undo=True)
        states = {
            "idle": baseline,
            "new-cards": replace(baseline, new=12, need=9),
            "syncing": replace(baseline, phase="syncing", hd=24_483, done=624, count=1_207, detail="3.2 cards/s"),
            "done": replace(baseline, phase="done", hd=25_063, downloaded=1_204, unavailable=3),
            "no-install": ViewState(phase="no-install"),
        }
        self.render_state(states[name])
