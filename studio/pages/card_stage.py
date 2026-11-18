"""Scroll artwork content independently of its primary actions."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from studio import theme as t
from studio.context import AppContext
from studio.pages.base import Page
from studio.pages.card_results import ResultView
from studio.pages.card_views import OptionsView, SettingsView, page_layout, scroll
from studio.widgets import Button, GameClosedBlock, Hero, ProgressBlock, margins


class CardStage(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.options = OptionsView()
        self.settings = SettingsView(context.cfg)
        self.results = ResultView()
        progress_view = QWidget()
        stage = page_layout(progress_view)
        progress_view.setMaximumWidth(t.WIDE_CONTENT)
        self.banner = Hero(
            "HD Cards", "Artwork sync", "Updating the selected installation.", "download"
        )
        stage.addWidget(self.banner)
        self.progress = ProgressBlock()
        stage.addWidget(self.progress)
        stage.addStretch()
        blocked = QWidget()
        layout = page_layout(blocked)
        self.back = Button("Back", "quiet", "back")
        self.back.setAccessibleName("Back to HD Cards")
        layout.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(
            Hero(
                "HD Cards",
                "Artwork changes",
                "Close the game so its files can be changed safely.",
                "download",
            )
        )
        self.guard = GameClosedBlock()
        blocked.setMaximumWidth(t.WIDE_CONTENT)
        layout.addWidget(self.guard)
        layout.addStretch()
        self.stack = QStackedWidget()
        self.views = {
            key: scroll(widget)
            for key, widget in (
                ("options", self.options),
                ("settings", self.settings),
                ("progress", progress_view),
                ("results", self.results),
                ("blocked", blocked),
            )
        }
        for view in self.views.values():
            self.stack.addWidget(view)
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.addWidget(self.stack, 1)
        self.footer = QStackedWidget()
        progress_actions = QWidget()
        row = QHBoxLayout(progress_actions)
        margins(row, 0)
        row.addStretch()
        row.addWidget(self.progress.cancel)
        self.action_rows = {
            "options": self.options.actions,
            "settings": self.settings.actions,
            "results": self.results.actions,
            "progress": progress_actions,
        }
        for actions in self.action_rows.values():
            self.footer.addWidget(actions)
        layout.addWidget(self.footer)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        inset = max(t.LG, (self.width() - t.WIDE_CONTENT) // 2 + t.XXL)
        self.footer.setContentsMargins(inset, t.SM, inset, t.MD)

    @property
    def state(self) -> str:
        return next(key for key, view in self.views.items() if view is self.stack.currentWidget())

    def open_view(self, key: str) -> None:
        self.stack.setCurrentWidget(self.views[key])
        actions = self.action_rows.get(key)
        self.footer.setVisible(actions is not None)
        if actions is not None:
            self.footer.setCurrentWidget(actions)
        self.views[key].verticalScrollBar().setValue(0)
        focus = {
            "blocked": self.guard.retry,
            "settings": self.settings.back,
            "options": self.options.start,
            "results": self.results.done,
            "progress": self.progress.cancel,
        }[key]
        if focus.isEnabled() and focus.isVisible():
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
