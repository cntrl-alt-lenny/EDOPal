"""The inline artwork views. CardsPage owns transitions and worker state."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from artwork_flow import CHECKBOXES
from config import Config
from studio import theme as t
from studio.context import AppContext
from studio.widgets import (
    ActionTile,
    Button,
    Hero,
    Panel,
    SectionHeader,
    SegmentedControl,
    ToggleSwitch,
    label,
    margins,
)


def scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content.setMaximumWidth(min(content.maximumWidth(), t.CONTENT_MAX))
    area.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    area.setWidget(content)
    return area


def line() -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "line")
    frame.setFixedHeight(t.HAIR)
    return frame


def page_layout(widget: QWidget) -> QVBoxLayout:
    body = QVBoxLayout(widget)
    margins(body, t.XXL)
    body.setSpacing(t.LG)
    return body


class OptionsView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.body = page_layout(self)
        self.hero = Hero(
            "HD Cards",
            "Your cards,",
            "Download missing artwork for your library.",
            emphasis="in HD.",
        )
        self.body.addWidget(self.hero)
        self.body.addSpacing(t.SM)
        self.groups = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.groups.setSpacing(t.XL)
        self.body.addLayout(self.groups)

        self.selection = QWidget()
        sync = QVBoxLayout(self.selection)
        margins(sync, 0)
        sync.setSpacing(t.LG)
        sync.addWidget(SectionHeader("Artwork to sync"))
        self.scope = SegmentedControl(
            (
                ("Full library", "Every card in your installed databases", "library"),
                ("My decks", "Only cards used in your saved decks", "deck"),
            )
        )
        sync.addWidget(self.scope)
        self.connect_game = Button("Choose your EDOPro folder", "primary", "folder")
        self.check_again = Button("Check again", "primary", "search")
        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        margins(actions, 0)
        actions.setSpacing(t.MD)
        self.status = label("", "small", True)
        actions.addWidget(self.status, 1)
        self.start = Button("Sync HD cards", "primary", "download")
        actions.addWidget(self.connect_game)
        actions.addWidget(self.check_again)
        actions.addWidget(self.start)
        sync.addStretch()
        self.groups.addWidget(self.selection, 3)

        library = QVBoxLayout()
        library.setSpacing(t.LG)
        self.rule = line()
        library.addWidget(self.rule)
        library.addWidget(SectionHeader("Library tools"))
        self.tools = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.tools.setSpacing(t.MD)
        self.settings = ActionTile("Sync settings", "Artwork, reports and maintenance", "settings")
        self.coverage = ActionTile(
            "Check artwork coverage", "See what’s already on disk", "coverage"
        )
        self.tools.addWidget(self.settings, 1)
        self.tools.addWidget(self.coverage, 1)
        library.addLayout(self.tools)
        library.addStretch()
        self.groups.addLayout(library, 2)
        self.snapshot = Panel(t.LG)
        self.snapshot.body.addWidget(label("THIS INSTALLATION", "goldKicker"))
        metrics = QHBoxLayout()
        self.values, self.captions = [], []
        for caption in ("Card artwork", "Saved decks", "Artwork on disk"):
            column = QVBoxLayout()
            number = label("…", "metric")
            name = label(caption, "muted", True)
            column.addWidget(number)
            column.addWidget(name)
            metrics.addLayout(column, 1)
            self.values.append(number)
            self.captions.append(name)
        self.snapshot.body.addLayout(metrics)
        self.snapshot.body.addWidget(line())
        self.last = label("", "small", True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.last, 1)
        self.undo = Button("Undo last artwork sync", "secondary", "back")
        self.undo.setToolTip(
            "Restore files replaced by the last sync and remove the files it added."
        )
        self.undo.hide()
        bottom.addWidget(self.undo)
        self.snapshot.body.addLayout(bottom)
        self.body.addWidget(self.snapshot)
        self.body.addStretch(1)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        width = self.width()
        wide = width >= t.CARDS_COLUMNS
        self.groups.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )
        self.tools.setDirection(
            QBoxLayout.Direction.TopToBottom if wide else QBoxLayout.Direction.LeftToRight
        )
        self.rule.setVisible(not wide)
        pad = t.LG if width < t.COMPACT_WIDTH * 0.75 else t.XXL
        margins(self.body, pad)
        viewport = self.parentWidget()
        self.hero.setFixedHeight(
            t.landing_hero_height(width, viewport.height() if viewport else self.height())
        )
        tile_height = max(t.TILE_H, min(t.TILE_LARGE, width // 16)) if wide else t.TILE_H
        for button in self.scope.buttons:
            button.setMinimumHeight(tile_height)


class SettingsView(QWidget):
    def __init__(self, cfg: Config, *, global_defaults: bool = False) -> None:
        super().__init__()
        self.setMaximumWidth(t.WIDE_CONTENT)
        body = page_layout(self)
        self.back = Button("Back", "quiet", "back")
        self.back.setAccessibleName("Back to Settings" if global_defaults else "Back to HD Cards")
        body.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(
            SectionHeader(
                "HD Cards defaults" if global_defaults else "Sync settings",
                "Save your choices for future artwork syncs. You can change them at any time.",
            )
        )
        self.scope = (
            SegmentedControl(
                (
                    ("Recommended", "Choose for this game", "spark"),
                    ("Full library", "All installed cards", "library"),
                    ("My decks", "Cards in saved decks", "deck"),
                )
            )
            if global_defaults
            else None
        )
        if self.scope:
            body.addWidget(label("Default artwork selection", "subheading"))
            body.addWidget(self.scope)
        panel = Panel(t.SM)
        panel.body.setSpacing(t.XS)
        self.toggles: dict[str, ToggleSwitch] = {}
        for key, title, hint, default in CHECKBOXES:
            if key in ("audio", "my_decks"):
                continue
            if key == "force":
                title, hint = (
                    "Re-download selected artwork",
                    "Replace existing images in this selection.",
                )
            elif key == "hd_era_art":
                title = "Use HD artwork with modern text"
                hint = "Turn off to prefer original wording on GOAT and Pre-Errata cards."
            switch = ToggleSwitch(title, hint, default)
            if self.toggles:
                panel.body.addWidget(line())
            panel.body.addWidget(switch)
            self.toggles[key] = switch
        body.addWidget(panel)
        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        margins(actions, 0)
        self.coverage = Button("Check artwork coverage", "secondary", "coverage")
        self.coverage.setVisible(not global_defaults)
        self.done = Button("Save defaults", "primary", "check")
        actions.addWidget(self.coverage)
        self.unsaved = label("Unsaved changes", "small")
        actions.addStretch()
        actions.addWidget(self.unsaved)
        actions.addWidget(self.done)
        body.addStretch()

        self.load(cfg)
        for toggle in self.toggles.values():
            toggle.toggled.connect(self._edited)
        if self.scope:
            self.scope.changed.connect(self._edited)

    def _edited(self) -> None:
        self.unsaved.setVisible(self.preferences() != self.saved)

    def load(self, cfg: Config) -> None:
        values = {
            "hd_era_art": not cfg.original_text,
            "theme": cfg.studio_theme,
            "repair": cfg.studio_repair,
        }
        for key, toggle in self.toggles.items():
            toggle.setChecked(values[key] if key in values else getattr(cfg, key))
        if self.scope:
            self.scope.select(("auto", "library", "decks").index(cfg.studio_scope))
        self.saved = self.preferences()
        self._edited()

    def discard(self, ctx: AppContext) -> bool:
        draft = self.preferences()
        if any(value != getattr(ctx.cfg, key) for key, value in draft.items()):
            if not ctx.confirm(
                "Discard unsaved changes?", "Leave without saving your artwork defaults?"
            ):
                return False
            self.load(ctx.cfg)
        return True

    def preferences(self) -> dict[str, bool | str]:
        values = {key: toggle.isChecked() for key, toggle in self.toggles.items()}
        values["original_text"] = not values.pop("hd_era_art")
        values["studio_theme"] = values.pop("theme")
        values["studio_repair"] = values.pop("repair")
        if self.scope:
            values["studio_scope"] = ("auto", "library", "decks")[self.scope.group.checkedId()]
        return values
