from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from studio import theme as t
from studio.widgets.art import ArchiveArt
from studio.widgets.controls import Button, Panel, label, margins


class Hero(ArchiveArt):
    def __init__(
        self,
        category: str,
        title: str,
        description: str,
        symbol: str = "cards",
        *,
        emphasis: str = "",
    ) -> None:
        super().__init__(symbol)
        row = QHBoxLayout(self)
        row.setContentsMargins(t.XXL, t.LG, t.XL, t.LG)
        copy = QVBoxLayout()
        copy.setSpacing(t.SM)
        copy.addStretch()
        overline = QHBoxLayout()
        overline.setSpacing(t.SM)
        rule = QWidget()
        rule.setFixedSize(t.XL, t.HAIR)
        rule.setStyleSheet(f"background: {t.GOLD};")
        overline.addWidget(rule)
        overline.addWidget(label(category.upper(), "goldKicker"))
        overline.addStretch()
        copy.addLayout(overline)
        self.headline = label(title, "heroTitle", True)
        if emphasis:
            self.headline.setTextFormat(Qt.TextFormat.RichText)
            self.headline.setText(
                f'{escape(title)}<br/><i style="color:{t.PALE}">{escape(emphasis)}</i>'
            )
        copy.addWidget(self.headline)
        self.description = label(description, "body", True)
        copy.addWidget(self.description)
        copy.addStretch()
        row.addLayout(copy, 55)
        row.addStretch(45)


class EmptyState(QWidget):
    def __init__(self, title: str, description: str, symbol: str, category: str) -> None:
        super().__init__()
        body = QVBoxLayout(self)
        margins(body, t.XXL)
        self.hero = Hero(category, title, description, symbol)
        self.hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.hero)


class Notice(Panel):
    def __init__(self) -> None:
        super().__init__(t.SM)
        self.message = label("", "notice", True)
        self.body.addWidget(self.message)
        self.hide()

    def show_message(self, message: str) -> None:
        self.message.setText(message)
        self.setVisible(bool(message))


class ProgressBlock(Panel):
    def __init__(self) -> None:
        super().__init__()
        self.phase = label("", "heading", True)
        self.percent = label("", "metric")
        top = QHBoxLayout()
        top.addWidget(self.phase, 1)
        top.addWidget(self.percent)
        self.body.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(t.SM)
        self.body.addWidget(self.bar)
        self.counts = label("", "muted")
        self.body.addWidget(self.counts)
        self.detail = label("", "body", True)
        self.body.addWidget(self.detail)
        self.cancel = Button("Cancel")
        self.body.addWidget(self.cancel, 0, Qt.AlignmentFlag.AlignLeft)

    def begin(self, phase: str) -> None:
        self.phase.setText(phase)
        self.bar.setRange(0, 0)
        self.percent.clear()
        self.counts.clear()
        self.detail.clear()
        self.cancel.setEnabled(True)

    def advance_to(self, done: int, total: int) -> None:
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.percent.setText(f"{min(100, 100 * done // max(total, 1))}%")
        self.counts.setText(f"{done:,} of {total:,}   ·   {max(total - done, 0):,} remaining")
