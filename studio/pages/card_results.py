"""Render measured coverage and sync outcomes from the shared display model."""

from dataclasses import replace

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QHBoxLayout, QListView, QProgressBar, QWidget

from artwork_flow import Summary
from main import DownloadStats
from studio import theme as t
from studio.pages.card_views import line, page_layout
from studio.widgets import Button, ElidedLabel, Hero, Panel, glyph, label, margins


def player_summary(summary: Summary) -> Summary:
    labels = {
        "Known-missing (cached)": "Previously unavailable artwork",
        "Queued downloads skipped (file already present)": "Images already present",
        "Network errors (will retry)": "Downloads interrupted by a connection error",
    }
    if summary.title == "Already up to date":
        return Summary("No artwork to download for this selection")
    return replace(
        summary,
        title=summary.title.replace("cancelled", "canceled"),
        rows=tuple((labels.get(name, name), count) for name, count in summary.rows),
        note=summary.note.replace(
            "Known-missing artwork", "Previously unavailable artwork"
        ).replace("Sync again to retry interrupted downloads.", "Sync again to retry."),
    )


def retry_summary(summary: Summary, stats: DownloadStats) -> tuple[Summary, str]:
    failures = tuple(
        f"{name} (ID {card_id})   ·   {kind}"
        + ("   ·   Will retry" if card_id in retry_ids else "")
        for kind, entries, retry_ids in (
            ("Card artwork", stats.failed_cards, stats.transient_ids),
            ("Field art", stats.field_failed_cards, stats.field_transient_ids),
        )
        for card_id, name in entries
    )
    summary = replace(summary, failures=failures)
    if stats.storage_error:
        return summary, "Retry"
    count = stats.transient_failures + len(stats.field_transient_ids)
    if count and summary.title == "Sync complete":
        downloads = f"{count:,} download" + ("s" if count != 1 else "")
        verb = "needs" if count == 1 else "need"
        return replace(
            summary,
            title=f"{downloads} {verb} another try",
            note=summary.note.replace("Sync again to retry interrupted downloads.", "").strip(),
        ), f"Retry {downloads}"
    return summary, ""


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
        self.setMaximumWidth(t.WIDE_CONTENT)
        self.back = Button("Back", "quiet", "back")
        body.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        self.title = label("", "display", True)
        self.kicker = label("HD CARDS", "goldKicker")
        body.addWidget(self.kicker)
        body.addWidget(self.title)
        self.outcome = Hero("HD Cards", "", "", "check")
        self.outcome.setMinimumHeight(t.TILE_LARGE)
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

        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        margins(actions, 0)
        self.done = Button("Done", "primary", "check")
        self.decks = Button("Open Tournament decks", "primary", "decks")
        self.full_library = Button("Sync everything instead", "secondary", "download")
        self.undo = Button("Undo last artwork sync", "secondary", "back")
        self.undo.setToolTip(
            "Restore replaced files and remove additions from the last sync that changed artwork in this folder."
        )
        self.allows_undo = False
        self.undo.hide()
        actions.addStretch()
        actions.addWidget(self.full_library)
        actions.addWidget(self.decks)
        actions.addWidget(self.undo)
        self.retry = Button("Retry", "primary", "download")
        actions.addWidget(self.retry)
        actions.addWidget(self.done)
        body.addStretch(1)

    def focus_action(self) -> Button:
        for button in (self.retry, self.decks, self.done):
            if button.isVisible():
                return button
        return self.back

    def _expand(self, expand: bool) -> None:
        self.list.setVisible(expand)
        self.failures.setText("Hide unavailable artwork" if expand else "Show unavailable artwork")

    def show_result(
        self,
        summary: Summary,
        *,
        no_decks: bool = False,
        scope: str = "",
        retry: str = "",
        back_to: str = "HD Cards",
        coverage: bool = False,
    ) -> None:
        category = f"HD CARDS  ·  {scope.upper()}" if scope else "HD CARDS"
        self.kicker.setText(category)
        self.outcome.category.setText(category)
        self.allows_undo = not (coverage or no_decks)
        self.decks.setVisible(no_decks)
        self.full_library.setVisible(no_decks)
        self.back.setText(f"Back to {back_to}")
        self.back.setVisible(coverage or no_decks or bool(retry))
        self.retry.setText(retry)
        self.retry.setVisible(bool(retry))
        self.done.setVisible(not (no_decks or coverage or retry))
        message_only = not (summary.first[0] or summary.second[0] or summary.rows)
        self.title.setText(summary.title)
        self.title.setVisible(not message_only)
        self.kicker.setVisible(not message_only)
        self.outcome.setVisible(message_only)
        if message_only:
            self.outcome.symbol = "decks" if no_decks else "warning" if summary.warning else "check"
            self.outcome.headline.setText(summary.title)
            self.outcome.description.setText(summary.note)
            self.outcome.description.setVisible(bool(summary.note))
            self.outcome.update()
        self.first.set_value(summary.first, False)
        self.second.set_value(summary.second, summary.warning and summary.second[0] != "0")
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
