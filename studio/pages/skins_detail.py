"""The preview workspace, with a scrolling inspector beside the game scene."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import extras
import themes
from studio import theme as t
from studio.context import AppContext
from studio.widgets import Button, ElidedLabel, Panel, ProgressBlock, ToggleSwitch, label, margins

from .skins_editor import Maker
from .skins_model import Look
from .skins_preview import Preview
from .skins_service import SkinStore


class Inspector(Panel):
    def __init__(self) -> None:
        super().__init__(t.LG)
        self.kind = label("", "goldKicker")
        self.description = label("", "body", True)
        self.sample = label("")
        self.sample.setScaledContents(True)
        self.sample.setFixedHeight(t.TILE_H)
        self.sample.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.facts = QWidget()
        table = QGridLayout(self.facts)
        table.setContentsMargins(0, 0, 0, 0)
        table.setVerticalSpacing(t.SM)
        self.values = []
        for i, name in enumerate(("Font", "Corner roundness", "Depth", "Turn highlight")):
            value = label("", "body")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            table.addWidget(label(name, "muted"), i, 0)
            table.addWidget(value, i, 1)
            self.values.append(value)
        self.source = ElidedLabel()
        self.hint = label("", "muted", True)
        self.highlight = ToggleSwitch(
            "Highlight the turn player", "Emphasize the active life bar.", False
        )
        self.apply = Button("Apply theme", "primary", "check")
        self.edit = Button("Customize theme", "secondary", "settings")
        for item in (
            self.kind,
            self.sample,
            self.description,
            self.facts,
            self.source,
            self.highlight,
            self.hint,
            self.edit,
        ):
            self.body.addWidget(item)
        self.history = label("Game changes", "subheading")
        self.back = Button("Restore previous skin")
        self.undo = Button("Undo last theme change")
        self.remove = Button("Remove theme from EDOPro")
        self.restore = Button("Restore appearance from before skin packs")
        for item in (self.history, self.back, self.undo, self.remove, self.restore):
            self.body.addWidget(item)
        self.manage = QWidget()
        grid = QGridLayout(self.manage)
        grid.setContentsMargins(0, t.SM, 0, 0)
        grid.addWidget(label("My themes", "subheading"), 0, 0, 1, 2)
        self.tools = {}
        for i, name in enumerate(("Rename", "Duplicate", "Export", "Delete")):
            btn = Button(name)
            grid.addWidget(btn, 1 + i // 2, i % 2)
            self.tools[name] = btn
        self.body.addWidget(self.manage)

    def display(self, look: Look, ready: bool, restore_pack: bool) -> None:
        pack = look.pack.pack if look.pack else None
        self.facts.setVisible(not pack)
        generated = not pack and not look.state.folder and look.theme.wallpaper in themes.STYLES
        self.sample.setVisible(generated)
        if generated:
            self.sample.setFixedHeight(t.HUGE if look.group == "My themes" else t.TILE_H)
            self.sample.setPixmap(QPixmap.fromImage(themes.wallpaper(look.theme, (480, 100))))
        self.kind.setText(
            "DOWNLOADABLE SKIN"
            if pack
            else "MY THEMES"
            if look.group == "My themes"
            else "INSTALLED IN THIS GAME"
            if look.group == "Installed"
            else "BUILT-IN THEME"
        )
        if pack:
            self.description.setText(
                f"{pack.description}\n\nBy {pack.author}\nLicense · {pack.license}\n\n{pack.attribution}"
            )
            self.source.set_value(pack.source_url)
            self.hint.setText(f"{look.availability} · Version {pack.version}")
        else:
            theme = look.theme
            wall = {"image": "Your own image", "inherit": "Game backgrounds"}.get(
                theme.wallpaper, theme.wallpaper.title() + " wallpaper"
            )
            font = "Inter font" if theme.font == "inter" else "Game fonts"
            self.description.setText(wall)
            for value, text in zip(
                self.values,
                (
                    font,
                    f"{theme.roundness} / 8",
                    f"{theme.depth}%",
                    "On" if theme.highlight_turn else "Off",
                ),
                strict=True,
            ):
                value.setText(text)
            self.hint.setText(
                "Applied to EDOPro."
                if look.active
                else "Save an editable theme or apply it to EDOPro."
            )
        self.source.setVisible(bool(pack))
        self.highlight.setVisible(bool(pack and look.name in extras.SKINS))
        download = bool(pack and look.availability in ("Not downloaded", "Update available"))
        self.apply.setText(
            ("Download update" if look.availability == "Update available" else "Download skin")
            if download
            else "Apply skin"
            if pack
            else "Apply theme"
        )
        self.apply.setEnabled(
            download or (ready and (not pack or look.availability == "Downloaded"))
        )
        self.manage.setVisible(look.group == "My themes")
        self.back.setVisible(look.state.active)
        self.undo.setVisible(look.state.can_undo)
        self.undo.setText(
            "Undo last theme change"
            if look.state.undo_removes_install
            else "Undo last theme change"
        )
        self.undo.setToolTip(
            "Put back the skin used before this theme was installed."
            if look.state.undo_removes_install
            else "Put back the last installed version of this theme."
        )
        self.back.setToolTip("Restore the earlier skin and fonts. Keep this theme installed.")
        self.remove.setVisible(bool(look.state.folder))
        self.restore.setVisible(bool(pack and restore_pack))
        self.history.setVisible(bool(look.state.folder or (pack and restore_pack)))
        for control in (self.back, self.undo, self.remove, self.restore):
            control.setEnabled(ready)


class Workspace(QWidget):
    def __init__(self, ctx: AppContext, store: SkinStore) -> None:
        super().__init__()
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.MD)
        top = QHBoxLayout()
        self.back = Button("Back", "quiet", "back")
        self.kicker = label("SKIN PREVIEW", "goldKicker")
        top.addWidget(self.back)
        top.addStretch()
        top.addWidget(self.kicker)
        self.title = ElidedLabel()
        self.title.setProperty("role", "display")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.insertWidget(1, self.title, 1)
        body.addLayout(top)
        split = QHBoxLayout()
        split.setSpacing(t.LG)
        self.preview = Preview(store, ctx.tasks)
        split.addWidget(self.preview, 3)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(t.TILE_SIZE[0] * 2 + t.HUGE)
        self.scroll.setMaximumWidth(t.TILE_SIZE[0] * 2 + 2 * t.HUGE)
        split.addWidget(self.scroll, 1)
        body.addLayout(split, 1)
        side = QWidget()
        stack = QVBoxLayout(side)
        margins(stack, 0)
        self.inspector = Inspector()
        self.maker_panel = Panel(t.LG)
        self.maker = Maker()
        self.maker_panel.body.addWidget(self.maker)
        self.form = Panel()
        self.form_title = label("", "subheading")
        self.form_name = QLineEdit()
        self.form_name.setMaxLength(64)
        self.form_name.setAccessibleName("New theme name")
        self.submit = Button("Save", "primary")
        self.cancel_name = Button("Cancel", "quiet")
        for item in (self.form_title, self.form_name, self.submit, self.cancel_name):
            self.form.body.addWidget(item)
        self.progress = ProgressBlock()
        self.panels = (self.inspector, self.maker_panel, self.form, self.progress)
        for item in self.panels:
            stack.addWidget(item)
        stack.addStretch()
        self.scroll.setWidget(side)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.inspector.apply)
        body.addLayout(actions)
        self.show_panel(self.inspector)

    def show_panel(self, panel: QWidget) -> None:
        for item in self.panels:
            item.setVisible(item is panel)
        self.inspector.apply.setVisible(panel is self.inspector)
        self.scroll.verticalScrollBar().setValue(0)
