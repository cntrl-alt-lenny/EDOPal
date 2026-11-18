"""Studio navigation, including the compact rail and its pinned footer."""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

import config
from studio import theme as t
from studio.widgets.controls import Button, label, margins

if TYPE_CHECKING:
    from studio.pages.base import Page


class Sidebar(QWidget):
    selected = Signal(str)

    def __init__(self, pages: Iterable[type["Page"]], app_version: str, mark: QWidget) -> None:
        super().__init__()
        self.nav: dict[str, Button] = {}
        self.setObjectName("rail")
        self.setFixedWidth(t.RAIL_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.MD, t.XL, t.MD, t.LG)
        layout.setSpacing(t.XS)
        brand = self.brand_row = QHBoxLayout()
        brand.setContentsMargins(t.SM, 0, 0, t.MD)
        brand.setSpacing(t.MD)
        brand.addWidget(mark)
        self.brand_text = QWidget()
        wordmark = QVBoxLayout(self.brand_text)
        margins(wordmark, 0)
        wordmark.setSpacing(0)
        wordmark.addWidget(label(config.APP_NAME, "brand"))
        wordmark.addWidget(label("Unofficial EDOPro companion", "muted", True))
        brand.addWidget(self.brand_text, 1)
        layout.addLayout(brand)
        self.rail_scroll = QScrollArea()
        self.rail_scroll.setObjectName("railScroll")
        self.rail_scroll.setWidgetResizable(True)
        self.rail_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        items = QWidget()
        items.setObjectName("railItems")
        entries = self.entries = QVBoxLayout(items)
        margins(entries, 0)
        entries.setSpacing(t.XS)
        entries.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rail_scroll.setWidget(items)
        layout.addWidget(self.rail_scroll, 1)
        self.rail_labels = [self.brand_text]
        headings = {"cards": "CUSTOMIZE", "decks": "DECKS", "replays": "TOOLS"}
        for page in pages:
            if page.id in headings:
                heading = label(headings[page.id], "kicker")
                heading.setContentsMargins(t.MD, t.SM, 0, t.XS)
                entries.addWidget(heading)
                self.rail_labels.append(heading)
            if page.id == "settings":
                self.release = Button("", "secondary", "link")
                self.release.hide()
                layout.addWidget(self.release)
            # Page titles can already contain a Qt literal ampersand.
            title = page.title.replace("&&", "&")
            item = Button(title.replace("&", "&&"), "nav", page.icon)
            item.setAccessibleName(title)
            item.setToolTip(title)
            item.setMinimumHeight(t.NAV_H)
            item.setCheckable(True)
            item.clicked.connect(lambda _checked=False, key=page.id: self.selected.emit(key))
            self.nav[page.id] = item
            (layout if page.id == "settings" else entries).addWidget(item)
        version = label(f"Version {app_version}", "muted")
        version.setContentsMargins(t.MD, t.SM, 0, 0)
        layout.addWidget(version)
        self.rail_labels.append(version)

    def fit(self, size: QSize) -> bool:
        """Return whether only the navigation icons are visible."""
        icons = size.width() < t.MIN_SIZE[0] - t.RAIL_COMPACT
        compact = size.width() < t.COMPACT_WIDTH
        width = t.RAIL_COMPACT if compact else t.RAIL_WIDTH
        self.setFixedWidth(t.NAV_H + t.XL if icons else width)
        for item in self.rail_labels:
            item.setVisible(not icons)
        for button in self.nav.values():
            button.setText("" if icons else button.accessibleName().replace("&", "&&"))
        self.release.setText(
            ""
            if icons
            else self.release.accessibleName()
            .replace("version ", "version\n")
            .replace("update ", "update\n")
        )
        short = size.height() <= t.COMPACT_HERO_HEIGHT
        tight = size.height() <= t.MIN_SIZE[1]
        row_height = t.XXL if tight else (t.BUTTON_H - t.XS) if short else t.NAV_H
        for button in self.nav.values():
            button.setFixedHeight(row_height)
        self.entries.setSpacing(0 if tight else t.HAIR if short else t.XS)
        for heading in self.rail_labels[1:-1]:
            heading.setContentsMargins(
                t.MD, 0 if tight else t.XS if short else t.SM, 0, 0 if short else t.XS
            )
        inset = t.SM if compact else t.MD
        self.brand_row.setContentsMargins(0 if icons else t.SM, 0, 0, 0 if tight else t.MD)
        self.layout().setContentsMargins(
            inset, t.SM if short else t.XL, inset, t.SM if short else t.LG
        )
        return icons
