"""The plugin gallery and its in-place detail drawer."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QProgressBar, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from edopal import __version__
from edopal.features.plugins import PluginEntry, PluginRegistry
from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label, rule
from edopal.ui.widgets import PrimaryButton, QuietButton
from edopal.ui.widgets.art import draw_cover, pixmap

_ART = t.ASSETS / "plugins"


class _Screenshot(QWidget):
    def __init__(self, filename, *, contain=False):
        super().__init__()
        self.image = pixmap(_ART / filename if filename else None)
        self.contain = contain
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def height_at(self, width):
        return round(width * self.image.height() / self.image.width()) if not self.image.isNull() else 0

    def paintEvent(self, _event):
        box = QRectF(self.rect())
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            clip = QPainterPath()
            clip.addRoundedRect(box, t.R_SMALL, t.R_SMALL)
            painter.setClipPath(clip)
            if self.image.isNull():
                painter.fillPath(clip, QColor(t.RAISED))
            elif self.contain:
                size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF(0, 0, size.width(), size.height())
                target.moveCenter(box.center())
                painter.drawPixmap(target, self.image, QRectF(self.image.rect()))
            else:
                draw_cover(painter, self.image, box)


class _Card(QFrame):
    def __init__(self, entry):
        super().__init__()
        self.setObjectName("pluginCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(264)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)
        if entry.artwork:
            layout.addWidget(_Screenshot(entry.artwork), 1)
        text = QVBoxLayout()
        text.setContentsMargins(13, 12, 12, 6)
        text.setSpacing(4)
        headline = label(entry.title, "heading")
        headline.setStyleSheet("font-size: 21px;")
        text.addWidget(headline)
        self.description = label(entry.description, "pluginDescription", wrap=True)
        self.description.setFixedHeight(38)
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text.addWidget(self.description)
        text.addWidget(rule())
        row = QHBoxLayout()
        row.setSpacing(6)
        self.status = label("", "pluginStatus")
        row.addWidget(self.status, 1)
        self.details = QuietButton("")
        self.details.setObjectName(f"plugin-details-{entry.id}")
        self.details.setAccessibleName(f"View Details for {entry.title}")
        self.details.setFixedHeight(27)
        self.details.setFixedWidth(105)
        action = QHBoxLayout(self.details)
        action.setContentsMargins(4, 0, 2, 0)
        action.setSpacing(5)
        view = label("View Details", "pluginAction")
        view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        action.addWidget(view)
        arrow = label("")
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        arrow.setPixmap(t.icon("chevron-right", t.GOLD).pixmap(QSize(14, 14)))
        action.addWidget(arrow)
        row.addWidget(self.details)
        text.addLayout(row)
        layout.addLayout(text, 0 if entry.artwork else 1)

    def set_status(self, text, active):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {t.GOLD if active else t.MUTED};")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.details.click()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class _Scrim(QWidget):
    dismissed = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismissed.emit()

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.fillRect(self.rect(), QColor(17, 17, 19, 117))


class _Drawer(QFrame):
    dismissed = Signal()
    toggled = Signal(str, bool)
    install_requested = Signal(str)

    def __init__(self, registry, parent):
        super().__init__(parent)
        self.registry = registry
        self.setObjectName("pluginDrawer")
        self.setAccessibleName("Plugin Details")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        column = QVBoxLayout(self)
        column.setContentsMargins(26, 15, 26, 17)
        column.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(label("Plugins   /   Details", "muted"), 1)
        self.close_button = QuietButton("×")
        self.close_button.setObjectName("pluginDrawerClose")
        self.close_button.setAccessibleName("Close Plugin Details")
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self.dismissed)
        header.addWidget(self.close_button)
        column.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        column.addWidget(self.scroll, 1)
        column.addWidget(rule())
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Plugin Download Progress")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        footer_layout.addWidget(self.progress)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.state = label("", "muted", wrap=True)
        self.state.setMinimumHeight(36)
        actions.addWidget(self.state, 1)
        self.install = PrimaryButton("Install")
        self.install.setObjectName("pluginInstall")
        self.install.setFixedSize(96, 36)
        self.install.clicked.connect(self._install)
        actions.addWidget(self.install)
        self.enable = PrimaryButton("Enable")
        self.enable.setObjectName("pluginEnable")
        self.enable.setFixedSize(96, 36)
        self.enable.clicked.connect(self._toggle)
        actions.addWidget(self.enable)
        footer_layout.addLayout(actions)
        column.addWidget(footer)

        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.dismissed)
        self.entry = None

    def display(self, entry: PluginEntry):
        self.entry = entry
        body = QWidget()
        body.setObjectName("pluginDrawerBody")
        self.scroll.setWidget(body)
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        headline = label(entry.title, "heading", wrap=True)
        headline.setStyleSheet("font-size: 32px;")
        column.addWidget(headline)
        column.addSpacing(4)
        description = label(entry.detail or entry.description, "pluginDescription", wrap=True)
        if description.text():
            column.addWidget(description)
        column.addSpacing(13)
        column.addWidget(rule())

        facts = QHBoxLayout()
        facts.setContentsMargins(0, 6, 0, 3)
        first = QVBoxLayout()
        first.setSpacing(6)
        self.fact_title = label("", "muted")
        self.fact_value = label("", "pluginFact")
        first.addWidget(self.fact_title)
        first.addWidget(self.fact_value)
        facts.addLayout(first, 1)
        author = QVBoxLayout()
        author.setSpacing(6)
        author.addWidget(label("Author", "muted"))
        author.addWidget(label("EDOPal", "pluginFact"))
        facts.addLayout(author, 1)
        column.addLayout(facts)
        column.addWidget(rule())
        screenshots = entry.screenshots or (((entry.artwork, f"{entry.title} Concept"),) if entry.artwork else ())
        if screenshots:
            column.addWidget(label("Screenshots", "heading"))
            for filename, caption in screenshots:
                shot = _Screenshot(filename, contain=True)
                shot.setFixedHeight(shot.height_at(417))
                shot.setAccessibleName(caption)
                column.addWidget(shot)
                column.addWidget(label(caption, "small"))
        column.addStretch()
        self.scroll.verticalScrollBar().setValue(0)

    def refresh(self, pack, transfer, error):
        plugin_id = self.entry.id
        installed = self.registry.is_installed(plugin_id)
        enabled = self.registry.is_enabled(plugin_id)
        self.fact_title.setText("Version" if installed else "Status")
        self.fact_value.setText((pack.version if pack else __version__) if installed else self.entry.stage)
        self.enable.setVisible(installed)
        self.enable.setText("Disable" if enabled else "Enable")
        self.install.setVisible(not installed and (bool(pack) or self.entry.stage == "In Development"))
        self.install.setEnabled(bool(pack) and transfer is None)
        self.progress.setVisible(transfer is not None)
        if transfer is not None:
            done, total = transfer
            self.progress.setRange(0, 100 if total else 0)
            if total:
                self.progress.setValue(min(100, round(done * 100 / total)))
            message = "Verifying…" if total and done >= total else "Downloading…"
        elif error:
            message = "Download Failed"
        elif installed:
            message = "Installed · Enabled" if enabled else "Installed · Disabled"
        else:
            message = "Not Installed" if pack else self.entry.stage
        self.state.setText(message)
        if not installed and pack:
            self.fact_value.setText("Available")

    def _install(self):
        self.install_requested.emit(self.entry.id)

    def _toggle(self):
        self.toggled.emit(self.entry.id, not self.registry.is_enabled(self.entry.id))


class PluginsPage(ShellPage):
    toggle_requested = Signal(str, bool)

    def __init__(self, registry: PluginRegistry, core=None):
        super().__init__("Plugins")
        self.registry = registry
        self.core = core
        self.packs = {}
        self.downloads = {}
        self.errors = set()
        self._loaded = False
        self.setStyleSheet(
            f"""
            QFrame#pluginCard {{ background: {t.PANEL}; border: 1px solid #444447; border-radius: 6px; }}
            QFrame#pluginCard[chosen="true"] {{ border-color: #b99861; }}
            QFrame#pluginDrawer, QWidget#pluginDrawerBody {{ background: #262629; }}
            QFrame#pluginDrawer {{ border-left: 1px solid #65615b; }}
            QWidget#pluginDrawer QScrollArea {{ background: #262629; }}
            QPushButton#pluginDrawerClose {{ background: {t.RAISED}; border-color: {t.EDGE}; }}
            QLabel[role="pluginDescription"] {{ color: {t.TEXT}; font-size: 13px; }}
            QFrame#pluginDrawer QLabel[role="pluginDescription"] {{ font-size: 15px; }}
            QLabel[role="pluginStatus"], QLabel[role="pluginAction"] {{ font-size: 12px; }}
            QLabel[role="pluginFact"] {{ font-size: 15px; font-weight: 600; }}
            """
        )
        self.count = label("", "small")
        self.count.setStyleSheet("font-size: 13px;")
        self.title_row.addWidget(self.count)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.addWidget(scroll, 1)
        content = QWidget()
        scroll.setWidget(content)
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        self.cards = {}
        for i, entry in enumerate(registry.entries):
            card = _Card(entry)
            card.details.clicked.connect(lambda _checked=False, item=entry: self.show_details(item))
            self.cards[entry.id] = card
            grid.addWidget(card, i // 3, i % 3)
        for i in range(3):
            grid.setColumnStretch(i, 1)

        self.selected = None
        self.scrim = _Scrim(self)
        self.scrim.dismissed.connect(self.close_details)
        self.scrim.hide()
        self.drawer = _Drawer(registry, self)
        self.drawer.dismissed.connect(self.close_details)
        self.drawer.toggled.connect(self.toggle_requested)
        self.drawer.install_requested.connect(self._install)
        self.drawer.hide()
        self.refresh()

    def refresh(self):
        enabled = sum(self.registry.is_enabled(key) for key in self.cards)
        self.count.setText(f"{len(self.cards)} Plugins  ·  {enabled} Enabled")
        for entry in self.registry.entries:
            active = self.registry.is_enabled(entry.id)
            if active:
                status = "✓  Enabled"
            elif self.registry.is_installed(entry.id):
                status = "Installed"
            else:
                status = "Available" if entry.id in self.packs else entry.stage
            self.cards[entry.id].set_status(status, active)
        if self.selected:
            self._refresh_drawer()

    def showEvent(self, event):
        super().showEvent(event)
        # The shell constructs the gallery with its registry before adding it to the stack.
        if self.core is None:
            self.core = getattr(self.window(), "core", None)
        if self._loaded or not self.core or not self.registry.packages:
            return
        self._loaded = True
        self.core.jobs.run(
            partial(_find_packages, self.core.packs, dict(self.registry.packages)),
            key="plugins.catalog",
            on_done=self._packages_ready,
            on_error=self._catalog_failed,
        )

    def _packages_ready(self, result):
        self.packs, installed = result
        for plugin_id in installed:
            self._mark_installed(plugin_id)
        self.refresh()

    def _catalog_failed(self, exc):
        self._loaded = False
        self.core.notify(f"Couldn’t load plugin packages. {exc}", "error")

    def _refresh_drawer(self):
        plugin_id = self.selected
        self.drawer.refresh(self.packs.get(plugin_id), self.downloads.get(plugin_id), plugin_id in self.errors)

    def _install(self, plugin_id):
        pack = self.packs.get(plugin_id)
        if not pack or plugin_id in self.downloads or self.registry.is_installed(plugin_id):
            return
        self.errors.discard(plugin_id)
        self._progress(plugin_id, 0, 0)
        self.core.jobs.run(
            partial(_download_package, self.core.packs, pack),
            key=f"plugins.install.{plugin_id}",
            on_done=lambda _: self._installed(plugin_id),
            on_error=lambda exc: self._install_failed(plugin_id, exc),
            on_progress=lambda done, total, _text: self._progress(plugin_id, done, total),
        )

    def _progress(self, plugin_id, done, total):
        self.downloads[plugin_id] = (done, total)
        if self.selected == plugin_id:
            self._refresh_drawer()

    def _mark_installed(self, plugin_id):
        self.registry.record_install(plugin_id)
        if self.registry.preferred_enabled(plugin_id) and not self.registry.is_enabled(plugin_id):
            self.toggle_requested.emit(plugin_id, True)

    def _installed(self, plugin_id):
        self.downloads.pop(plugin_id, None)
        self._mark_installed(plugin_id)
        self.refresh()

    def _install_failed(self, plugin_id, exc):
        self.downloads.pop(plugin_id, None)
        self.errors.add(plugin_id)
        self.core.notify(f"Couldn’t install the plugin. {exc}", "error")
        self.refresh()

    def show_details(self, entry):
        if self.selected:
            self._mark(self.selected, False)
        self.selected = entry.id
        self._mark(entry.id, True)
        self.drawer.display(entry)
        self._refresh_drawer()
        self.scrim.setGeometry(self.rect())
        self.drawer.setGeometry(self.width() - min(self.width(), 479), 0, min(self.width(), 479), self.height())
        self.scrim.show()
        self.scrim.raise_()
        self.drawer.show()
        self.drawer.raise_()
        self.drawer.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def close_details(self):
        if self.selected:
            card = self.cards[self.selected]
            self._mark(self.selected, False)
            self.selected = None
            self.drawer.hide()
            self.scrim.hide()
            card.details.setFocus(Qt.FocusReason.OtherFocusReason)

    def _mark(self, key, chosen):
        card = self.cards[key]
        card.setProperty("chosen", chosen)
        card.style().unpolish(card)
        card.style().polish(card)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "drawer"):
            width = min(self.width(), 479)
            self.scrim.setGeometry(self.rect())
            self.drawer.setGeometry(self.width() - width, 0, width, self.height())

    def preview_state(self, name):
        if entry := next((entry for entry in self.registry.entries if entry.id == name), None):
            self.show_details(entry)


def _find_packages(service, package_ids, ctx):
    catalogue = {pack.id: pack for pack in service.catalog("plugin")}
    packs, installed = {}, set()
    for plugin_id, pack_id in package_ids.items():
        if ctx.cancelled:
            break
        if pack := catalogue.get(pack_id):
            packs[plugin_id] = pack
        if service.cached(pack_id):
            installed.add(plugin_id)
    return packs, installed


def _download_package(service, pack, ctx):
    return service.ensure(pack, progress=lambda done, total: ctx.progress(done, total), cancel=lambda: ctx.cancelled)
