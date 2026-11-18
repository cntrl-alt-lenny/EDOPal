"""Scrolling soundtrack views and the actions that stay within reach below them."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QVariantAnimation
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from studio import theme as t
from studio.context import AppContext
from studio.pages.base import Page
from studio.pages.music_widgets import action
from studio.widgets import label, margins


class MusicSurface(Page):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transition = QVariantAnimation(self)
        self.transition.setDuration(t.MOTION_MS)
        self.transition.setStartValue(t.MD)
        self.transition.setEndValue(0)
        self.transition.valueChanged.connect(self.slide)
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll, 1)
        self.footer: QWidget | None = None

    def fixed_actions(self) -> QVBoxLayout:
        self.footer = QWidget()
        outer = QHBoxLayout(self.footer)
        outer.setContentsMargins(self.inset, 0, self.inset, self.inset)
        inner = QWidget()
        inner.setMaximumWidth(t.CONTENT_MAX - 2 * self.inset)
        outer.addStretch()
        outer.addWidget(inner, 100)
        outer.addStretch()
        self.layout().addWidget(self.footer)
        layout = QVBoxLayout(inner)
        margins(layout, 0)
        return layout

    def screen(
        self,
        name: str,
        back: Callable | None = None,
        title: str = "",
        section: str = "MUSIC LIBRARY",
    ) -> QVBoxLayout:
        self.transition.stop()
        self.preview.stop()
        self.preview.setParent(self)
        self.preview.hide()
        self.state, self.back = name, back
        if self.footer:
            self.footer.hide()
            self.layout().removeWidget(self.footer)
            self.footer.deleteLater()
            self.footer = None
        old = self.scroll.takeWidget()
        if old:
            old.hide()
            old.deleteLater()
        body = QWidget()
        body.setMaximumWidth(t.CONTENT_MAX)
        layout = QVBoxLayout(body)
        self.inset = t.LG if self.width() < t.COMPACT_WIDTH - t.RAIL_WIDTH else t.XXL
        margins(layout, self.inset)
        layout.setSpacing(t.LG)
        self.scroll.setWidget(body)
        if back or title:
            top = QHBoxLayout()
            if back:
                self.back_button = action("Back", back, "quiet", "back")
                top.addWidget(self.back_button)
            top.addWidget(label(section, "goldKicker"))
            top.addStretch()
            layout.addLayout(top)
            if title:
                layout.addWidget(label(title, "heading", True))
        if back:
            self.transition.start()
        return layout

    def slide(self, offset: int) -> None:
        layout = self.scroll.widget().layout()
        layout.setContentsMargins(self.inset + offset, self.inset, self.inset - offset, self.inset)
