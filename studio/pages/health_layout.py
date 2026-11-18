"""Layout and background-read lifecycle for the installation tools."""

from collections.abc import Callable
from pathlib import Path
from weakref import ref

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLayout,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import config
import desktop_helpers
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


def technical_details(body: QVBoxLayout, text: str) -> None:
    if text:
        button = Button("Technical details", "quiet")
        button.setCheckable(True)
        body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        detail = label(text, "muted", True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(detail)
        detail.hide()
        button.toggled.connect(detail.setVisible)


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
        technical: str = "",
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
        technical_details(panel.body, technical)
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
            "Choose your EDOPro folder",
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
        pending = ref(job)

        def deliver(callback: Callable, value: object) -> None:
            if self.read_job is not None and self.read_job is pending():
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
        self.open_in_desktop(path, desktop_helpers.open_link, "The folder could not be opened.")

    def open_in_desktop(self, path: Path, launch: Callable[[Path], bool], message: str) -> None:
        job = self.ctx.tasks.start(lambda _: launch(path))
        job.result.connect(lambda opened: None if opened else self.ctx.notify(message))
        job.error.connect(lambda _: self.ctx.notify(message))

    @staticmethod
    def folder_read_error() -> str:
        return f"{config.APP_NAME} couldn’t read the EDOPro folder. Reconnect the drive or choose another folder."

    def back_button(self, callback: Callable[[], None]) -> Button:
        button = Button("Back", "quiet", "back")
        button.clicked.connect(callback)
        self.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        return button
