"""Shared click-to-open card details. Anchors use global screen coordinates."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class CardDetail:
    code: int
    name: str
    kind: str
    text: str
    image: Path | None
    facts: tuple[tuple[str, str], ...] = ()


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    label.setWordWrap(True)
    return label


class _CardPopup(QFrame):
    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and not self.frameGeometry().contains(
            event.globalPosition().toPoint()
        ):
            self.close()
            return True
        return super().eventFilter(watched, event)


def show_card_detail(parent: QWidget, detail: CardDetail, anchor: QRect | None = None) -> QWidget:
    """Open beside a global anchor (the parent widget by default)."""
    if anchor is None:
        anchor = QRect(parent.mapToGlobal(QPoint()), parent.size())
    screen = QGuiApplication.screenAt(anchor.center()) or parent.screen()
    bounds = screen.availableGeometry()
    popup = _CardPopup(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
    popup.setObjectName("cardDetail")
    popup.setProperty("role", "panel")
    popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    popup.setAccessibleName(detail.name)
    popup.setWindowTitle(detail.name)
    row = QHBoxLayout(popup)
    row.setContentsMargins(12, 12, 12, 12)
    row.setSpacing(14)

    pix = QPixmap()
    if detail.image:
        reader = QImageReader(str(detail.image))
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(QSize(200, 290), Qt.AspectRatioMode.KeepAspectRatio))
        pix = QPixmap.fromImage(reader.read())
    if not pix.isNull():
        art = QLabel()
        art.setPixmap(pix)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setAccessibleName(f"{detail.name} card image")
        row.addWidget(art)

    info = QVBoxLayout()
    info.setSpacing(6)
    row.addLayout(info, 1)
    heading = QHBoxLayout()
    title = _label(detail.name, "heading")
    title.setStyleSheet("font-size: 21px;")
    heading.addWidget(title, 1)
    close = QToolButton()
    close.setText("×")
    close.setAccessibleName("Close card details")
    close.setFixedSize(24, 24)
    close.clicked.connect(popup.close)
    heading.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
    info.addLayout(heading)
    if detail.kind:
        info.addWidget(_label(detail.kind, "muted"))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
    body = QWidget()
    column = QVBoxLayout(body)
    column.setContentsMargins(0, 4, 5, 0)
    column.setSpacing(6)
    if detail.text:
        description = _label(detail.text, "body")
        description.setStyleSheet("font-size: 13px;")
        column.addWidget(description)
    column.addStretch()
    if detail.facts:
        rule = QFrame()
        rule.setProperty("role", "line")
        rule.setFixedHeight(1)
        column.addWidget(rule)
        facts = QGridLayout()
        facts.setVerticalSpacing(5)
        facts.setColumnStretch(1, 1)
        for i, (key, value) in enumerate(detail.facts):
            facts.addWidget(_label(key, "muted"), i, 0)
            fact = _label(value, "small")
            fact.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            facts.addWidget(fact, i, 1)
        column.addLayout(facts)
    scroll.setWidget(body)
    info.addWidget(scroll, 1)
    QShortcut(QKeySequence(Qt.Key.Key_Escape), popup, activated=popup.close)

    width = 526 if not pix.isNull() else 320
    height = 312 if not pix.isNull() else min(312, 110 + body.heightForWidth(width - 36))
    popup.setFixedSize(min(width, bounds.width()), min(max(140, height), bounds.height()))
    x, y = anchor.right() + 7, anchor.top()
    if x + popup.width() > bounds.right() + 1:
        x = anchor.left() - popup.width() - 6
        if x < bounds.left():
            x, y = anchor.left(), anchor.bottom() + 7
            if y + popup.height() > bounds.bottom() + 1:
                y = anchor.top() - popup.height() - 6
    popup.move(
        max(bounds.left(), min(x, bounds.right() - popup.width() + 1)),
        max(bounds.top(), min(y, bounds.bottom() - popup.height() + 1)),
    )
    popup.show()
    return popup
