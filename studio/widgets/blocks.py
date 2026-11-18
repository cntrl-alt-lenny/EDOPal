from html import escape

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

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
        self._normal_heights: tuple[int, int] | None = None
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
        self.category = label(category.upper(), "goldKicker")
        overline.addWidget(self.category)
        overline.addStretch()
        copy.addLayout(overline)
        self.headline = label(title, "heroTitle", True)
        self._emphasis = emphasis
        self._title = title
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

    def showEvent(self, event: QShowEvent) -> None:
        # Some sections construct their banner after the last window resize.
        if (compact := self.window().property("compactHeroes")) is not None:
            self.set_compact(compact)
        super().showEvent(event)

    def set_compact(self, compact: bool) -> None:
        """Adapt the banner while retaining the page's preferred full-size dimensions."""
        if compact == (self._normal_heights is not None):
            return
        row = self.layout()
        self.headline.setProperty("compact", compact)
        self.headline.style().unpolish(self.headline)
        self.headline.style().polish(self.headline)
        if self._emphasis:
            join = " " if compact else "<br/>"
            self.headline.setText(
                f'{escape(self._title)}{join}<i style="color:{t.PALE}">{escape(self._emphasis)}</i>'
            )
        row.setStretch(0, 72 if compact else 55)
        row.setStretch(1, 28 if compact else 45)
        pad = t.SM if compact else t.LG
        row.setContentsMargins(t.XL if compact else t.XXL, pad, t.XL, pad)
        row.itemAt(0).layout().setSpacing(t.XS if compact else t.SM)
        if compact:
            self._normal_heights = self.minimumHeight(), self.maximumHeight()
            self._fit_compact()
        else:
            low, high = self._normal_heights
            self._normal_heights = None
            self.headline.setMinimumHeight(0)
            self.description.setMinimumHeight(0)
            super().setMinimumHeight(low)
            super().setMaximumHeight(high)
        self.updateGeometry()

    def _fit_compact(self) -> None:
        # Keep long titles and error descriptions readable after wrapping.
        # Qt can give surplus space to the two stretches before satisfying a
        # wrapped label's height-for-width. Reserve the actual lines first.
        for item in (self.headline, self.description):
            needed = max(0, item.heightForWidth(item.width()))
            if needed != item.minimumHeight():
                item.setMinimumHeight(needed)
        height = max(t.TILE_H + t.LG, self.layout().totalHeightForWidth(self.width()))
        if self.minimumHeight() != height or self.maximumHeight() != height:
            super().setFixedHeight(height)

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize) and self._normal_heights:
            self._fit_compact()
        return result

    def setFixedHeight(self, height: int) -> None:
        if self._normal_heights is None:
            super().setFixedHeight(height)
        else:
            self._normal_heights = height, height

    def setMinimumHeight(self, height: int) -> None:
        if self._normal_heights is None:
            super().setMinimumHeight(height)
        else:
            self._normal_heights = height, self._normal_heights[1]

    def setMaximumHeight(self, height: int) -> None:
        if self._normal_heights is None:
            super().setMaximumHeight(height)
        else:
            self._normal_heights = self._normal_heights[0], height


class EmptyState(QWidget):
    def __init__(self, title: str, description: str, symbol: str, category: str) -> None:
        super().__init__()
        body = QVBoxLayout(self)
        margins(body, t.XXL)
        self.hero = Hero(category, title, description, symbol)
        self.hero.setMinimumHeight(t.TILE_LARGE)
        body.addWidget(self.hero)
        body.addStretch()


class Notice(Panel):
    def __init__(self) -> None:
        super().__init__(t.SM)
        self.message = label("", "notice", True)
        self.body.addWidget(self.message)
        self.hide()

    def show_message(self, message: str) -> None:
        self.message.setText(message)
        self.setVisible(bool(message))


class GameClosedBlock(Panel):
    """Inline recovery after AppContext.require_game_closed stops a write."""

    def __init__(self) -> None:
        super().__init__()
        self.body.addWidget(label("Close EDOPro to continue", "heading", True))
        self.detail = label("", "body", True)
        self.body.addWidget(self.detail)
        self.retry = Button("Retry", "primary")
        self.body.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)

    def show_reason(self, reason: str) -> None:
        self.detail.setText(reason)
        self.show()
        self.retry.setFocus()


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
        self.body.addWidget(self.cancel, 0, Qt.AlignmentFlag.AlignRight)

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
