"""Responsive placement of the sound collection, mixer and shared action row."""

from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout

from studio import theme as t
from studio.pages.base import Page
from studio.pages.card_views import scroll
from studio.pages.music_sounds_controls import TITLE_ROW_HEIGHT
from studio.pages.sounds_views import add_section
from studio.widgets import margins


class SoundWorkspace(Page):
    def build_layout(self) -> None:
        self.stack = QStackedWidget()
        widgets = {
            "overview": self.overview,
            "mix": self.mixer,
            "chants": self.chants,
            "event": self.picker,
            "saved": self.collections.library,
            "downloads": self.collections.downloads,
            "name": self.collections.name,
        }
        self.views = {key: scroll(widget) for key, widget in widgets.items()}
        self.mixer.scroller, self.chants.scroller = self.views["mix"], self.views["chants"]
        for key, view in self.views.items():
            self.stack.addWidget(view)
            if key != "overview":
                widgets[key].back.clicked.connect(self.escape)
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(0)
        self.messages = QHBoxLayout()
        self.messages.setContentsMargins(t.XXL, 0, t.XXL, 0)
        notices = QVBoxLayout()
        notices.setSpacing(t.XS)
        notices.addWidget(self.feedback)
        notices.addWidget(self.closed)
        self.messages.addLayout(notices)
        body.addLayout(self.messages)
        body.addWidget(self.stack, 1)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(t.XXL, t.XS, t.XXL, t.LG)
        self.footer.addWidget(self.bar)
        body.addLayout(self.footer)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        inset = t.XXL + max(0, (event.size().width() - t.CONTENT_MAX) // 2)
        self.messages.setContentsMargins(inset, 0, inset, 0)
        self.footer.setContentsMargins(inset, t.XS, inset, t.LG)
        self.place_bar()
        self.size_overview()

    def place_bar(self) -> None:
        inline = self.state == "overview" and self.height() > t.TALL_HEIGHT + t.HERO_MIN
        if inline and self.bar.parentWidget() is not self.overview:
            self.overview.wide_actions.addWidget(self.bar)
        elif not inline and self.bar.parentWidget() is self.overview:
            self.footer.addWidget(self.bar)
        self.bar.setVisible(self.state in ("overview", "mix", "chants"))

    def size_overview(self) -> None:
        compact = self.width() < t.COMPACT_WIDTH - t.RAIL_COMPACT
        if self.overview.compact_tools != compact:
            self.overview.place_tools(compact)
        columns = 1 if compact else max(1, len(self.cards))
        if self.overview.columns != columns:
            self.overview.columns = columns
            add_section(self.overview.cards, self.cards.values(), columns)
            for col in range(3):
                self.overview.cards.setColumnStretch(col, int(col < columns))
        short = self.window().height() <= TITLE_ROW_HEIGHT
        self.overview.hero.setVisible(not short)
        self.overview.compact_title.setVisible(short)
        condensed = short and self.snapshot is not None
        self.overview.summary.setVisible(condensed)
        self.overview.current.setVisible(not condensed or self.overview.summary.isChecked())
        self.bar.detail.setVisible(not short)
        self.bar.restart.setVisible(not short and bool(self.draft.assignments and self.snapshot))
        for button, hint in (
            (self.bar.undo, self.bar.undo_help),
            (self.bar.restore, self.bar.restore_help),
        ):
            hint.setVisible(not short and not button.isHidden())
