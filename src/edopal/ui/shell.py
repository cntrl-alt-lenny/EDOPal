"""The EDOPal window, navigation and installation status."""

from __future__ import annotations

import logging
import weakref
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from edopal import APP_NAME
from edopal.core import Core
from edopal.core.feature import FeatureSpec
from edopal.core.install import RunState
from edopal.ui import theme as t
from edopal.ui.folder_picker import FolderPicker
from edopal.ui.widgets import Hero, Notice, PrimaryButton, QuietButton, page_layout

log = logging.getLogger(__name__)


def unavailable_page(label: str, summary: str):
    def create(core):
        page = QWidget()
        layout = page_layout(page)
        layout.addWidget(Hero("EDOPro companion", label, summary, art=t.ASSETS / "logo-mark.png"))
        layout.addWidget(Notice(f"{label} is unavailable in this development build."))
        layout.addStretch()
        return page

    return create


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    return label


class _ElidedLabel(QLabel):
    """Keep the whole folder name accessible even when the header is narrow."""

    def __init__(self, role):
        super().__init__()
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_value(self, value):
        self.full_text = value
        self.setToolTip(value)
        self.setAccessibleName(value)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, self.width()))


class _NavButton(QuietButton):
    def __init__(self, name, label, glyph, summary):
        super().__init__("")
        self.setObjectName(f"nav-{name}")
        self.setProperty("role", "nav")
        self.setAccessibleName(label)
        self.setToolTip(summary or label)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(t.NAV_H)
        self.glyph = glyph
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 0, 12, 0)
        row.setSpacing(11)
        self.mark = QLabel()
        self.mark.setFixedSize(t.ICON, t.ICON)
        self.mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.mark)
        self.title = _label(label, "navTitle")
        row.addWidget(self.title, 1)
        self.badge = _label("", "navBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.badge)
        self.badge.hide()
        self.compact = False
        self.toggled.connect(self._tint)
        self._tint(False)

    def _tint(self, checked):
        color = t.GOLD if checked else t.TEXT
        self.mark.setPixmap(t.icon(self.glyph, color).pixmap(QSize(t.ICON, t.ICON), self.devicePixelRatioF()))
        self.title.setStyleSheet(f"color: {color}; font-weight: {650 if checked else 550};")

    def set_badge(self, text):
        value = text or ""
        self.badge.setText(self.badge.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, 40))
        self.badge.setToolTip(value)
        self.badge.setVisible(bool(value) and not self.compact)
        self.setAccessibleDescription(value)

    def set_compact(self, compact):
        self.compact = compact
        self.title.setVisible(not compact)
        self.badge.setVisible(bool(self.badge.text()) and not compact)
        self.layout().setAlignment(self.mark, Qt.AlignmentFlag.AlignCenter if compact else Qt.AlignmentFlag.AlignVCenter)


class _Toast(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAccessibleName("Notification")
        row = QHBoxLayout(self)
        row.setContentsMargins(15, 10, 8, 10)
        row.setSpacing(10)
        self.mark = QLabel()
        self.mark.setFixedSize(20, 20)
        row.addWidget(self.mark)
        self.message = _label("", "body")
        self.message.setWordWrap(True)
        row.addWidget(self.message, 1)
        dismiss = QuietButton("×")
        dismiss.setAccessibleName("Dismiss notification")
        dismiss.setFixedSize(32, 32)
        dismiss.clicked.connect(self.hide)
        row.addWidget(dismiss)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def present(self, message, kind):
        glyph, color = {
            "info": ("logo", t.GOLD),
            "success": ("check", t.GREEN),
            "warning": ("warning", t.GOLD),
            "error": ("warning", t.RED),
        }.get(kind, ("logo", t.GOLD))
        icon = QIcon(str(t.ASSETS / "app-icon-small.png")) if glyph == "logo" else t.icon(glyph, color)
        self.mark.setPixmap(icon.pixmap(QSize(20, 20), self.devicePixelRatioF()))
        self.message.setText(message)
        self.setToolTip(message)
        self.setAccessibleDescription(message)
        self.position()
        self.show()
        self.raise_()
        self.timer.start(t.NOTICE_MS)

    def position(self):
        parent = self.parentWidget()
        width = min(
            max(270, self.message.fontMetrics().horizontalAdvance(self.message.text()) + 100), 500, parent.width() - 32
        )
        height = max(54, self.layout().heightForWidth(width))
        self.setGeometry(parent.width() - width - 20, parent.height() - height - 20, width, height)


class ShellWindow(QMainWindow):
    notice_requested = Signal(str, str)
    navigation_requested = Signal(str)
    install_changed = Signal(object)

    def __init__(self, core: Core, specs: list[FeatureSpec], start: str | None = None):
        super().__init__()
        self.core = core
        self.specs = {spec.id: spec for spec in specs}
        self.pages: dict[str, QWidget] = {}
        self.nav: dict[str, _NavButton] = {}
        self.current_id = None
        self.picker = None
        self._closed = False
        self._poll_job = None
        self._generation = 0
        self._launching = False
        self._launch_deadline = 0.0
        self._install = core.installs.current()
        self._state = self._last_known = RunState.UNKNOWN
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(t.ASSETS / "app-icon.png")))
        self.setMinimumSize(*t.MIN_SIZE)
        self.resize(*t.DEFAULT_SIZE)
        self.setStyleSheet(_window_style())
        canvas = QWidget()
        canvas.setObjectName("canvas")
        self.setCentralWidget(canvas)
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.rail = self._sidebar()
        outer.addWidget(self.rail)
        content = QWidget()
        outer.addWidget(content, 1)
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._header())
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        column.addWidget(self.stack, 1)
        self.toast = _Toast(canvas)

        self.notice_requested.connect(self.toast.present)
        self.navigation_requested.connect(self.navigate)
        self.install_changed.connect(self._selection_changed)
        self._old_notify, self._old_navigate = core.notify, core.navigate
        core.notify, core.navigate = self.notify, self.navigation_requested.emit

        # Services can publish selection on a worker, and subscribe() has no disconnect.
        window_ref = weakref.ref(self)

        def selection_updated(install):
            if (window := window_ref()) is not None and not window._closed:
                window.install_changed.emit(install)

        core.installs.subscribe(selection_updated)
        self._display_install()
        self.navigate(start if start in self.nav else next(iter(self.nav)))
        self.poll_timer = QTimer(self, interval=3000)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self._poll()

    def _sidebar(self):
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(196)
        column = QVBoxLayout(rail)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        brand = QFrame()
        brand.setObjectName("brandBar")
        brand.setFixedHeight(80)
        row = QHBoxLayout(brand)
        row.setContentsMargins(15, 0, 14, 0)
        row.setSpacing(9)
        mark = QLabel()
        image = QPixmap(str(t.ASSETS / "logo-mark.png"))
        dpr = self.devicePixelRatioF()
        image = image.scaled(
            round(45 * dpr), round(45 * dpr), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        image.setDevicePixelRatio(dpr)
        mark.setPixmap(image)
        row.addWidget(mark)
        self.wordmark = QWidget()
        lockup = QVBoxLayout(self.wordmark)
        lockup.setContentsMargins(0, 0, 0, 0)
        lockup.setSpacing(1)
        lockup.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lockup.addWidget(_label(APP_NAME, "brand"))
        lockup.addWidget(_label("EDOPro companion", "brandCaption"))
        row.addWidget(self.wordmark, 1)
        column.addWidget(brand)
        entries = QVBoxLayout()
        entries.setContentsMargins(10, 22, 10, 12)
        entries.setSpacing(5)
        column.addLayout(entries, 1)
        self.group = QButtonGroup(self)
        for spec in self.specs.values():
            entries.addWidget(self._add_nav(spec.id, spec.label, spec.icon, spec.summary))
        entries.addStretch()
        line = QFrame()
        line.setProperty("role", "line")
        line.setFixedHeight(1)
        entries.addWidget(line)
        entries.addSpacing(4)
        entries.addWidget(self._add_nav("settings", "Settings & About", "settings", "Settings, updates and credits"))
        return rail

    def _add_nav(self, key, label, glyph, summary):
        button = _NavButton(key, label, glyph, summary)
        self.nav[key] = button
        self.group.addButton(button)
        button.clicked.connect(lambda _checked=False: self.navigate(key))
        return button

    def _header(self):
        header = QFrame()
        header.setObjectName("installBar")
        header.setFixedHeight(80)
        row = QHBoxLayout(header)
        row.setContentsMargins(t.CONTENT_MARGIN, 0, t.CONTENT_MARGIN, 0)
        row.setSpacing(12)
        folder_mark = QLabel()
        folder_mark.setObjectName("folderMark")
        folder_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        folder_mark.setFixedSize(36, 36)
        folder_mark.setPixmap(t.icon("folder", t.GOLD).pixmap(QSize(20, 20), self.devicePixelRatioF()))
        row.addWidget(folder_mark)
        folder = QVBoxLayout()
        folder.setSpacing(3)
        folder.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title = QHBoxLayout()
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title.setSpacing(9)
        self.folder_name = QLabel()
        self.folder_name.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_name.setProperty("role", "folderTitle")
        title.addWidget(self.folder_name)
        self.folder_state = QLabel()
        self.folder_state.setProperty("role", "folderState")
        title.addWidget(self.folder_state)
        folder.addLayout(title)
        self.folder_path = _ElidedLabel("muted")
        folder.addWidget(self.folder_path)
        row.addLayout(folder, 1)
        self.change = QuietButton("Change folder")
        self.change.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change.clicked.connect(self._open_picker)
        row.addWidget(self.change)
        self.launch = PrimaryButton("Launch EDOPro")
        self.launch.setObjectName("launchGame")
        self.launch.setMinimumSize(163, t.BUTTON_H)
        self.launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch.clicked.connect(self._launch)
        row.addWidget(self.launch)
        return header

    def navigate(self, key):
        if self._closed or key not in self.nav:
            return
        if key not in self.pages:
            if key == "settings":
                from edopal.ui.settings_page import SettingsPage

                page = SettingsPage(self.core)
            else:
                spec = self.specs[key]
                try:
                    page = spec.page(self.core)
                except ModuleNotFoundError as exc:
                    if exc.name != f"edopal.features.{key}.page":
                        raise
                    page = unavailable_page(spec.label, spec.summary)(self.core)
            self.pages[key] = page
            self.stack.addWidget(page)
        # Follow navigation initiated by a feature while focus is still in the rail.
        if self.current_id and key != self.current_id and self.nav[self.current_id].hasFocus():
            self.nav[key].setFocus(Qt.FocusReason.OtherFocusReason)
        self.stack.setCurrentWidget(self.pages[key])
        self.current_id = key
        self.nav[key].setChecked(True)
        self._refresh_badges()

    def _refresh_badges(self):
        for spec in self.specs.values():
            if spec.badge:
                self.nav[spec.id].set_badge(spec.badge(self.core))

    def _hooks(self, name):
        for spec in self.specs.values():
            if hook := getattr(spec, name):
                try:
                    hook(self.core)
                except Exception:
                    log.exception("%s hook failed for %s", name, spec.id)
                    self.notify(f"Couldn’t refresh {spec.label}.", "warning")
        self._refresh_badges()

    def notify(self, text, kind="info"):
        if not self._closed:
            self.notice_requested.emit(text, kind)

    def _selection_changed(self, install):
        if self._closed:
            return
        self._generation += 1
        self._install = install
        self._state = self._last_known = RunState.UNKNOWN
        self._launching = False
        self._launch_deadline = 0.0
        self._display_install()
        self._hooks("on_install_changed")
        self._poll()

    def _display_install(self):
        install = self._install
        if install:
            name = install.label or install.root.name or "Game folder"
            home = Path.home()
            path = f"~/{install.root.relative_to(home)}" if install.root.is_relative_to(home) else str(install.root)
            status, color = ("Ready", t.GREEN) if install.ready else ("Waiting for databases", t.GOLD)
        else:
            name, path = "Game folder", "Choose your EDOPro installation"
            status, color = "Missing", t.RED
        self.folder_name.setText(self.folder_name.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 210))
        self.folder_name.setToolTip(name)
        self.folder_path.set_value(path)
        if install:
            self.folder_path.setToolTip(str(install.root))
        self.folder_state.setText(f"●  {status}")
        self.folder_state.setStyleSheet(f"color: {color};")
        self._display_launch()

    def _display_launch(self):
        install = self._install
        state = self._state
        if self._launching or self._launch_deadline:
            text, tip = "Starting…", "Waiting for EDOPro to start."
        elif state == RunState.RUNNING:
            text, tip = "Running", "EDOPro is running."
        elif not install:
            text, tip = "Launch EDOPro", "Choose a game folder to launch EDOPro."
        elif install.launch is None:
            text, tip = "Launch EDOPro", "This folder has no EDOPro launch target."
        else:
            text, tip = "Launch EDOPro", "Start EDOPro from the selected game folder."
        enabled = bool(
            install
            and install.launch is not None
            and state != RunState.RUNNING
            and not self._launching
            and not self._launch_deadline
        )
        glyph = "check" if state == RunState.RUNNING else "launch"
        self.launch.setText(text)
        self.launch.setToolTip(tip)
        self.launch.setEnabled(enabled)
        self.launch.setIcon(t.icon(glyph, t.ON_GOLD if enabled else t.MUTED))
        self.change.setEnabled(not self._launching)

    def _open_picker(self):
        if self.picker:
            self.picker.raise_()
            return
        self.picker = FolderPicker(self.core, self)
        self.picker.finished.connect(self._picker_finished)
        self.picker.open()

    def _picker_finished(self):
        self.picker.deleteLater()
        self.picker = None

    def _launch(self):
        if not self.launch.isEnabled():
            return
        self._launching = True
        self._display_launch()
        generation = self._generation
        launch = self.core.installs.launch

        def finished(message, success):
            if generation != self._generation or self._closed:
                return
            self._launching = False
            self._launch_deadline = monotonic() + 12 if success and self._state != RunState.RUNNING else 0.0
            self._display_launch()
            self.notify(message, "success" if success else "error")

        self.core.jobs.run(
            lambda _: launch(),
            on_done=lambda result: finished(result, True),
            on_error=lambda exc: finished(f"Couldn’t launch EDOPro. {exc}", False),
            key="shell.launch",
        )

    def _poll(self):
        if self._closed:
            return
        if not self._install:
            self._refresh_badges()
            return
        if self._poll_job and self._poll_job.running:
            return
        generation = self._generation
        service = self.core.installs

        def probe(_):
            return service.run_state(), service.current()

        def update(result):
            if self._closed or generation != self._generation:
                return
            state, current = result
            if current != self._install:
                self._selection_changed(current)
                return
            exited = state == RunState.CLOSED and self._last_known == RunState.RUNNING
            if state != RunState.UNKNOWN:
                self._last_known = state
            self._state = state
            if state == RunState.RUNNING or monotonic() >= self._launch_deadline:
                self._launch_deadline = 0.0
            self._display_launch()
            if exited:
                self._hooks("on_game_exited")
                if self._install and not self._install.ready:
                    self._refresh_waiting(self._install)
            else:
                self._refresh_badges()

        self._poll_job = self.core.jobs.run(
            probe, on_done=update, on_error=lambda _: update((RunState.UNKNOWN, self._install)), key="shell.run-state"
        )

    def _refresh_waiting(self, install):
        # A first launch can bootstrap databases. Let an open picker finish the user's choice first.
        if self._closed or self._install != install:
            return
        if self.picker:
            self.picker.finished.connect(lambda: self._refresh_waiting(install))
            return
        select = self.core.installs.select
        self.core.jobs.run(
            lambda _: select(install.root),
            key="shell.select",
            on_error=lambda exc: self.notify(f"Couldn’t refresh the game folder. {exc}", "warning"),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "rail"):
            compact = self.width() < t.COMPACT_WIDTH
            self.rail.setFixedWidth(75 if compact else 196)
            self.wordmark.setVisible(not compact)
            for button in self.nav.values():
                button.set_compact(compact)
            if self.toast.isVisible():
                self.toast.position()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.poll_timer.stop()
        if self.picker:
            self.picker.done(FolderPicker.DialogCode.Rejected)
        if shutdown := getattr(self.core.jobs, "shutdown", None):
            shutdown()
        self.core.notify, self.core.navigate = self._old_notify, self._old_navigate

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def _window_style():
    return f"""
        QFrame#rail {{ background: {t.RAIL}; border-right: 1px solid {t.EDGE}; }}
        QFrame#brandBar, QFrame#installBar {{ border-bottom: 1px solid {t.EDGE}; }}
        QFrame#installBar {{ background: {t.CANVAS}; }}
        QLabel[role="brandCaption"] {{ color: {t.MUTED}; font-size: 11px; }}
        QLabel[role="folderTitle"] {{ font-weight: 650; }}
        QLabel[role="folderState"] {{ font-size: 11px; font-weight: 550; }}
        QLabel#folderMark {{ background: {t.ACTIVE}; border-radius: 9px; }}
        QLabel[role="navBadge"] {{ color: {t.PALE}; background: {t.ACTIVE}; border-radius: 6px;
            font-weight: 650; font-size: 11px; padding: 2px 5px; }}
        QFrame#toast {{ background: {t.RAISED}; border: 1px solid {t.EDGE}; border-radius: 9px; }}
    """
