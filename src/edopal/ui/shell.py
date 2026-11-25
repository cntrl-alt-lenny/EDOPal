"""The EDOPal window, navigation and installation status."""

from __future__ import annotations

import logging
import weakref
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from edopal import APP_NAME
from edopal.core import Core, updates
from edopal.core.feature import FeatureSpec
from edopal.core.install import RunState
from edopal.features.plugins import PluginRegistry
from edopal.ui import theme as t
from edopal.ui.folder_picker import FolderPicker
from edopal.ui.page_chrome import rule
from edopal.ui.release_button import ReleaseButton
from edopal.ui.widgets import Hero, Notice, QuietButton, page_layout

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
        row.setContentsMargins(11, 0, 8, 0)
        row.setSpacing(10)
        self.mark = QLabel()
        self.mark.setFixedSize(19, 19)
        self.mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.mark)
        self.title = _label(label, "navTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.title, 1)
        self.badge = _label("", "navBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.badge)
        self.badge.hide()
        self.compact = False
        self.toggled.connect(self._tint)
        self._tint(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_title()

    def _fit_title(self):
        """Drop leading words ("Card Backs & Icons" -> "Backs & Icons") before eliding a long label."""
        label, room = self.accessibleName(), self.title.width()
        words = label.split()
        metrics = self.title.fontMetrics()
        for start in range(len(words)):
            candidate = " ".join(words[start:])
            if candidate.startswith("&"):
                continue
            if metrics.horizontalAdvance(candidate) <= room:
                break
        else:
            candidate = metrics.elidedText(label, Qt.TextElideMode.ElideRight, room)
        if self.title.text() != candidate:
            self.title.setText(candidate)

    def _tint(self, checked):
        color = t.GOLD if checked else t.TEXT
        self.mark.setPixmap(
            t.icon(self.glyph, t.GOLD if checked else t.MUTED).pixmap(QSize(19, 19), self.devicePixelRatioF())
        )
        self.title.setStyleSheet(f"color: {color}; font-weight: {650 if checked else 500};")
        self._fit_title()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked() and not self.property("keyboardFocus"):
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.GOLD))
                painter.drawRoundedRect(0, 10, 3, 18, 1, 1)

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
        margins = (1, 0, 1, 0) if compact else (11, 0, 8, 0)
        self.layout().setContentsMargins(*margins)
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
        self.plugins = PluginRegistry(specs, core.settings)
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
        self._compact = False
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
        self.header = self._header()
        column.addWidget(self.header)
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
        default = next((spec.id for spec in specs if spec.kind == "feature"), "plugins")
        self.navigate(start if start in self.nav else default)
        self.poll_timer = QTimer(self, interval=3000)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self._poll()

    def _sidebar(self):
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(t.RAIL_COMPACT)
        column = QVBoxLayout(rail)
        column.setContentsMargins(0, 0, 1, 0)
        column.setSpacing(0)

        brand = QWidget()
        brand.setObjectName("sidebarBrand")
        brand.setFixedHeight(134)
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 11, 0, 0)
        brand_layout.setSpacing(0)
        mark = _label("", "")
        mark.setFixedSize(43, 43)
        mark.setPixmap(t.icon("logo", t.GOLD).pixmap(QSize(43, 43), self.devicePixelRatioF()))
        brand_layout.addWidget(mark, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.wordmark = _label(APP_NAME, "brand")
        self.wordmark.setFixedHeight(26)
        brand_layout.addWidget(self.wordmark, alignment=Qt.AlignmentFlag.AlignHCenter)
        brand_layout.addSpacing(8)
        self.release = ReleaseButton(self.devicePixelRatioF())
        self.release.clicked.connect(self._open_release)
        brand_layout.addWidget(self.release, alignment=Qt.AlignmentFlag.AlignHCenter)
        brand_layout.addStretch()
        column.addWidget(brand)

        self.group = QButtonGroup(self)
        scroll = QScrollArea()
        scroll.setObjectName("railScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        entries = QWidget()
        entries.setObjectName("railItems")
        scroll.setWidget(entries)
        sections = QVBoxLayout(entries)
        sections.setContentsMargins(10, 0, 10, 0)
        sections.setSpacing(0)
        library = {}
        for spec in self.specs.values():
            if spec.kind == "feature":
                library.setdefault(spec.section or "Library", []).append(spec)
        self.section_labels = []
        for section, specs in library.items():
            self._section_heading(sections, section)
            for spec in specs:
                sections.addWidget(self._add_nav(spec.id, spec.label, spec.icon, spec.summary))
                sections.addSpacing(3)
            sections.addSpacing(12)

        self._section_heading(sections, "Plugins")
        self.plugin_entries = QVBoxLayout()
        self.plugin_entries.setSpacing(3)
        sections.addLayout(self.plugin_entries)
        self._refresh_plugin_nav()
        sections.addStretch()
        column.addWidget(scroll, 1)

        footer = QVBoxLayout()
        footer.setContentsMargins(10, 0, 10, 19)
        footer.setSpacing(0)
        self._sidebar_rule(footer)
        footer.addSpacing(15)
        footer.addWidget(self._add_nav("plugins", "Browse Plugins", "gallery", "Browse EDOPal plugins"))
        footer.addSpacing(5)
        footer.addWidget(self._add_nav("settings", "Settings", "settings", "Settings, updates and credits"))
        column.addLayout(footer)
        return rail

    def _sidebar_rule(self, column):
        inset = QHBoxLayout()
        inset.setContentsMargins(10, 0, 10, 0)
        inset.addWidget(rule())
        column.addLayout(inset)

    def _section_heading(self, column, title):
        self._sidebar_rule(column)
        caption = _label(title, "railCaption")
        caption.setContentsMargins(14, 0, 0, 0)
        caption.setFixedHeight(43)
        column.addWidget(caption)
        self.section_labels.append(caption)

    def _refresh_plugin_nav(self):
        enabled = [spec for key, spec in self.plugins.specs.items() if self.plugins.is_enabled(key)]
        enabled_ids = {spec.id for spec in enabled}
        for key in self.plugins.specs.keys() - enabled_ids:
            if key in self.nav:
                button = self.nav.pop(key)
                self.plugin_entries.removeWidget(button)
                self.group.removeButton(button)
                button.hide()
                button.deleteLater()
        for i, spec in enumerate(enabled):
            if spec.id not in self.nav:
                button = self._add_nav(spec.id, spec.label, spec.icon, spec.summary)
                self.plugin_entries.insertWidget(i, button)
                button.set_compact(self._compact)

    def _set_plugin_enabled(self, key, enabled):
        if self._closed:
            return
        try:
            changed = self.plugins.set_enabled(key, enabled)
        except OSError as exc:
            self.notify(f"Couldn’t save plugin settings. {exc}", "error")
            return
        if not changed:
            return
        if not enabled and key in self.pages:
            if self.current_id == key:
                self.navigate("plugins")
            page = self.pages.pop(key)
            self.stack.removeWidget(page)
            page.deleteLater()
        self._refresh_plugin_nav()
        if changed_hook := getattr(self.specs[key], "on_enabled_changed", None):
            changed_hook(self.core, enabled)
        if gallery := self.pages.get("plugins"):
            gallery.refresh()
        self._refresh_badges()

    def _add_nav(self, key, label, glyph, summary):
        button = _NavButton(key, label, glyph, summary)
        self.nav[key] = button
        self.group.addButton(button)
        button.clicked.connect(lambda _checked=False: self.navigate(key))
        return button

    def _open_release(self):
        if not QDesktopServices.openUrl(QUrl(self.release.url)):
            self.notify("Couldn’t open EDOPal releases.", "warning")

    def check_updates(self):
        if self._closed:
            return
        self.release.set_result(None)
        cache = self.core.paths.cache
        self.core.jobs.run(
            lambda _: updates.check_for_updates(cache),
            key="shell.release",
            on_done=self._show_release,
            on_error=lambda _: self._show_release(updates.UpdateCheck(None, False, True)),
        )

    def _show_release(self, result):
        if not self._closed:
            self.release.set_result(result)

    def _header(self):
        header = QFrame()
        header.setObjectName("installBar")
        header.setFixedHeight(58)
        row = QHBoxLayout(header)
        row.setContentsMargins(t.CONTENT_MARGIN, 0, t.CONTENT_MARGIN, 1)
        row.setSpacing(12)

        self.change = QuietButton("")
        self.change.setObjectName("changeFolder")
        self.change.setAccessibleName("Change Game Folder")
        self.change.setFixedHeight(34)
        self.change.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.change.clicked.connect(self._open_picker)
        folder = QHBoxLayout(self.change)
        folder.setContentsMargins(0, 0, 6, 0)
        folder.setSpacing(10)
        icon = _label("", "")
        icon.setFixedSize(18, 18)
        icon.setPixmap(t.icon("folder", t.TEXT).pixmap(QSize(18, 18), self.devicePixelRatioF()))
        folder.addWidget(icon)
        title = _label("Game Folder", "muted")
        title.setFixedWidth(76)
        folder.addWidget(title)
        separator = QFrame()
        separator.setFixedSize(1, 16)
        separator.setStyleSheet("background: #444446;")
        folder.addWidget(separator)
        folder.addSpacing(6)
        self.folder_path = _ElidedLabel("folderPath")
        folder.addWidget(self.folder_path, 1)
        arrow = _label("", "")
        arrow.setFixedSize(13, 13)
        arrow.setPixmap(t.icon("chevron-down", t.MUTED).pixmap(QSize(13, 13), self.devicePixelRatioF()))
        folder.addWidget(arrow)
        for child in (icon, title, separator, self.folder_path, arrow):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.change)
        self.folder_state = _label("", "folderState")
        row.addWidget(self.folder_state)
        row.addStretch()

        self.launch = QuietButton("Launch EDOPro")
        self.launch.setObjectName("launchGame")
        self.launch.setFixedHeight(32)
        self.launch.setMinimumWidth(147)
        self.launch.setIconSize(QSize(13, 13))
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

                page = SettingsPage(self.core, choose_game=self._open_picker)
            elif key == "plugins":
                from edopal.features.plugins.page import PluginsPage

                page = PluginsPage(self.plugins)
                page.toggle_requested.connect(self._set_plugin_enabled)
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
            if spec.badge and spec.id in self.nav:
                self.nav[spec.id].set_badge(spec.badge(self.core))

    def _hooks(self, name):
        for spec in self.specs.values():
            if spec.id not in self.nav:
                continue
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
        if settings := self.pages.get("settings"):
            settings.set_install(install)
        self._hooks("on_install_changed")
        self._poll()

    def _display_install(self):
        install = self._install
        if install:
            home = Path.home()
            path = f"~/{install.root.relative_to(home)}" if install.root.is_relative_to(home) else str(install.root)
            status, color = ("Ready", t.GREEN) if install.ready else ("Waiting for Databases", t.GOLD)
        else:
            path = "Choose EDOPro Installation"
            status, color = ("Missing", t.RED)
        self.folder_path.set_value(path)
        self.folder_path.setFixedWidth(min(348, self.folder_path.fontMetrics().horizontalAdvance(path) + 3))
        self.change.setFixedWidth(self.change.layout().sizeHint().width())
        self.change.setToolTip(str(install.root) if install else path)
        self.change.setAccessibleDescription(f"{status} · {install.root if install else path}")
        self.folder_state.setText(f"●  {status}")
        self.folder_state.setStyleSheet(f"color: {color};")
        self.folder_state.setVisible(not install or not install.ready)
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
        self.launch.setText(text)
        self.launch.setToolTip(tip)
        self.launch.setEnabled(enabled)
        colour = t.INK if enabled else t.MUTED
        running = state == RunState.RUNNING
        self.launch.setFixedWidth(107 if running else 147)
        glyph_icon = t.icon("status-dot", t.GREEN) if running else t.icon("play", colour)
        self.launch.setIcon(glyph_icon)
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

        tracker = self.plugins.specs.get("deck-tracker") if self.plugins.is_enabled("deck-tracker") else None

        def launch_failed(exc):
            if generation == self._generation and not self._closed and tracker and tracker.on_game_exited:
                tracker.on_game_exited(self.core)
            finished(f"Couldn’t launch EDOPro. {exc}", False)

        def dispatch():
            if generation == self._generation and not self._closed:
                self.core.jobs.run(
                    lambda _: launch(),
                    on_done=lambda result: finished(result, True),
                    on_error=launch_failed,
                    key="shell.launch",
                )

        hook = getattr(tracker, "before_launch", None)
        if hook:
            hook(self.core, dispatch, lambda message: finished(message, False))
        else:
            dispatch()

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
            width = self.width()
            self._compact = width < t.COMPACT_WIDTH
            wide = width > t.WIDE_WIDTH
            self.rail.setFixedWidth(t.RAIL_ICONS if self._compact else t.RAIL_WIDTH if wide else t.RAIL_COMPACT)
            self.release.set_compact(self._compact)
            for caption in self.section_labels:
                caption.setVisible(not self._compact)
            for button in self.nav.values():
                button.set_compact(self._compact)
            self.header.setFixedHeight(68 if wide else 58)
            margin = 36 if wide else t.CONTENT_MARGIN
            self.header.layout().setContentsMargins(margin, 0, margin, 1)
            if self.toast.isVisible():
                self.toast.position()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.poll_timer.stop()
        if self.picker:
            self.picker.done(FolderPicker.DialogCode.Rejected)
        if spec := self.plugins.specs.get("deck-tracker"):
            if stop_tracker := getattr(spec, "on_shutdown", None):
                stop_tracker(self.core)
        if shutdown := getattr(self.core.jobs, "shutdown", None):
            shutdown()
        self.core.notify, self.core.navigate = self._old_notify, self._old_navigate

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def _window_style():
    return f"""
        QFrame#rail {{ background: {t.RAIL}; border-right: 1px solid #38383b; }}
        QFrame#installBar {{ background: #1c1c1e; border-bottom: 1px solid {t.RULE}; }}
        QLabel[role="railCaption"] {{ color: {t.MUTED}; font-size: 12px; font-weight: 600; }}
        QLabel[role="folderPath"] {{ font-size: 13px; }}
        QLabel[role="folderState"] {{ font-size: 11px; font-weight: 550; }}
        QPushButton#changeFolder {{ padding: 0; }}
        QPushButton#launchGame {{
            background: {t.PANEL}; border-color: #505053; padding: 0 14px; font-size: 13px;
        }}
        QPushButton#launchGame:hover:enabled {{ background: {t.HOVER}; }}
        QPushButton#launchGame:disabled {{ color: {t.TEXT}; }}
        QPushButton#releaseStatus {{
            background: #252527; border-color: #414143; border-radius: 13px; padding: 0;
        }}
        QPushButton#releaseStatus:hover {{ background: #343433; border-color: #837457; }}
        QPushButton#releaseStatus[updateAvailable="true"] {{
            background: {t.GOLD}; border-color: {t.GOLD}; border-radius: 5px;
        }}
        QPushButton#releaseStatus[updateAvailable="true"]:hover {{ background: {t.PALE}; }}
        QLabel[role="releaseTitle"] {{ color: {t.TEXT}; font-size: 12px; }}
        QPushButton#releaseStatus[keyboardFocus="true"]:focus,
        QPushButton#launchGame[keyboardFocus="true"]:focus {{ border: 2px solid {t.PALE}; }}
        QLabel[role="navBadge"] {{ color: {t.PALE}; background: {t.ACTIVE}; border-radius: 6px;
            font-weight: 650; font-size: 11px; padding: 2px 5px; }}
        QFrame#toast {{ background: {t.RAISED}; border: 1px solid {t.EDGE}; border-radius: 9px; }}
    """
