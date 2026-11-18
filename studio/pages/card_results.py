"""Render measured coverage and sync outcomes from the shared display model."""

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QHBoxLayout, QListView, QProgressBar, QWidget

from artwork_flow import Summary
from studio import theme as t
from studio.pages.card_views import line, page_layout
from studio.widgets import Button, ElidedLabel, Hero, Panel, glyph, label, margins


class Metric(Panel):
    def __init__(self, symbol: str) -> None:
        super().__init__(t.XL)
        row = QHBoxLayout()
        self.value = label("", "metric")
        row.addWidget(self.value, 1)
        row.addWidget(glyph(symbol, t.ICON_BIG))
        self.body.addLayout(row)
        self.caption = label("", "body")
        self.body.addWidget(self.caption)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedHeight(t.XS)
        self.bar.setTextVisible(False)
        self.body.addWidget(self.bar)

    def set_value(self, pair: tuple[str, str], warning: bool) -> None:
        value, caption = pair
        self.setVisible(bool(value))
        self.value.setText(value)
        self.caption.setText(caption)
        self.value.setProperty("role", "warnMetric" if warning else "metric")
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)
        self.bar.setVisible(value.endswith("%"))
        if value.endswith("%"):
            self.bar.setValue(int(value[:-1]))


class ResultView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.body = body = page_layout(self)
        body.addStretch(0)
        self.title = label("", "display", True)
        self.kicker = label("HD CARDS", "goldKicker")
        body.addWidget(self.kicker)
        body.addWidget(self.title)
        self.outcome = Hero("HD Cards", "", "", "check")
        body.addWidget(self.outcome)
        metrics = QHBoxLayout()
        metrics.setSpacing(t.MD)
        self.first, self.second = Metric("cards"), Metric("coverage")
        metrics.addWidget(self.first, 1)
        metrics.addWidget(self.second, 1)
        body.addLayout(metrics)

        self.details = Panel(t.LG)
        self.details.body.setSpacing(t.SM)
        body.addWidget(self.details)
        self.failures = Button("Show unavailable artwork", "secondary")
        self.failures.setCheckable(True)
        body.addWidget(self.failures, 0, Qt.AlignmentFlag.AlignLeft)
        self.list = QListView()
        self.list.setAccessibleName("Unavailable artwork")
        self.list.setUniformItemSizes(True)
        self.list.setMinimumHeight(t.TILE_H)
        self.model = QStringListModel(self.list)
        self.list.setModel(self.model)
        self.failures.toggled.connect(self._expand)
        body.addWidget(self.list)

        self.note = label("", "body", True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.note)
        self.location = QWidget()
        path = QHBoxLayout(self.location)
        margins(path, 0)
        path.addWidget(glyph("folder", t.ICON))
        self.location_name = label("", "small")
        path.addWidget(self.location_name)
        self.path = ElidedLabel()
        path.addWidget(self.path, 1)
        body.addWidget(self.location)

        actions = QHBoxLayout()
        self.done = Button("Back to HD Cards", "primary", "back")
        self.close_button = Button("Close", "quiet")
        actions.addWidget(self.done)
        actions.addStretch()
        actions.addWidget(self.close_button)
        body.addLayout(actions)
        body.addStretch(1)

    def _expand(self, expand: bool) -> None:
        self.list.setVisible(expand)
        self.failures.setText("Hide unavailable artwork" if expand else "Show unavailable artwork")

    def show_result(self, summary: Summary) -> None:
        message_only = not (summary.first[0] or summary.second[0] or summary.rows)
        self.body.setStretch(0, int(message_only))
        self.title.setText(summary.title)
        self.title.setVisible(not message_only)
        self.kicker.setVisible(not message_only)
        self.outcome.setVisible(message_only)
        if message_only:
            self.outcome.symbol = "warning" if summary.warning else "check"
            self.outcome.headline.setText(summary.title)
            self.outcome.description.setText(summary.note)
            self.outcome.update()
        self.first.set_value(summary.first, False)
        self.second.set_value(summary.second, summary.warning)
        while self.details.body.count():
            item = self.details.body.takeAt(0)
            if widget := item.widget():
                widget.hide()
                widget.deleteLater()
        for index, (name, count) in enumerate(summary.rows):
            if index:
                self.details.body.addWidget(line())
            row = QWidget()
            layout = QHBoxLayout(row)
            margins(layout, 0)
            layout.addWidget(label(name), 1)
            layout.addWidget(label(count))
            self.details.body.addWidget(row)
        self.details.setVisible(bool(summary.rows))
        self.model.setStringList(summary.failures)
        self.failures.setChecked(False)
        self.failures.setVisible(bool(summary.failures))
        self.list.hide()
        self.note.setText(summary.note)
        self.note.setVisible(bool(summary.note) and not message_only)
        self.location_name.setText(summary.location[0])
        self.path.set_value(summary.location[1])
        self.location.setVisible(bool(summary.location[1]))
