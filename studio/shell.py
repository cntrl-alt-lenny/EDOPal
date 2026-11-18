import asyncio
from pathlib import Path

from PySide6.QtCore import QEvent, QPropertyAnimation, QSettings, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
from artwork_flow import compact_path
from studio import geometry
from studio import theme as t
from studio.context import AppContext, Installation
from studio.icons import icon
from studio.installations import InstallationPicker
from studio.pages.base import Page
from studio.pages.cards import CardsPage
from studio.pages.decks import DecksPage
from studio.pages.health import HealthPage
from studio.pages.music import MusicPage
from studio.pages.replays import ReplaysPage
from studio.pages.settings import SettingsPage
from studio.pages.skins import SkinsPage
from studio.pages.sleeves import SleevesPage
from studio.pages.sounds import SoundsPage
from studio.tasks import WorkerContext
from studio.widgets import Button, ElidedLabel, Notice, glyph, label, margins

PAGES = (CardsPage, SkinsPage, SleevesPage, SoundsPage, MusicPage, DecksPage, ReplaysPage, HealthPage, SettingsPage)
RELEASES = "https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases/latest"


class StudioWindow(QMainWindow):
    def __init__(self, context: AppContext, *, startup: bool = True) -> None:
        super().__init__()
        self.ctx = context
        self.ctx.attach(self)
        self.ctx.installations_requested.connect(self.open_picker)
        self.setWindowTitle(config.APP_NAME)
        self.settings = QSettings(
            str(Path(context.cfg.config_path).with_suffix(".studio.ini")),
            QSettings.Format.IniFormat,
        )
        self.closing = False
        self.generation = 0
        self.pages: dict[str, Page] = {}
        self.nav: dict[str, Button] = {}
        self.central = QWidget()
        self.central.setObjectName("canvas")
        self.setCentralWidget(self.central)
        outer = QHBoxLayout(self.central)
        margins(outer, 0)
        outer.setSpacing(0)
        self.rail = QWidget()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(t.RAIL_WIDTH)
        outer.addWidget(self.rail)
        self.stack = QStackedWidget()
        self._sidebar()
        column = QVBoxLayout()
        column.setSpacing(0)
        outer.addLayout(column, 1)
        column.addWidget(self._install_bar())
        self.notice = Notice()
        column.addWidget(self.notice)
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.notice.hide)
        self.ctx.notice.connect(self._notice)
        self.content = QStackedWidget()
        column.addWidget(self.content, 1)
        self.content.addWidget(self.stack)
        self.picker = InstallationPicker(context)
        self.picker.dismissed.connect(self._end_picker)
        self.content.addWidget(self.picker)
        for page_type in PAGES:
            page = page_type(context)
            self.pages[page.id] = page
            self.stack.addWidget(page)
            self.ctx.installation_changed.connect(page.installation_changed)
        self.ctx.installation_changed.connect(self._display_folder)
        self.ctx.busy_changed.connect(self._busy)
        self.ctx.tasks.idle.connect(self._idle)
        self.effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self.effect)
        self.effect.setOpacity(1)
        self.effect.setEnabled(False)
        self.animation = QPropertyAnimation(self.effect, b"opacity", self)
        self.animation.setDuration(t.MOTION_MS)
        self.animation.setStartValue(0.72)
        self.animation.setEndValue(1.0)
        self.animation.finished.connect(lambda: self.effect.setEnabled(False))
        self._select(self.current)
        self.current.shown()
        self._display_folder(context.installation)
        geometry.restore(self, self.settings)
        if startup:
            self._startup()

    @property
    def current(self) -> Page:
        return self.stack.currentWidget()

    def _sidebar(self) -> None:
        layout = QVBoxLayout(self.rail)
        layout.setContentsMargins(t.MD, t.XL, t.MD, t.LG)
        layout.setSpacing(t.XS)
        brand = QHBoxLayout()
        brand.setContentsMargins(t.SM, 0, 0, t.XL)
        brand.setSpacing(t.MD)
        brand.addWidget(glyph("brand", t.BRAND_ICON))
        wordmark = QVBoxLayout()
        wordmark.setSpacing(0)
        wordmark.addWidget(label("EDOPro", "brand"))
        wordmark.addWidget(label("S T U D I O", "goldKicker"))
        brand.addLayout(wordmark, 1)
        layout.addLayout(brand)
        workspace = label("WORKSPACE", "kicker")
        workspace.setContentsMargins(t.MD, t.LG, 0, t.SM)
        layout.addWidget(workspace)
        for page in PAGES:
            if page.id == "settings":
                layout.addStretch()
                self.release = Button("", "secondary", "link")
                self.release.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES)))
                self.release.hide()
                layout.addWidget(self.release)
            item = Button("  " + page.title, "nav", page.icon)
            item.setAccessibleName(page.title)
            item.setMinimumHeight(t.NAV_H)
            item.setCheckable(True)
            item.clicked.connect(lambda _checked=False, key=page.id: self.navigate(key))
            item.installEventFilter(self)
            self.nav[page.id] = item
            layout.addWidget(item)
        version = label(f"Version {self.ctx.version}", "muted")
        version.setContentsMargins(t.MD, t.SM, 0, 0)
        layout.addWidget(version)

    def _install_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("installBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(t.XXL, t.MD, t.XXL, t.MD)
        row.setSpacing(t.MD)
        row.addWidget(glyph("folder", t.ICON_BIG))
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        copy.addWidget(label("GAME INSTALLATION", "kicker"))
        info = QHBoxLayout()
        info.setSpacing(t.MD)
        self.status_dot = QWidget()
        self.status_dot.setFixedSize(t.SM, t.SM)
        info.addWidget(self.status_dot)
        self.folder_name = label("", "body")
        self.folder_name.setMaximumWidth(t.RAIL_WIDTH)
        self.folder_name.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        info.addWidget(self.folder_name)
        self.path = ElidedLabel()
        info.addWidget(self.path, 1)
        copy.addLayout(info)
        row.addLayout(copy, 1)
        self.change = Button("Change folder", "secondary")
        self.change.clicked.connect(self.open_picker)
        row.addWidget(self.change)
        return bar

    def _startup(self) -> None:
        self.folder_name.setText("Looking for EDOPro…")
        generation = self.generation
        cfg = self.ctx.cfg
        guess, explicit = cfg.edopro_path, cfg.cli.edopro_path is not None

        def inspect(_: WorkerContext) -> Installation:
            return Installation.inspect(
                guess if explicit else config.find_edopro_folder([cfg.discovery_guess()]) or ""
            )

        def found(installation: Installation) -> None:
            if generation == self.generation and not self.closing:
                cfg.folder_detected = bool(installation.path and not explicit)
                self.ctx.set_installation(installation)

        job = self.ctx.tasks.start(inspect)
        job.result.connect(found)

        def failed(exc: Exception) -> None:
            if generation == self.generation and not self.closing:
                found(Installation())
                self.ctx.notify(str(exc))

        job.error.connect(failed)

        async def check() -> str | None:
            from main import _create_http_session, check_for_update

            async with _create_http_session() as session:
                return await check_for_update(session, self.ctx.version)

        job = self.ctx.tasks.start(lambda _: asyncio.run(check()))
        job.result.connect(self._update)

    @Slot(object)
    def _update(self, latest: str | None) -> None:
        if latest:
            self.release.setText(f"Update to {latest}")
            self.release.show()

    def _select(self, page: Page) -> None:
        for key, button in self.nav.items():
            chosen = key == page.id
            button.setChecked(chosen)
            button.setIcon(icon(self.pages[key].icon, t.GOLD if chosen else t.MUTED))

    def navigate(self, key: str) -> bool:
        if key not in self.pages or self.closing:
            return False
        old = self.current
        if self.content.currentWidget() is self.picker:
            if not self.picker.back.isEnabled():
                self._select(old)
                return False
            self.picker.close_picker()
        if key == old.id or not old.hidden():
            self._select(old)
            return key == old.id
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self._select(page)
        self.animation.stop()
        self.effect.setEnabled(True)
        self.animation.start()
        page.shown()
        return True

    def _display_folder(self, installation: Installation) -> None:
        self.generation += 1
        self.folder_name.setText(
            Path(installation.path).name if installation.path else "No installation selected"
        )
        path = installation.path
        self.path.set_value(path, compact_path(path, 30) if len(path) > 62 else path)
        self.folder_name.setToolTip(path)
        colour = t.GREEN if installation.ready else t.GOLD
        self.status_dot.setStyleSheet(f"background: {colour}; border-radius: {t.XS}px;")
        self.change.setText("Change folder" if installation.path else "Choose folder")

    def _busy(self, busy: bool) -> None:
        self.change.setEnabled(not busy)

    def _notice(self, message: str) -> None:
        self.notice.show_message(message)
        self.notice_timer.start(t.NOTICE_MS)

    def open_picker(self) -> None:
        if self.ctx.busy or not self.current.hidden():
            return
        if self.content.currentWidget() is self.picker:
            return
        self.content.setCurrentWidget(self.picker)
        self.change.setEnabled(False)
        self.picker.open_picker()

    def _end_picker(self) -> None:
        self.content.setCurrentWidget(self.stack)
        self.change.setEnabled(not self.ctx.busy)
        self.change.setFocus()
        self.current.shown()

    def sync_deck_files(self, game: Path, paths: tuple[Path, ...]) -> bool:
        return self.pages["cards"].sync_deck_files(game, paths)

    def choose_folder(self, initial: str) -> str | None:
        return (
            QFileDialog.getExistingDirectory(self, "Select your EDOPro game folder", initial)
            or None
        )

    def confirm(self, title: str, message: str) -> bool:
        return (
            QMessageBox.question(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and obj in self.nav.values():
            buttons = list(self.nav.values())
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if event.key() == Qt.Key.Key_Down else -1
                buttons[(buttons.index(obj) + step) % len(buttons)].setFocus()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.content.currentWidget() is self.picker:
                self.picker.close_picker()
            elif not self.current.escape():
                self.close()
            event.accept()
        elif (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and self.current.id == "cards"
            and QApplication.focusWidget() is None
        ):
            if self.current.state == "options":
                self.current.start_sync()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.rail.setFixedWidth(t.RAIL_COMPACT if self.width() < t.COMPACT_WIDTH else t.RAIL_WIDTH)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.current.request_close():
            event.ignore()
            return
        if self.ctx.tasks.jobs:
            self.closing = True
            self.central.setEnabled(False)
            self.ctx.tasks.cancel_all()
            event.ignore()
            return
        geometry.save(self, self.settings)
        event.accept()

    def _idle(self) -> None:
        if self.closing:
            QTimer.singleShot(0, self.close)
