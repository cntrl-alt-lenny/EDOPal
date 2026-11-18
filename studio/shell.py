import asyncio
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPropertyAnimation, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

import config
from artwork_flow import compact_path
from studio import theme as t
from studio.context import AppContext, Installation
from studio.icons import icon
from studio.installations import InstallationPicker
from studio.pages.backup import BackupPage
from studio.pages.base import Page, PageRegistry
from studio.pages.cards import CardsPage
from studio.pages.decks import DecksPage
from studio.pages.formats import FormatsPage
from studio.pages.health import HealthPage
from studio.pages.music import MusicPage
from studio.pages.mydecks import MyDecksPage
from studio.pages.practice import PracticePage
from studio.pages.replays import ReplaysPage
from studio.pages.settings import SettingsPage
from studio.pages.skins import SkinsPage
from studio.pages.sleeves import SleevesPage
from studio.pages.sounds import SoundsPage
from studio.tasks import WorkerContext
from studio.widgets import Button, ElidedLabel, Hero, Notice, ProgressBlock, glyph, label, margins
from studio.widgets.controls import confirm_action
from studio.widgets.sidebar import Sidebar
from studio.widgets.window import WorkspaceScroll, restore_window, save_window

PAGES = (
    CardsPage,
    SkinsPage,
    SleevesPage,
    SoundsPage,
    MusicPage,
    DecksPage,
    MyDecksPage,
    FormatsPage,
    PracticePage,
    ReplaysPage,
    BackupPage,
    HealthPage,
    SettingsPage,
)
RELEASES = "https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases/latest"


class StudioWindow(QMainWindow):
    def __init__(self, context: AppContext, *, startup: bool = True) -> None:
        super().__init__()
        self.ctx = context
        self.ctx.attach(self)
        self.destroyed.connect(context.detach)
        self.ctx.installations_requested.connect(self.open_picker)
        self.setWindowTitle(config.APP_NAME)
        self.setWindowIcon(icon("brand"))
        self.settings = context.state
        self.ctx.select_installation_requested.connect(self.select_installation)
        self.ctx.update_requested.connect(self.check_updates)
        self.closing = False
        self.generation = 0
        self.pages = PageRegistry(PAGES, self._build_page)
        self.central = QWidget()
        self.central.setObjectName("canvas")
        self.setCentralWidget(self.central)
        outer = QHBoxLayout(self.central)
        margins(outer, 0)
        outer.setSpacing(0)
        self.rail = Sidebar(PAGES, self.ctx.version, glyph("mark", t.BRAND_ICON + t.XS))
        self.nav = self.rail.nav
        self.rail_scroll = self.rail.rail_scroll
        self.release = self.rail.release
        self.rail.selected.connect(self.navigate)
        self.release.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES)))
        outer.addWidget(self.rail)
        self.reveal_timer = QTimer(self)
        self.reveal_timer.setSingleShot(True)
        self.reveal_timer.timeout.connect(self._reveal_current)
        self.rail_scroll.viewport().installEventFilter(self)
        for button in self.nav.values():
            button.installEventFilter(self)
        self.stack = QStackedWidget()
        self.workspace_scroll = WorkspaceScroll()
        self.workspace = self.workspace_scroll.widget()
        outer.addWidget(self.workspace_scroll, 1)
        column = QVBoxLayout(self.workspace)
        margins(column, 0)
        column.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        column.setSpacing(0)
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
        self.stack.setCurrentWidget(self.pages["cards"])
        self.ctx.installation_changed.connect(self._display_folder)
        self.ctx.folder_unavailable.connect(self._unavailable_folder)
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
        QApplication.instance().focusChanged.connect(self.workspace_scroll.reveal_focus)
        self._display_folder(context.installation)
        restore_window(self, self.settings)
        from main import _parse_version

        saved = self.settings.value("updates/latest", "", type=str)
        if _parse_version(saved) > _parse_version(self.ctx.version):
            self.ctx.latest = saved
            self._release_status(cached=True)
        if startup:
            self._startup()

    def _build_page(self, page_type: type[Page]) -> Page:
        was_active = self.isActiveWindow()
        page = page_type(self.ctx)
        self.stack.addWidget(page)
        page.installation_changed(self.ctx.installation)
        self.ctx.installation_changed.connect(page.installation_changed)
        if was_active:
            # Transient, unparented controls created by a page can briefly take focus.
            self.activateWindow()
        return page

    @property
    def current(self) -> Page:
        return self.stack.currentWidget()

    def _install_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("installBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(t.XXL, t.MD, t.XXL, t.MD)
        row.setSpacing(t.MD)
        row.addWidget(glyph("folder", t.ICON_BIG))
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        copy.addWidget(label("EDOPro folder", "kicker"))
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

        choose_on_start = self.ctx.state.value("games/chooseOnStart", False, type=bool)

        def inspect(_: WorkerContext) -> Installation:
            if not explicit and choose_on_start:
                return Installation()
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

        self.check_updates()

    def check_updates(self) -> None:
        if self.ctx.checking_update:
            return
        self.ctx.checking_update = True
        self.ctx.update_offline = False
        self.ctx.update_status = "Checking GitHub Releases…"
        self.ctx.update_changed.emit()

        async def check() -> str | None:
            from main import _create_http_session, check_for_update

            async with _create_http_session() as session:
                return await check_for_update(session, self.ctx.version, strict=True)

        job = self.ctx.tasks.start(lambda _: asyncio.run(check()))
        job.result.connect(self._update)
        job.error.connect(self._update_error)

    def _update_error(self, _exc: Exception) -> None:
        self.ctx.update_offline = True
        self.ctx.checking_update = False
        self.ctx.update_status = (
            "Could not reach GitHub Releases. Check your connection and try again."
        )
        self._release_status(cached=True)

    @Slot(object)
    def _update(self, latest: str | None) -> None:
        self.ctx.update_offline = False
        self.ctx.checking_update = False
        self.ctx.latest = latest or ""
        self.ctx.update_status = (
            f"Version {latest} is available." if latest else "You have the latest release."
        )
        self.settings.setValue("updates/latest", self.ctx.latest)
        self.settings.setValue(
            "updates/lastCheck", datetime.now().astimezone().isoformat(timespec="seconds")
        )
        self._release_status(cached=False)

    def _release_status(self, *, cached: bool) -> None:
        latest = self.ctx.latest
        label = f"Last known update {latest}" if cached else f"Download version {latest}"
        self.ctx.cached_release = cached
        self.ctx.release_label = label
        if cached:
            stamp = self.settings.value("updates/lastCheck", "", type=str)
            try:
                when = datetime.fromisoformat(stamp).astimezone().strftime("%d %b %Y at %H:%M")
            except ValueError:
                when = ""
            if when or latest:
                self.ctx.update_status = " ".join(
                    filter(
                        None,
                        (
                            self.ctx.update_status,
                            f"Last successful check {when}."
                            if when
                            else "Date of last check unavailable.",
                        ),
                    )
                )
        self.release.setAccessibleName(label)
        self.release.setToolTip(self.release.accessibleName())
        self._rail_mode()
        self.release.setVisible(bool(latest))
        self.ctx.update_changed.emit()

    def _select(self, page: Page) -> None:
        for key, button in self.nav.items():
            chosen = key == page.id
            button.setChecked(chosen)
            button.setIcon(icon(self.pages.types[key].icon, t.GOLD if chosen else t.MUTED))

        self._fit_nav_width()
        if page.id != "settings":
            self.rail_scroll.ensureWidgetVisible(self.nav[page.id], 0, t.XS)

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
        name = Path(installation.path).name if installation.path else "No EDOPro folder selected"
        self.folder_name.setText(
            self.folder_name.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideMiddle, t.RAIL_WIDTH
            )
        )
        path = installation.path
        self.path.set_value(path, compact_path(path, 30) if len(path) > 62 else path)
        self.folder_name.setToolTip(path)
        colour = t.GREEN if installation.ready else t.GOLD
        self.status_dot.setStyleSheet(f"background: {colour}; border-radius: {t.XS}px;")
        self.change.setText("Change folder" if installation.path else "Choose folder")

    @Slot(str)
    def _unavailable_folder(self, path: str) -> None:
        if path == self.ctx.installation.path:
            self.status_dot.setStyleSheet(f"background: {t.GOLD}; border-radius: {t.XS}px;")
            self.folder_name.setText("Folder unavailable")
            self.folder_name.setToolTip(path)

    def _busy(self, busy: bool) -> None:
        self.change.setEnabled(not busy)
        self.change.setToolTip("You can change folders when this finishes." if busy else "")

    def _notice(self, message: str) -> None:
        self.notice.show_message(message)
        self.notice_timer.start(t.NOTICE_MS)

    def open_picker(self) -> None:
        self.select_installation("")

    def select_installation(self, path: str) -> None:
        if self.ctx.busy or not self.current.hidden():
            return
        if self.content.currentWidget() is self.picker:
            return
        self.picker_opener = self.focusWidget() or self.change
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.content.setCurrentWidget(self.picker)
        self.workspace_scroll.fit(self.size(), page_visible=False)
        self.change.setEnabled(False)
        self.picker.open_picker(path)

    def _end_picker(self) -> None:
        self.stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.content.setCurrentWidget(self.stack)
        self.workspace_scroll.fit(self.size(), page_visible=True)
        self.change.setEnabled(not self.ctx.busy)
        self.current.shown()
        opener = self.picker_opener
        (
            opener if isValid(opener) and opener.isVisible() and opener.isEnabled() else self.change
        ).setFocus()

    def sync_deck_files(self, game: Path, paths: tuple[Path, ...]) -> bool:
        return self.pages["cards"].sync_deck_files(game, paths)

    def choose_folder(self, initial: str) -> str | None:
        return QFileDialog.getExistingDirectory(self, "Choose your EDOPro folder", initial) or None

    def confirm(self, title: str, message: str, affirmative: str | None = None) -> bool:
        return confirm_action(self, title, message, affirmative)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and obj is self.rail_scroll.viewport():
            self._fit_nav_width()
            # The update link and scrollbar can resize the rail independently of the window.
            if not self.closing:
                self.reveal_timer.start(0)
        if (
            event.type() == QEvent.Type.FocusIn
            and obj in self.nav.values()
            and obj != self.nav["settings"]
        ):
            self.rail_scroll.ensureWidgetVisible(obj, 0, t.XS)
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
            else:
                self._page_escape()
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

    def _page_escape(self) -> None:
        page = self.current
        if self.ctx.busy or (
            self.ctx.tasks.jobs and any(p.isVisible() for p in page.findChildren(ProgressBlock))
        ):
            return
        # Sound previews have a dedicated Stop action. Leaving an active clip editor
        # would also cancel its worker through the legacy page handler.
        if page.id == "sounds" and (
            (
                page.state == "overview"
                and page.audio.preview is not None
                and not page.closed.isVisible()
            )
            or (page.state == "event" and page.events.clip_job is not None)
        ):
            return
        if not page.escape():
            for field in page.findChildren(QLineEdit):
                if (
                    field.isVisible()
                    and field.text()
                    and "search" in field.placeholderText().lower()
                ):
                    field.clear()
                    field.setFocus()
                    break

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        short = event.size().height() <= t.COMPACT_HERO_HEIGHT
        if self.property("compactHeroes") != short:
            self.setProperty("compactHeroes", short)
            for hero in self.findChildren(Hero):
                hero.set_compact(short)
        self._rail_mode()

    def _rail_mode(self) -> None:
        self.path.setVisible(not self.rail.fit(self.size()))
        self.workspace_scroll.fit(
            self.size(), page_visible=self.content.currentWidget() is self.stack
        )

    def _fit_nav_width(self) -> None:
        # A scrollbar can appear during keyboard navigation, before the next layout pass.
        self.rail_scroll.widget().setFixedWidth(self.rail_scroll.viewport().width())
        self.rail.entries.activate()

    def _reveal_current(self) -> None:
        if self.current.id != "settings":
            self.rail_scroll.ensureWidgetVisible(self.nav[self.current.id], 0, t.XS)

    def closeEvent(self, event: QCloseEvent) -> None:
        ordered = [self.current, *(p for p in self.pages.built.values() if p is not self.current)]
        if not self.closing and not all(page.request_close() for page in ordered):
            event.ignore()
            return
        self.reveal_timer.stop()
        self.animation.stop()
        save_window(self, self.settings)
        if self.ctx.tasks.jobs:
            self.closing = True
            self.central.setEnabled(False)
            self.ctx.tasks.cancel_all()
            event.ignore()
            return
        event.accept()

    def _idle(self) -> None:
        if self.closing:
            self.close()
