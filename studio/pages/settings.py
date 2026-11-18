"""Preferences and local maintenance, all within the Studio window."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
from content.cache import PackCache
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.card_views import SettingsView, line, page_layout, scroll
from studio.pages.settings_shortcuts import (
    Shortcut,
    change_shortcut,
    inspect_shortcut,
    shortcut_locations,
)
from studio.pages.settings_storage import Storage, byte_size, inspect_storage
from studio.pages.settings_views import PROJECT, actions, add_about, button, open_url
from studio.tasks import Job
from studio.widgets import ActionTile, ElidedLabel, Hero, Panel, label, margins

SECTIONS = {
    "games": ("EDOPro folders", "Choose which copy of EDOPro EDOPal uses.", "folder"),
    "defaults": ("HD Cards defaults", "Choose what each artwork sync includes.", "cards"),
    "storage": ("Downloads and storage", "See what uses space and manage your packs.", "download"),
    "updates": ("Updates", "Check for a new release.", "spark"),
    "shortcuts": ("Shortcuts", "Open the portable app from your desktop.", "arrow"),
    "about": ("About", "Credits, licenses and project links.", "brand"),
}


class SettingsPage(Page):
    id, title, icon = "settings", "Settings", "settings"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.section = "home"
        self.revision = 0
        self.pending: dict[Job, tuple[int, bool, Callable]] = {}
        self.usage_layout: QBoxLayout | None = None
        self.cache = PackCache.for_config(context.cfg)
        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.addWidget(self.stack)

        home = QWidget()
        body = page_layout(home)
        self.hero = Hero(
            "Preferences",
            "Settings",
            "Manage your game folders, downloads and app preferences.",
            "settings",
        )
        body.addWidget(self.hero)
        body.addWidget(label("Preferences & tools", "heading"))
        self.grid = QGridLayout()
        self.grid.setSpacing(t.MD)
        body.addLayout(self.grid)
        self.tiles: dict[str, ActionTile] = {}
        for i, (key, (title, hint, symbol)) in enumerate(SECTIONS.items()):
            tile = ActionTile(title, hint, symbol)
            tile.clicked.connect(lambda _checked=False, key=key: self.open_section(key))
            self.tiles[key] = tile
            self.grid.addWidget(tile, i // 2, i % 2)
        self.classic = button("Open the classic window", self._classic, "link", "quiet")
        body.addWidget(self.classic, 0, Qt.AlignmentFlag.AlignLeft)
        body.addStretch()
        self.home = scroll(home)
        self.stack.addWidget(self.home)
        self.defaults = SettingsView(context.cfg, global_defaults=True)
        self.defaults.back.clicked.connect(self.home_view)
        self.defaults.done.clicked.connect(
            lambda: context.save_card_preferences(self.defaults.preferences(), self.home_view)
        )
        self.defaults_view = scroll(self.defaults)
        self.stack.addWidget(self.defaults_view)

        detail = QWidget()
        content = page_layout(detail)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.back = button("Back", self.home_view, "back", "quiet")
        self.back.setAccessibleName("Back to Settings")
        content.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        detail.setMaximumWidth(t.CARDS_COLUMNS)
        self.heading = label("", "heading", True)
        content.addWidget(self.heading)
        self.inner = QVBoxLayout()
        self.inner.setSpacing(t.MD)
        content.addLayout(self.inner)
        self.message = label("", "body", True)
        content.addWidget(self.message)
        content.addStretch()
        self.detail = scroll(detail)
        self.stack.addWidget(self.detail)
        context.update_changed.connect(self._update_news)
        self._update_news()
        context.busy_changed.connect(lambda busy: self.stack.setDisabled(busy))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.usage_layout:
            self.usage_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
                if self.width() >= t.CARDS_COLUMNS
                else QBoxLayout.Direction.TopToBottom
            )
        self.hero.setFixedHeight(t.landing_hero_height(self.width(), self.height()))

    def installation_changed(self, installation: Installation) -> None:
        name = Path(installation.path).name if installation.path else "No EDOPro folder selected"
        self.tiles["games"].hint.setText(name)
        self.tiles["games"].hint.setToolTip(installation.path)
        if self.section == "games":
            self.open_section("games")

    def shown(self) -> None:
        if self.section == "home":
            self.tiles["games"].setFocus()
        elif self.section == "defaults":
            self.defaults.back.setFocus()
        else:
            self.open_section(self.section)

    def hidden(self) -> bool:
        return not self.ctx.busy and self._leave_defaults()

    def escape(self) -> bool:
        if self.ctx.busy:
            return True
        if self.section == "home":
            return False
        self.home_view()
        return True

    def request_close(self) -> bool:
        if self.section == "defaults" and self.ctx.busy and self.isVisible():
            return False
        return self._leave_defaults() and not any(
            writing for _, writing, _ in self.pending.values()
        )

    def _leave_defaults(self) -> bool:
        return self.section != "defaults" or self.defaults.discard(self.ctx)

    def home_view(self) -> None:
        if not self._leave_defaults():
            return
        previous = self.section
        self.section = "home"
        self.revision += 1
        self.stack.setCurrentWidget(self.home)
        self.tiles.get(previous, self.tiles["games"]).setFocus()

    def open_section(self, key: str) -> None:
        self.section = key
        self.revision += 1
        if key == "defaults":
            self.defaults.load(self.ctx.cfg)
            self.stack.setCurrentWidget(self.defaults_view)
            self.defaults_view.verticalScrollBar().setValue(0)
            self.defaults.back.setFocus()
            return
        self.heading.setText(SECTIONS[key][0])
        self.stack.setCurrentWidget(self.detail)
        self.detail.verticalScrollBar().setValue(0)
        self.back.setFocus()
        self._clear()
        self.message.clear()
        {
            "games": self._games,
            "updates": self._updates,
            "storage": self._storage,
            "shortcuts": self._shortcuts,
            "about": lambda: add_about(self.ctx, self._panel),
        }[key]()

    def _clear(self) -> None:
        self.usage_layout = None
        while self.inner.count():
            widget = self.inner.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _panel(self, title: str, target: QBoxLayout | None = None) -> Panel:
        panel = Panel(t.LG)
        panel.body.addWidget(label(title, "subheading", True))
        panel.body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        if target is None:
            self.inner.addWidget(panel)
        else:
            target.addWidget(panel, 1)
        return panel

    def _work(
        self, work: Callable[[], Any], success: Callable[[Any], None], *, writing: bool = False
    ) -> None:
        if writing:
            self.ctx.set_busy(True)
        job = self.ctx.tasks.start(lambda _: work())

        self.pending[job] = (self.revision, writing, success)
        job.result.connect(self._finished)
        job.error.connect(self._failed)

    @Slot(object)
    def _finished(self, result: Any) -> None:
        self._finish_work(result, error=False)

    @Slot(object)
    def _failed(self, exc: Exception) -> None:
        self._finish_work(exc, error=True)

    def _finish_work(self, result: Any, *, error: bool) -> None:
        revision, writing, success = self.pending.pop(self.sender())
        if writing:
            self.ctx.set_busy(False)
        if revision == self.revision:
            if error:
                self.message.setText(f"Could not finish. {result}")
                if self.section == "home":
                    self.ctx.notify(self.message.text())
                elif self.stack.currentWidget() is self.detail:
                    QTimer.singleShot(
                        0, self.detail, lambda: self.detail.ensureWidgetVisible(self.message)
                    )
            else:
                success(result)

    def _games(self) -> None:
        current = self.ctx.installation
        panel = self._panel("Saved EDOPro folders")
        known = self.ctx.known_installations()
        if not known:
            panel.body.addWidget(label("Choose your EDOPro folder to get started.", "body", True))
        for path in known:
            selected = bool(current.path) and Path(path) == Path(current.path)
            panel.body.addWidget(line())
            name = ElidedLabel()
            name.setProperty("role", "subheading")
            name.set_value(path, Path(path).name)
            panel.body.addWidget(name)
            location = ElidedLabel()
            location.set_value(path)
            panel.body.addWidget(location)
            if selected:
                panel.body.addWidget(
                    label(
                        "Selected folder"
                        if current.ready
                        else "Selected · Open EDOPro once to get its card data.",
                        "goldKicker",
                        True,
                    )
                )
            row = actions()
            if not selected:
                row.insertWidget(
                    0,
                    button(
                        "Use this folder",
                        lambda path=path: self.ctx.select_installation_requested.emit(path),
                        "check",
                    ),
                )
            row.insertWidget(
                row.count() - 1,
                button(
                    "Open folder",
                    lambda path=path: open_url(QUrl.fromLocalFile(path), self.ctx),
                    "folder",
                ),
            )
            row.insertWidget(
                row.count() - 1,
                button("Forget", lambda path=path: self._forget(path), role="quiet"),
            )
            panel.body.addLayout(row)
        self.find_games = button(
            "Find EDOPro folders", self.ctx.installations_requested.emit, "search"
        )
        panel.body.addWidget(self.find_games, 0, Qt.AlignmentFlag.AlignLeft)

    def _forget(self, path: str) -> None:
        if not self.ctx.confirm(
            "Forget this game folder?",
            f"Remove {path} from your saved folders? Your game files stay on disk. You can add it again.",
        ):
            return
        selected = bool(self.ctx.installation.path) and Path(self.ctx.installation.path) == Path(
            path
        )
        settings = self.ctx.cfg.config_path

        def forget() -> None:
            if selected and not config.save_setting(settings, "edopro_path", None):
                raise OSError("Could not save the change to your settings.")

        def forgotten(_: None) -> None:
            self.ctx.state.setValue(
                "games/known", [p for p in self.ctx.known_installations() if p != path]
            )
            if selected:
                self.ctx.state.setValue("games/chooseOnStart", True)
                self.ctx.set_installation(Installation())
            else:
                self.open_section("games")

        self._work(forget, forgotten, writing=True)

    def _storage(self) -> None:
        panel = self._panel("App data folder")
        root = str(Path(self.ctx.cfg.config_path).parent)
        path = ElidedLabel()
        path.set_value(root)
        panel.body.addWidget(path)
        panel.body.addLayout(
            actions(
                button(
                    "Open folder", lambda: open_url(QUrl.fromLocalFile(root), self.ctx), "folder"
                ),
                button("Refresh storage", lambda: self.open_section("storage")),
            )
        )
        self.message.setText("Measuring local files…")
        self._work(lambda: inspect_storage(self.ctx.cfg, self.cache), self._show_storage)

    def _show_storage(self, storage: Storage) -> None:
        self.message.clear()
        group = QWidget()
        direction = (
            QBoxLayout.Direction.LeftToRight
            if self.width() >= t.CARDS_COLUMNS
            else QBoxLayout.Direction.TopToBottom
        )
        self.usage_layout = QBoxLayout(direction, group)
        margins(self.usage_layout, 0)
        self.usage_layout.setSpacing(t.MD)
        self.inner.addWidget(group)
        panel = self._panel(
            f"Space used   ·   {byte_size(sum(storage.parts.values()))}", self.usage_layout
        )
        for name, size in storage.parts.items():
            row = QHBoxLayout()
            row.addWidget(label(name), 1)
            row.addWidget(label(byte_size(size), "small"))
            panel.body.addLayout(row)
        packs = self._panel("Downloaded packs", self.usage_layout)
        self.remove_buttons = {}
        for pack in storage.packs:
            row = QHBoxLayout()
            row.addWidget(label(pack.title, "body", True), 1)
            row.addWidget(label(byte_size(pack.size), "muted"))
            remove = button(
                "Remove", lambda pack=pack: self._remove(pack.key, pack.title), role="quiet"
            )
            self.remove_buttons[pack.key] = remove
            row.addWidget(remove)
            packs.body.addLayout(row)
        if not storage.packs:
            packs.body.addWidget(label("Your downloaded packs will appear here.", "muted", True))
        packs.body.addStretch()
        self.clear_packs = button(
            "Clear pack downloads", lambda: self._remove("", "all pack downloads")
        )
        self.clear_packs.setEnabled(bool(storage.packs or storage.has_catalogue))
        packs.body.addWidget(self.clear_packs, 0, Qt.AlignmentFlag.AlignLeft)

    def _remove(self, key: str, title: str) -> None:
        if not self.ctx.confirm(
            "Remove downloaded files?",
            f"Remove {title} from app storage? Files already applied to EDOPro stay in place.",
        ):
            return
        self.message.setText("Removing downloaded files…")
        self._work(
            lambda: self.cache.remove_pack(key) if key else self.cache.clear(),
            lambda _: self.open_section("storage"),
            writing=True,
        )

    def _update_news(self) -> None:
        latest = self.ctx.latest
        self.tiles["updates"].hint.setText(
            f"Version {latest} is available" if latest else SECTIONS["updates"][1]
        )
        if self.section == "updates":
            # Keep the keyboard on the check action as the request completes.
            self.news.setText(self.ctx.update_status)
            self.news.setVisible(bool(self.ctx.update_status))
            self.check.setEnabled(not self.ctx.checking_update)
            self.get_release.setVisible(bool(latest))
            self.offer.setText(
                f"Download version {latest} from GitHub Releases and replace your portable app."
                if latest
                else ""
            )
            self.offer.setVisible(bool(latest))

    def _updates(self) -> None:
        panel = self._panel(f"Installed version   ·   {self.ctx.version}")
        self.news = label(self.ctx.update_status, "body", True)
        self.offer = label("", "body", True)
        panel.body.addWidget(self.news)
        panel.body.addWidget(self.offer)
        self.check = button("Check for updates", self.ctx.update_requested.emit, "search")
        self.get_release = button(
            "View releases",
            lambda: open_url(QUrl(PROJECT + "/releases/latest"), self.ctx),
            "link",
            "primary",
        )
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.check)
        row.addWidget(self.get_release)
        panel.body.addLayout(row)
        self._update_news()

    def _shortcuts(self) -> None:
        locations = shortcut_locations()
        if not locations:
            panel = self._panel("Launch from your app")
            text = (
                "Open the app in Finder or drag it to your Dock."
                if sys.platform == "darwin"
                else "Shortcuts are available in the portable release. This copy is running from source."
            )
            panel.body.addWidget(label(text, "body", True))
            return
        exe = Path(sys.executable)
        previous = {
            link.title: self.ctx.state.value("shortcuts/" + link.title, "", type=str)
            for link in locations
        }

        def display(items: list[tuple[Shortcut, str]]) -> None:
            self.message.clear()
            for link, status in items:
                panel = self._panel(link.title)
                panel.body.addWidget(
                    label(
                        {
                            "absent": "Create a shortcut to this copy of the app.",
                            "installed": "Your shortcut opens this copy of the app.",
                            "moved": "Your shortcut points to an earlier location.",
                            "occupied": "Another file already uses this shortcut name.",
                        }[status],
                        "body",
                        True,
                    )
                )

                def change(remove: bool, link: Shortcut = link) -> None:
                    def finished(_: None) -> None:
                        self.ctx.state.setValue(
                            "shortcuts/" + link.title, "" if remove else str(exe)
                        )
                        self.open_section("shortcuts")

                    self._work(
                        lambda: change_shortcut(link, exe, previous[link.title], remove=remove),
                        finished,
                        writing=True,
                    )

                row = actions()
                if status in ("absent", "moved"):
                    row.insertWidget(
                        0,
                        button(
                            "Create shortcut" if status == "absent" else "Update shortcut",
                            lambda change=change: change(False),
                        ),
                    )
                if status in ("installed", "moved"):
                    row.insertWidget(
                        0, button("Remove shortcut", lambda change=change: change(True))
                    )
                panel.body.addLayout(row)

        self.message.setText("Checking shortcuts…")
        self._work(
            lambda: [inspect_shortcut(link, exe, previous[link.title]) for link in locations],
            display,
        )

    def _classic(self) -> None:
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve().parents[2] / "main.py"))
        command.extend(("--legacy-gui", "--config", self.ctx.cfg.config_path))
        if self.ctx.installation.path:
            command.extend(("--edopro-path", self.ctx.installation.path))
        # The one-file child needs its own unpacked files after the parent closes.
        environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        self._work(
            lambda: subprocess.Popen(command, env=environment),
            lambda _: self.window().close(),
            writing=True,
        )
