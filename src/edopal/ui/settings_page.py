"""Application information, local folders, project links and licenses."""

from __future__ import annotations

import platform
from functools import partial
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from edopal import PACKS_REPO, REPO, __version__
from edopal.core import Core
from edopal.core.updates import RELEASES
from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label, rule
from edopal.ui.widgets import QuietButton


class Mascot(QWidget):
    """Engraved vector artwork with a spotlight that follows the pointer."""

    def __init__(self):
        super().__init__()
        self.setObjectName("settingsMascot")
        self.setFixedSize(115, 115)
        self.setMouseTracking(True)
        self.setAccessibleName("EDOPal Mascot")
        self.pointer: QPointF | None = None
        logo = (t.ASSETS / "logo.svg").read_text(encoding="utf-8")
        outline = logo.replace(
            'fill="currentColor"', 'fill="none" stroke="currentColor" stroke-width="8" stroke-linejoin="round"'
        )
        self.engraving = QSvgRenderer(QByteArray(outline.replace("currentColor", "#505052").encode()), self)
        self.outline = QSvgRenderer(QByteArray(outline.replace("currentColor", t.GOLD).encode()), self)

    def enterEvent(self, event):
        self.pointer = event.position()
        self.update()
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        self.pointer = event.position()
        self.update()

    def leaveEvent(self, event):
        self.pointer = None
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        box = QRectF(self.rect())
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.engraving.render(painter, box)
            if self.pointer is None:
                return
            halo = QRadialGradient(self.pointer, 66)
            fade = ((0, 1), (1 / 3, 0.61), (2 / 3, 0.14), (1, 0))
            for position, opacity in fade:
                colour = QColor(t.GOLD)
                colour.setAlphaF(0.10 * opacity)
                halo.setColorAt(position, colour)
            painter.fillRect(box, halo)

            dpr = self.devicePixelRatioF()
            pixels = QImage(self.size() * dpr, QImage.Format.Format_ARGB32_Premultiplied)
            pixels.setDevicePixelRatio(dpr)
            pixels.fill(Qt.GlobalColor.transparent)
            with QPainter(pixels) as glow:
                glow.setRenderHint(QPainter.RenderHint.Antialiasing)
                self.outline.render(glow, box)
                for position, opacity in fade:
                    halo.setColorAt(position, QColor.fromRgbF(1, 1, 1, 0.82 * opacity))
                glow.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                glow.fillRect(box, halo)
            painter.drawImage(QPointF(), pixels)


def _glyph(name, size, colour=t.MUTED):
    glyph = label("")
    glyph.setFixedSize(size, size)
    glyph.setPixmap(t.icon(name, colour).pixmap(QSize(size, size), glyph.devicePixelRatioF()))
    return glyph


class _PathLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", "settingsValue")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value = ""

    def set_path(self, path):
        home = Path.home()
        self.value = f"~/{path.relative_to(home)}" if path and path.is_relative_to(home) else str(path or "Choose a Folder")
        full_path = str(path) if path else self.value
        self.setToolTip(full_path)
        self.setAccessibleName(full_path)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        self.setText(self.fontMetrics().elidedText(self.value, Qt.TextElideMode.ElideMiddle, self.width()))


class _BuildInfo(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(118)
        self.version = QWidget(self)
        lockup = QVBoxLayout(self.version)
        lockup.setContentsMargins(0, 0, 0, 0)
        lockup.setSpacing(5)
        lockup.addWidget(label("Installed Version", "muted"))
        version = QHBoxLayout()
        version.setSpacing(11)
        name = label("EDOPal", "brand")
        name.setStyleSheet("font-size: 29px;")
        version.addWidget(name)
        version.addWidget(label(__version__, "settingsVersion"), alignment=Qt.AlignmentFlag.AlignVCenter)
        version.addStretch()
        lockup.addLayout(version)
        lockup.addWidget(label("Unofficial companion for EDOPro", "body"))

        self.metadata = []
        for title, value in (("Update Checks", "On Launch"), ("Platform", f"{platform.system()} · {platform.machine()}")):
            column = QWidget(self)
            column.setAccessibleName(f"{title}: {value}")
            pair = QVBoxLayout(column)
            pair.setContentsMargins(0, 0, 0, 0)
            pair.setSpacing(5)
            pair.addWidget(label(title, "muted"), alignment=Qt.AlignmentFlag.AlignHCenter)
            pair.addWidget(label(value, "settingsMeta"), alignment=Qt.AlignmentFlag.AlignHCenter)
            self.metadata.append(column)
        self.mascot = Mascot()
        self.mascot.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.version.adjustSize()
        self.version.move(0, (self.height() - self.version.height()) // 2 - 1)
        self.mascot.move(self.width() - 18 - self.mascot.width(), 1)

        first = self.version.width() / 2
        last = self.mascot.x() + self.mascot.width() / 2
        for i, column in enumerate(self.metadata, 1):
            column.adjustSize()
            center = first + i * (last - first) / 3
            column.move(round(center - column.width() / 2), (self.height() - column.height()) // 2 + 2)


class SettingsPage(ShellPage):
    def __init__(self, core: Core, parent: QWidget | None = None, *, choose_game=None):
        super().__init__("Settings & About", parent)
        self._core = core
        self.choose_game = choose_game
        self.setStyleSheet(
            f"""
            QPushButton[role="settingsRow"] {{ background: transparent; border-color: transparent; padding: 0; }}
            QPushButton[role="settingsRow"]:hover {{ background: {t.PANEL}; }}
            QLabel[role="settingsValue"] {{ color: {t.TEXT}; font-size: 13px; }}
            QLabel[role="settingsStrong"] {{ font-size: 15px; font-weight: 600; }}
            QLabel[role="settingsMeta"] {{ font-size: 17px; font-weight: 600; }}
            QLabel[role="settingsVersion"] {{ color: {t.GOLD}; font-size: 15px; }}
            """
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.addWidget(scroll, 1)
        sheet = QWidget()
        scroll.setWidget(sheet)
        content = QVBoxLayout(sheet)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(rule())
        content.addWidget(_BuildInfo())
        content.addWidget(rule())
        content.addSpacing(24)

        groups = QHBoxLayout()
        groups.setSpacing(24)
        left, right = QVBoxLayout(), QVBoxLayout()
        for column in (left, right):
            column.setSpacing(0)
        groups.addLayout(left, 598)
        divider = rule()
        divider.setFixedWidth(1)
        divider.setMaximumHeight(16777215)
        divider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        groups.addWidget(divider)
        groups.addLayout(right, 394)
        content.addLayout(groups)
        content.addStretch()

        self._section(left, "Local Folders")
        for title, path in (
            ("Game Installation", None),
            ("Settings", core.paths.config),
            ("Data & Undo History", core.paths.data),
            ("Downloads & Cache", core.paths.cache),
        ):
            row, button = self._action(left, title, 48)
            row.addWidget(_glyph("folder", 19, t.TEXT))
            row.addWidget(label(title), 2)
            value = _PathLabel()
            row.addWidget(value, 4)
            row.addWidget(_glyph("external", 15))
            if path is None:
                self._game_path = value
                button.clicked.connect(self._open_game)
            else:
                value.set_path(path)
                button.clicked.connect(partial(self._open, QUrl.fromLocalFile(str(path))))
        left.addWidget(rule())
        self.set_install(core.installs.current())

        self._section(right, "Project Links")
        for title, description, target in (
            ("Source Code", REPO, f"https://github.com/{REPO}"),
            ("Content Packs", PACKS_REPO, f"https://github.com/{PACKS_REPO}"),
            ("Release Notes", "EDOPal Releases on GitHub", RELEASES),
        ):
            row, button = self._action(right, title, 63)
            details = QVBoxLayout()
            details.setSpacing(4)
            details.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            details.addWidget(label(title, "settingsStrong"))
            details.addWidget(label(description, "muted"))
            row.addLayout(details, 1)
            row.addWidget(_glyph("external", 15))
            button.clicked.connect(partial(self._open, QUrl(target)))
        right.addWidget(rule())

        left.addSpacing(31)
        right.addSpacing(34)
        self._section(left, "Media Credits", height=40)
        self._section(right, "Licenses", height=40)
        for name, contribution in (
            ("Project Ignis", "Game Data"),
            ("YGOPRODeck", "Card Artwork"),
            ("Format Library", "Formats and Tournament Results"),
            ("LaunchBox", "Game Cover Artwork"),
        ):
            left.addWidget(rule())
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 35, 0)
            row.setSpacing(8)
            credit = label(name)
            credit.setFixedHeight(32)
            row.addWidget(credit, 2)
            row.addWidget(label(contribution, "settingsValue"), 3)
            left.addLayout(row)

        for name, terms, target in (
            ("EDOPal", "MIT", QUrl(f"https://github.com/{REPO}/blob/HEAD/LICENSE")),
            ("Inter & Outfit", "SIL OFL 1.1", None),
            ("Qt for Python", "Licensing Terms", QUrl("https://doc.qt.io/qtforpython-6/licenses.html")),
        ):
            row, button = self._action(right, name, 43)
            row.addWidget(label(name), 1)
            row.addWidget(label(terms, "settingsValue"))
            row.addWidget(_glyph("external", 15))
            if target is None:
                menu = QMenu(button)
                for family in ("Inter", "Outfit"):
                    url = QUrl.fromLocalFile(str(t.ASSETS / "fonts" / f"{family}-OFL.txt"))
                    menu.addAction(f"{family} License", partial(self._open, url))
                button.setMenu(menu)
            else:
                button.clicked.connect(partial(self._open, target))
        left.addStretch()
        right.addStretch()

    @staticmethod
    def _section(column, title, height=45):
        heading = label(title, "heading")
        heading.setFixedHeight(height)
        heading.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        column.addWidget(heading)

    @staticmethod
    def _action(column, title, height):
        column.addWidget(rule())
        button = QuietButton("")
        button.setProperty("role", "settingsRow")
        button.setAccessibleName(title)
        button.setFixedHeight(height - 1)
        layout = QHBoxLayout(button)
        layout.setContentsMargins(0, 0, 3, 0)
        layout.setSpacing(12)
        column.addWidget(button)
        return layout, button

    def set_install(self, install):
        self._game_path.set_path(install.root if install else None)

    def _open_game(self):
        if install := self._core.installs.current():
            self._open(QUrl.fromLocalFile(str(install.root)))
        elif self.choose_game is not None:
            self.choose_game()

    def _open(self, url):
        if not QDesktopServices.openUrl(url):
            self._core.notify(f"Couldn’t open {url.toDisplayString()}", "warning")
