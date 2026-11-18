"""Layout and background-read lifecycle for the installation tools."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLayout,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.tasks import Job, Work
from studio.widgets import Button, Hero, Panel, glyph, label, margins


def row() -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(t.MD)
    return layout


def tool_margins(body: QLayout, width: int) -> None:
    inset = max(t.XL, (width - t.CONTENT_MAX) // 2)
    body.setContentsMargins(inset, t.XL, inset, t.XL)


def editor(name: str, *, readonly: bool = False) -> QPlainTextEdit:
    field = QPlainTextEdit()
    field.setAccessibleName(name)
    field.setReadOnly(readonly)
    field.setTabChangesFocus(True)
    field.setStyleSheet(f"""
        QPlainTextEdit {{ background: {t.RAIL}; border: {t.HAIR}px solid {t.EDGE};
            border-radius: {t.R_SMALL}px; padding: {t.MD}px; color: {t.TEXT}; }}
        QPlainTextEdit:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}
    """)
    return field


class ToolPage(Page):
    mood: str
    introduction: str

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        outer = QVBoxLayout(self)
        margins(outer, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.read_job: Job | None = None
        self.entered = False
        self.state = ""

    def surface(self, state: str) -> QVBoxLayout:
        self.state = state
        old = self.scroll.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        canvas = QWidget()
        self.body = QVBoxLayout(canvas)
        self.body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._fit_width()
        self.body.setSpacing(t.LG)
        canvas.resize(self.scroll.viewport().size())
        self.scroll.setWidget(canvas)
        self.scroll.verticalScrollBar().setValue(0)
        return self.body

    def _fit_width(self) -> None:
        tool_margins(self.body, self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.scroll.widget():
            self._fit_width()

    def banner(self, headline: str, description: str, *, large: bool = False) -> Hero:
        hero = Hero(
            "Tools" if headline == self.title else self.title, headline, description, self.mood
        )
        hero.setMinimumHeight(t.HERO_MIN if large else t.TILE_LARGE)
        if large:
            hero.setMaximumHeight(t.HERO_MAX)
            hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body.addWidget(hero, int(large))
        return hero

    def state_view(
        self,
        state: str,
        headline: str,
        message: str,
        action: str = "",
        callback: Callable[[], None] | None = None,
        *,
        back: Callable[[], None] | None = None,
    ) -> None:
        self.surface(state)
        if back:
            self.back_button(back)
        self.banner(self.title, self.introduction, large=True)
        panel = Panel()
        line = row()
        line.addWidget(glyph("warning" if state.endswith("error") else self.icon, t.ICON_BIG))
        copy = QVBoxLayout()
        copy.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        copy.addWidget(label(headline, "heading", True))
        copy.addWidget(label(message, "body", True))
        line.addLayout(copy, 1)
        panel.body.addLayout(line)
        if action and callback:
            button = Button(action, "primary")
            button.clicked.connect(callback)
            panel.body.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        self.body.addWidget(panel)
        if action and callback:
            button.setFocus()

    def no_game(self) -> None:
        self.state_view(
            "no_game",
            "Choose your installation",
            "Select the folder where you open EDOPro to get started.",
            "Choose folder",
            self.ctx.installations_requested.emit,
        )

    def stop_read(self) -> None:
        if self.read_job:
            self.read_job.cancel()
            self.read_job = None

    def read(self, work: Work, receive: Callable, failed: Callable) -> None:
        self.stop_read()
        job = self.ctx.tasks.start(work)
        self.read_job = job

        def deliver(callback: Callable, value: object) -> None:
            if self.read_job is job:
                self.read_job = None
                callback(value)

        job.result.connect(lambda result: deliver(receive, result))
        job.error.connect(lambda exc: deliver(failed, exc))

    def refresh(self) -> None:
        raise NotImplementedError

    def shown(self) -> None:
        self.entered = True
        self.refresh()

    def hidden(self) -> bool:
        self.entered = False
        self.stop_read()
        return True

    def installation_changed(self, installation: Installation) -> None:
        if self.entered:
            self.refresh()

    def open_folder(self, path: Path) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.ctx.notify("The folder could not be opened in your file manager.")

    def back_button(self, callback: Callable[[], None]) -> Button:
        button = Button("Back", "quiet", "back")
        button.clicked.connect(callback)
        self.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        return button
