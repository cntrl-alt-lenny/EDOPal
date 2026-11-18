"""The self-contained Game display section of Settings."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import config
import gamesettings as gs
from gamesettings.document import Change, current_profile, preview
from gamesettings.options import OPTIONS, PROFILES, Profile, describe, font_parts, renderer_warning
from gamesettings.storage import available_profiles, profiles, save_profile
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.card_views import line, scroll
from studio.widgets import Button, ElidedLabel, GameClosedBlock, Hero, Panel, label, margins

if TYPE_CHECKING:
    from studio.pages.settings import SettingsPage

DISPLAY_SECTION = (
    "Game display",
    "Adjust text size, fullscreen and graphics for this device.",
    "settings",
)
ACTIONS = {"apply": "Apply changes", "undo": "Undo last change", "restore": "Restore original"}


def open_display(page: SettingsPage) -> None:
    view = page.stack.findChild(DisplayView)
    if view is None:
        view = DisplayView(page.ctx, page.home_view)
        page.stack.addWidget(view)
    page.stack.setCurrentWidget(view)
    view.back.setFocus()


def _button(title: str, action: Callable, role: str = "secondary", icon: str = "") -> Button:
    button = Button(title, role, icon)
    button.clicked.connect(action)
    return button


class ChoiceBox(QComboBox):
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.showPopup()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(t.GOLD if self.hasFocus() else t.TEXT), t.FOCUS))
        x, y = self.width() - t.LG, self.height() // 2
        painter.drawLine(x - t.XS, y - 2, x, y + 2)
        painter.drawLine(x, y + 2, x + t.XS, y - 2)


class DisplayView(QWidget):
    def __init__(self, ctx: AppContext, leave: Callable[[], None]) -> None:
        super().__init__()
        self.setMaximumWidth(t.CONTENT_MAX)
        self.ctx, self.leave = ctx, leave
        self.config_dir = Path(ctx.cfg.config_path).parent
        self.snapshot: gs.Snapshot | None = None
        self.saved: tuple[Profile, ...] = ()
        self.draft: dict[str, str] = {}
        self.selected = self.group = self.generation = 0
        self.mode = "load"
        self.action = "apply"
        self.status = ""
        self.writing = False
        self.pending: tuple[Change, ...] = ()
        self.controls: dict[str, QWidget] = {}
        self.body = QVBoxLayout(self)
        margins(self.body, t.LG)
        self.body.setSpacing(t.SM)
        self.back = _button("Settings", self.escape, "quiet", "back")
        self.body.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        hero = Hero(
            "Preferences", "Game display", "Tune EDOPro for the screen you play on.", "settings"
        )
        hero.setFixedHeight(t.TILE_H + t.HUGE)
        self.body.addWidget(hero)
        self.area = scroll(QWidget())
        self.body.addWidget(self.area, 1)
        self.block = GameClosedBlock()
        self.block.retry.clicked.connect(self._commit)
        self.body.addWidget(self.block)
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        margins(footer, 0)
        self.note = label("", "muted", True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        footer.addWidget(self.note, 1)
        self.secondary = _button("Save profile", self._secondary)
        self.primary = _button("Review changes", self._primary, "primary")
        footer.addWidget(self.secondary)
        footer.addWidget(self.primary)
        self.body.addWidget(self.footer)
        self.shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.shortcut.activated.connect(self.escape)
        ctx.installation_changed.connect(self.installation_changed)
        self.setStyleSheet(f"""
            QComboBox, QSpinBox {{ background: {t.RAISED}; color: {t.INK}; border: {t.HAIR}px solid {t.EDGE};
                border-radius: {t.R_SMALL}px; padding: {t.XS}px {t.SM}px; }}
            QComboBox:focus, QSpinBox:focus {{ border: {t.FOCUS}px solid {t.GOLD}; }}
            QComboBox QAbstractItemView {{ background: {t.RAISED}; color: {t.INK}; selection-background-color: {t.ACTIVE}; }}
            QPushButton[role="scope"] {{ padding: {t.SM}px {t.LG}px; text-align: center; }}
            QComboBox::drop-down {{ border: none; width: {t.XL}px; }}
        """)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.installation_changed(self.ctx.installation)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.mode == "edit" and (event.oldSize().width() >= t.WIDE_CONTENT) != (
            self.width() >= t.WIDE_CONTENT
        ):
            self._render()

    def hidden(self) -> bool:
        return not self.writing

    def escape(self) -> bool:
        if self.writing:
            return True
        if self.mode in ("review", "save", "blocked", "done", "failed"):
            self.mode, self.status = "edit", ""
            self._render()
        else:
            self.leave()
        return True

    def installation_changed(self, installation: Installation) -> None:
        self.generation += 1
        self.snapshot = None
        self.selected = self.group = 0
        self.draft = {}
        self.status = ""
        if not self.isVisible():
            return
        if not installation.ready:
            self.mode = "unready" if installation.path else "empty"
            self._render()
            return
        self.engine = gs.DisplaySettings(Path(installation.path), self.config_dir)
        engine, directory = self.engine, self.config_dir
        self.mode = "load"
        self._render()
        self._work(lambda: (engine.inspect(), *available_profiles(directory)), self._loaded)

    def _work(self, fn: Callable, success: Callable, *, writing: bool = False) -> None:
        generation = self.generation
        if writing:
            self.writing = True
            self.ctx.set_busy(True)
        job = self.ctx.tasks.start(lambda _: fn(), owner=self)

        def result(value: Any) -> None:
            if generation == self.generation:
                success(value)

        def error(exc: Exception) -> None:
            if generation != self.generation:
                return
            if isinstance(exc, gs.GameOpenError):
                self.mode = "blocked"
            elif self.mode == "saving":
                self.mode = "save"
            else:
                self.mode = "failed" if self.snapshot else "error"
            self.status = str(exc)
            self._render()

        def finished() -> None:
            if writing:
                self.writing = False
                self.ctx.set_busy(False)
            if self.isVisible() and generation == self.generation:
                if self.mode == "edit":
                    self.profile.setFocus()
                else:
                    self.back.setFocus()
                if self.mode == "blocked":
                    self.block.retry.setFocus()

        job.result.connect(result)
        job.error.connect(error)
        job.finished.connect(finished)

    def _loaded(self, data: tuple[gs.Snapshot, tuple[Profile, ...], str]) -> None:
        self.snapshot, self.saved, self.status = data
        self.mode = "edit"
        self._render()

    def _render(self) -> None:
        old = self.area.takeWidget()
        if old:
            old.hide()
            old.deleteLater()
        content = QWidget()
        self.layout_body = QVBoxLayout(content)
        margins(self.layout_body, 0)
        self.layout_body.setSpacing(t.MD)
        self.controls.clear()
        self.back.setText(
            "Settings"
            if self.mode in ("edit", "load", "empty", "unready", "error")
            else "Game display"
        )
        self.back.setEnabled(self.mode not in ("writing", "saving"))
        self.secondary.hide()
        self.primary.hide()
        self.block.hide()
        self.note.setText("")
        self.footer.setVisible(True)
        if self.mode == "edit":
            self._editor()
        elif self.mode in ("review", "writing", "blocked", "done", "failed"):
            self._review()
        elif self.mode in ("save", "saving"):
            self._save_view()
        else:
            titles = {
                "empty": "Choose a game folder",
                "unready": "Waiting for the game to update",
                "load": "Reading display settings…",
                "error": "Couldn’t read display settings",
            }
            hints = {
                "empty": "Select the EDOPro installation you want to adjust.",
                "unready": "Open EDOPro, let the card databases finish updating, then return here.",
                "load": "",
                "error": self.status,
            }
            panel = self._panel(titles[self.mode], hints[self.mode])
            if self.mode != "load":
                choose = self.mode == "empty"
                button = _button(
                    "Choose folder" if choose else "Retry",
                    self.ctx.installations_requested.emit if choose else self._reload,
                    "primary",
                )
                panel.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            self.footer.hide()
        self.layout_body.addStretch()
        self.area.setWidget(content)
        self.area.verticalScrollBar().setValue(0)

    def _reload(self) -> None:
        if self.mode == "unready":
            path = self.ctx.installation.path
            self.mode = "load"
            self._render()
            self._work(lambda: Installation.inspect(path), self.ctx.set_installation)
        else:
            self.installation_changed(self.ctx.installation)

    def _panel(self, title: str, hint: str = "") -> Panel:
        panel = Panel(t.LG)
        panel.body.addWidget(label(title, "heading", True))
        if hint:
            desc = label(hint, "body", True)
            desc.setTextFormat(Qt.TextFormat.PlainText)
            panel.body.addWidget(desc)
        self.layout_body.addWidget(panel)
        return panel

    def _combo(self, title: str, items: tuple[tuple[str, str], ...], current: str) -> QComboBox:
        box = ChoiceBox()
        box.setAccessibleName(title)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for value, name in items:
            box.addItem(name, value)
            if title == "Text font":
                box.setItemData(box.count() - 1, value, Qt.ItemDataRole.ToolTipRole)
        index = box.findData(current)
        if index < 0:
            box.addItem(current, current)
            index = box.count() - 1
        box.setCurrentIndex(index)
        box.setMinimumHeight(t.BUTTON_H)
        return box

    def _editor(self) -> None:
        choices = (
            Profile("Current settings", "Start with the values in this game folder.", {}),
            *PROFILES,
            *self.saved,
        )
        profile = choices[self.selected]
        panel = Panel(t.LG)
        self.layout_body.addWidget(panel)
        heading = QHBoxLayout()
        heading.addWidget(label("Device profile", "heading"), 2)
        self.profile = self._combo(
            "Device profile", tuple((p.name, p.name) for p in choices), profile.name
        )
        heading.addWidget(self.profile, 3)
        panel.body.addLayout(heading)
        panel.body.addWidget(label(profile.hint, "muted", True))

        def select(idx: int) -> None:
            self.selected, self.draft, self.status = idx, choices[idx].values.copy(), ""
            self._render()
            self.profile.setFocus()

        self.profile.currentIndexChanged.connect(select)
        titles = ("Text and screen", "Motion and graphics")
        wide = self.width() >= t.WIDE_CONTENT
        if not wide:
            tabs = QHBoxLayout()
            for index, title in enumerate(titles):
                tab = _button(title, lambda _, i=index: self._tab(i))
                tab.setCheckable(True)
                tab.setChecked(index == self.group)
                tab.setProperty("role", "scope")
                tabs.addWidget(tab)
            self.layout_body.addLayout(tabs)
        groups = QHBoxLayout()
        self.layout_body.addLayout(groups)
        for index in range(2) if wide else (self.group,):
            panel = Panel(t.LG)
            groups.addWidget(panel, 1, Qt.AlignmentFlag.AlignTop)
            if wide:
                panel.body.addWidget(label(titles[index], "heading"))
            keys = list(OPTIONS)
            for i, key in enumerate(keys[:3] if index == 0 else keys[3:]):
                if i:
                    panel.body.addWidget(line())
                self._field(panel, key)
            if not index:
                panel.body.addWidget(
                    label(
                        self.snapshot.window + " · Drag the window edge in game to resize it.",
                        "muted",
                        True,
                    )
                )
        if self.snapshot.history.last or self.snapshot.history.original:
            self._recovery_buttons()
        self.primary.setText("Review changes")
        self.secondary.setText("Save profile")
        self.primary.show()
        self.secondary.show()
        self._count()

    def _tab(self, index: int) -> None:
        self.group = index
        self._render()
        next(iter(self.controls.values())).setFocus()

    def _field(self, panel: Panel, key: str) -> None:
        option = OPTIONS[key]
        value = self.draft.get(key, self.snapshot.values[key] or option.default)
        row = QHBoxLayout()
        desc = QVBoxLayout()
        desc.setSpacing(t.XS)
        desc.addWidget(label(option.title, "subheading"))
        hint = option.hint
        hint += " Current: " + describe(key, self.snapshot.values[key]) + "."
        desc.addWidget(label(hint, "muted", True))
        row.addLayout(desc, 3)
        panel.body.addLayout(row)
        if key == "textfont":
            try:
                font, size = font_parts(value)
            except ValueError:
                font, size = value, "12"
            box = self._combo(
                "Text font", tuple((p, Path(p).stem) for p in self.snapshot.fonts), font
            )
            size_box = self._number("Text size", size, 9, 24, " px")

            def font_edit() -> None:
                self._edit(key, f"{box.currentData()} {size_box.value()}")

            box.currentIndexChanged.connect(font_edit)
            size_box.valueChanged.connect(font_edit)
            row.addWidget(box, 2)
            row.addWidget(size_box)
            self.controls["size"] = size_box
        elif key in ("maxFPS", "dpi_scale"):
            scale = key == "dpi_scale"
            try:
                number = str(round(float(value) * (100 if scale else 1)))
            except (ValueError, OverflowError):
                number = "0"
            box = self._number(
                option.title,
                number,
                100 if scale else 30,
                200 if scale else 240,
                "%" if scale else " fps",
            )
            box.valueChanged.connect(lambda n: self._edit(key, str(n / 100 if scale else n)))
            row.addWidget(box, 2)
        else:
            box = self._combo(option.title, option.choices, value)
            box.currentIndexChanged.connect(lambda: self._edit(key, box.currentData()))
            row.addWidget(box, 2)
        self.controls[key] = box
        box.setToolTip(
            value if key == "textfont" else "Current: " + describe(key, self.snapshot.values[key])
        )

    @staticmethod
    def _number(title: str, value: str, low: int, high: int, suffix: str) -> QSpinBox:
        box = QSpinBox()
        box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        box.setAccessibleName(title)
        box.setRange(low, high)
        box.setValue(max(low, min(high, int(value))))
        box.setSuffix(suffix)
        box.setMinimumHeight(t.BUTTON_H)
        return box

    def _edit(self, key: str, value: str) -> None:
        self.draft[key] = value
        self.status = ""
        self._count()

    def _count(self) -> None:
        try:
            changes = preview(self.snapshot.values, self.draft)
        except ValueError as exc:
            self.primary.setEnabled(False)
            self.secondary.setEnabled(False)
            self.note.setText(str(exc))
            return
        self.primary.setEnabled(bool(changes))
        saved_index = self.selected - len(PROFILES) - 1
        saved = saved_index >= 0 and self.saved[saved_index].values == self.draft
        self.secondary.setText("Saved" if saved else "Save profile")
        self.secondary.setEnabled(
            not saved and bool(self.draft or current_profile(self.snapshot.values))
        )
        n = len(changes)
        self.note.setText(
            self.status
            or (
                f"{n} setting{'s' if n != 1 else ''} to review"
                if n
                else "Choose a profile or adjust a value."
            )
        )

    def _recovery_buttons(self) -> None:
        panel = self._panel("Recovery")
        if self.snapshot.history.last and self.mode != "done":
            panel.body.addWidget(_button("Undo last change", lambda: self._prepare("undo")))
            panel.body.addWidget(
                label("Put back the values from just before the last change.", "muted", True)
            )
        if self.snapshot.history.original:
            panel.body.addWidget(_button("Restore original", lambda: self._prepare("restore")))
            panel.body.addWidget(
                label(
                    f"Put each setting back to its value before {config.APP_NAME} first changed it.",
                    "muted",
                    True,
                )
            )

    def _prepare(self, action: str) -> None:
        self.action = action
        try:
            self.pending = (
                preview(self.snapshot.values, self.draft)
                if action == "apply"
                else gs.recovery(self.snapshot, action)
            )
        except ValueError as exc:
            self.pending, self.mode, self.status = (), "failed", str(exc)
        else:
            self.mode, self.status = "review", ""
        self._render()
        self.primary.setFocus()

    def _review(self) -> None:
        done = self.mode == "done"
        title = {
            "done": {
                "apply": "Display settings applied",
                "undo": "Last change undone",
                "restore": "Original values restored",
            }[self.action],
            "failed": "Couldn’t change display settings",
            "writing": "Saving display settings…",
        }.get(self.mode, "Review display changes")
        panel = self._panel(
            title,
            self.status
            if self.mode == "failed"
            else "These values take effect next time you start EDOPro.",
        )
        table = QGridLayout()
        table.setColumnStretch(0, 4)
        table.setColumnStretch(1, 3)
        table.setColumnStretch(2, 3)
        for col, heading in enumerate(("SETTING", "BEFORE", "AFTER")):
            table.addWidget(label(heading, "goldKicker"), 0, col)
        for i, c in enumerate(self.pending, 1):
            table.addWidget(label(OPTIONS[c.key].title, "body", True), i, 0)
            for col, val in enumerate((c.before, c.after), 1):
                cell = ElidedLabel()
                cell.set_value(
                    f"{c.key} = {val}" if val is not None else "No saved value",
                    describe(c.key, val),
                )
                table.addWidget(cell, i, col)
        panel.body.addLayout(table)
        if not self.pending and self.mode == "review":
            panel.body.addWidget(label("These settings already match.", "muted"))
        for change in self.pending:
            if (
                change.key == "driver_type"
                and change.after
                and (warning := renderer_warning(change.after))
            ):
                self.note.setText(warning)
        if self.pending and self.action != "undo" and self.mode == "review":
            panel.body.addWidget(line())
            panel.body.addWidget(
                label(
                    f"If EDOPro won’t start, open {config.APP_NAME} → Settings → Game display and choose Undo last change.",
                    "muted",
                    True,
                )
            )
        if done:
            self._recovery_buttons()
            self.note.setText("")
            if self.snapshot.history.last:
                self.note.setText("Undo puts back the values in the Before column.")
                self.secondary.setText("Undo last change")
                self.secondary.setEnabled(True)
                self.secondary.show()
        self.primary.setText(
            "Applied"
            if done and self.action == "apply"
            else "Done"
            if done
            else ACTIONS[self.action]
        )
        self.primary.setVisible(self.mode in ("review", "done"))
        self.primary.setEnabled(self.mode == "review" and bool(self.pending))
        if self.mode == "writing":
            self.note.setText("Saving… Keep this game folder selected.")
        if self.mode == "blocked":
            self.footer.hide()
            self.block.show_reason(self.status)
        if self.mode == "failed":
            self.primary.setText("Reload settings")
            self.primary.setEnabled(True)
            self.primary.show()

    def _commit(self) -> None:
        engine, snapshot, draft, mode = self.engine, self.snapshot, self.draft.copy(), self.action

        def work() -> gs.Snapshot:
            engine.commit(snapshot, draft, mode)
            return engine.inspect()

        def success(snapshot: gs.Snapshot) -> None:
            self.snapshot, self.mode, self.status = snapshot, "done", ""
            self.selected, self.draft = 0, {}
            self._render()

        self.mode = "writing"
        self._render()
        self._work(work, success, writing=True)

    def _save_view(self) -> None:
        panel = self._panel(
            "Save a device profile",
            "Save your chosen values for another game folder on this device.",
        )
        self.name = QLineEdit(getattr(self, "profile_name", ""))
        self.name.setPlaceholderText("Profile name")
        self.name.setAccessibleName("Profile name")
        self.name.setMaxLength(48)
        self.name.setMinimumHeight(t.BUTTON_H)
        panel.body.addWidget(self.name)
        panel.body.addWidget(
            label(", ".join(OPTIONS[k].title for k in self.to_save), "muted", True)
        )
        self.note.setText(self.status)
        self.primary.setText("Save profile")
        self.primary.show()
        self.primary.setEnabled(bool(self.name.text().strip()) and self.mode == "save")
        self.name.textChanged.connect(lambda s: self.primary.setEnabled(bool(s.strip())))
        self.name.returnPressed.connect(self.primary.click)

    def _secondary(self) -> None:
        if self.mode == "done":
            self._prepare("undo")
            return
        self.to_save = self.draft.copy() or current_profile(self.snapshot.values)
        self.mode, self.status, self.profile_name = "save", "", ""
        self._render()
        self.name.setFocus()

    def _primary(self) -> None:
        if self.mode == "edit":
            self._prepare("apply")
        elif self.mode == "review":
            self._commit()
        elif self.mode == "failed":
            self._reload()
        elif self.mode == "save":
            self.profile_name = name = self.name.text().strip()
            directory, draft = self.config_dir, self.to_save

            def work() -> tuple[Profile, ...]:
                save_profile(directory, name, draft)
                return profiles(directory)

            def success(saved: tuple[Profile, ...]) -> None:
                index = next(i for i, p in enumerate(saved) if p.name == name)
                self.saved, self.draft = saved, saved[index].values.copy()
                self.selected = 1 + len(PROFILES) + index
                self.mode, self.status = "edit", "Profile saved on this device."
                self._render()

            self.mode = "saving"
            self._render()
            self.name.setDisabled(True)
            self._work(work, success, writing=True)
