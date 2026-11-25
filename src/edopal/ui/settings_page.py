"""Application information, stable releases and EDOPal's local folders."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from html import escape
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from edopal import PACKS_REPO, REPO, __version__
from edopal.core import Core
from edopal.core.updates import RELEASES, UpdateCheck, check_for_updates
from edopal.ui import theme
from edopal.ui.widgets import PageHeader, Panel, QuietButton, page_layout


def _queue_check(core: Core, done: Callable, error: Callable) -> None:
    cache = core.paths.cache
    core.jobs.run(lambda ctx: check_for_updates(cache), key="settings.release", on_done=done, on_error=error)


def check_on_startup(core: Core) -> None:
    def checked(result: UpdateCheck):
        if result.available and not result.failed:
            core.notify(f"EDOPal {result.latest} is available. Find the release in Settings & About.", "info")

    _queue_check(core, checked, lambda exc: None)


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    return label


class _PathLabel(QLabel):
    def __init__(self, path: Path):
        super().__init__()
        self._path = str(path)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setProperty("role", "small")
        self.setToolTip(self._path)
        self.setAccessibleName(self._path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self._path, Qt.TextElideMode.ElideMiddle, self.contentsRect().width()))


class SettingsPage(QWidget):
    def __init__(self, core: Core, parent: QWidget | None = None):
        super().__init__(parent)
        self._core = core
        self._release_url = RELEASES + "/latest"
        main = page_layout(self)
        main.addWidget(PageHeader("Application", "Settings & About"))

        sheet = Panel(padding=theme.XL)
        sheet.setMaximumWidth(1040)
        sheet.body.setSpacing(theme.LG)
        main.addWidget(sheet)
        main.addStretch()
        release_row = QHBoxLayout()
        release_row.setSpacing(theme.MD)
        mark = QLabel()
        mark.setPixmap(QIcon(str(theme.ASSETS / "app-icon.png")).pixmap(QSize(48, 48), self.devicePixelRatioF()))
        release_row.addWidget(mark)
        details = QVBoxLayout()
        details.setSpacing(theme.XS)
        details.addWidget(_label(f"EDOPal {__version__}", "heading"))
        self._status = _label("Updates are checked once a day.", "muted")
        self._status.setWordWrap(True)
        details.addWidget(self._status)
        release_row.addLayout(details, 1)
        self._check = QPushButton("Check for updates")
        self._check.setFixedHeight(theme.BUTTON_H)
        self._check.clicked.connect(self._check_updates)
        release_row.addWidget(self._check)
        release_button = QuietButton("View release ↗")
        release_button.clicked.connect(lambda: self._open(QUrl(self._release_url)))
        release_row.addWidget(release_button)
        sheet.body.addLayout(release_row)

        self._divider(sheet)
        sheet.body.addWidget(_label("Local folders", "subheading"))
        rows = QGridLayout()
        rows.setHorizontalSpacing(theme.XL)
        rows.setVerticalSpacing(theme.XS)
        rows.setColumnStretch(1, 1)
        for row, (title, path) in enumerate(
            (
                ("Settings", core.paths.config),
                ("Data & undo history", core.paths.data),
                ("Downloads & cache", core.paths.cache),
            )
        ):
            rows.addWidget(_label(title, "body"), row, 0)
            rows.addWidget(_PathLabel(path), row, 1)
            button = QuietButton("Open", icon="folder")
            button.setIconSize(QSize(theme.LG, theme.LG))
            button.setAccessibleName(f"Open {title.lower()} folder")
            button.setToolTip(str(path))
            button.clicked.connect(partial(self._open, QUrl.fromLocalFile(str(path))))
            rows.addWidget(button, row, 2)
        sheet.body.addLayout(rows)

        self._divider(sheet)
        resources = QHBoxLayout()
        resources.setSpacing(theme.XXL)
        for title, items in (
            (
                "Project links",
                [
                    ("GitHub ↗", QUrl(f"https://github.com/{REPO}")),
                    ("Packs repository ↗", QUrl(f"https://github.com/{PACKS_REPO}")),
                ],
            ),
            (
                "Licenses & credits",
                [
                    ("EDOPal · MIT", QUrl(f"https://github.com/{REPO}/blob/HEAD/LICENSE")),
                    ("Inter · OFL", QUrl.fromLocalFile(str(theme.ASSETS / "fonts/Inter-OFL.txt"))),
                    ("Qt for Python", QUrl("https://doc.qt.io/qtforpython-6/licenses.html")),
                ],
            ),
        ):
            group = QVBoxLayout()
            group.setSpacing(theme.SM)
            group.addWidget(_label(title, "subheading"))
            group.addWidget(self._links(items))
            resources.addLayout(group, 1)
        sheet.body.addLayout(resources)
        credits = _label(
            "Card art and game data from YGOPRODeck and Project Ignis. Historical formats from Format Library.", "muted"
        )
        credits.setWordWrap(True)
        sheet.body.addWidget(credits)

    @staticmethod
    def _divider(panel):
        line = QFrame()
        line.setProperty("role", "line")
        line.setFixedHeight(theme.HAIR)
        panel.body.addWidget(line)

    def _links(self, items: list[tuple[str, QUrl]]) -> QLabel:
        anchors = []
        for name, url in items:
            target = escape(url.toString(QUrl.ComponentFormattingOption.FullyEncoded))
            anchors.append(f'<a style="color:{theme.GOLD}" href="{target}">{escape(name)}</a>')
        links = QLabel(" &nbsp; ".join(anchors))
        links.setProperty("role", "small")
        links.setWordWrap(True)
        links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        links.linkActivated.connect(lambda address: self._open(QUrl(address)))
        return links

    def _open(self, url: QUrl) -> None:
        if not QDesktopServices.openUrl(url):
            self._core.notify(f"Couldn’t open {url.toDisplayString()}", "warning")

    def _check_updates(self):
        self._check.setEnabled(False)
        self._status.setText("Checking GitHub…")
        _queue_check(self._core, self._checked, self._failed)

    def _checked(self, result: UpdateCheck):
        self._check.setEnabled(True)
        self._release_url = result.url if result.available else RELEASES + "/latest"
        if result.failed:
            status = f"The last known release was {result.latest}." if result.latest else "Next check tomorrow."
            self._status.setText(f"Couldn’t verify the latest release. {status}")
        elif result.available:
            self._status.setText(f"Update available · {result.latest}")
        else:
            self._status.setText("You’re up to date.")

    def _failed(self, exc: BaseException):
        self._check.setEnabled(True)
        self._status.setText("Couldn’t check for updates.")
        self._core.notify(str(exc), "warning")
