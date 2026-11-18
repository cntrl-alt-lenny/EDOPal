"""The inline artwork views. CardsPage owns transitions and worker state."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from artwork_flow import CHECKBOXES
from config import Config
from studio import theme as t
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
    content.setMaximumWidth(t.CONTENT_MAX)
    area.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
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
        self.body.addStretch(0)
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

        sync = QVBoxLayout()
        sync.setSpacing(t.LG)
        sync.addWidget(SectionHeader("Artwork to sync"))
        self.scope = SegmentedControl(
            (
                ("Full library", "Every card in your installed databases", "library"),
                ("My decks", "Only cards used in your saved decks", "deck"),
            )
        )
        sync.addWidget(self.scope)
        self.connect_game = ActionTile(
            "Choose your game folder", "Find your installation or browse to ProjectIgnis.", "folder"
        )
        sync.addWidget(self.connect_game)
        actions = QHBoxLayout()
        actions.setSpacing(t.MD)
        self.status = label("", "small", True)
        actions.addWidget(self.status, 1)
        self.start = Button("Sync HD cards", "primary", "download")
        actions.addWidget(self.start)
        sync.addLayout(actions)
        sync.addStretch()
        self.groups.addLayout(sync, 3)

        library = QVBoxLayout()
        library.setSpacing(t.LG)
        self.rule = line()
        library.addWidget(self.rule)
        library.addWidget(SectionHeader("Library tools"))
        self.tools = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.tools.setSpacing(t.MD)
        self.settings = ActionTile("Sync settings", "Artwork, reports and maintenance", "settings")
        self.coverage = ActionTile("Show coverage", "See what’s already on disk", "coverage")
        self.tools.addWidget(self.settings, 1)
        self.tools.addWidget(self.coverage, 1)
        library.addLayout(self.tools)
        library.addStretch()
        self.groups.addLayout(library, 2)
        self.body.addStretch(1)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        width = self.width()
        wide = width >= t.WIDE_CONTENT
        self.body.setStretch(0, int(width >= t.ROOMY_WIDTH and self.height() >= t.TALL_HEIGHT))
        self.groups.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )
        self.tools.setDirection(
            QBoxLayout.Direction.TopToBottom if wide else QBoxLayout.Direction.LeftToRight
        )
        self.rule.setVisible(not wide)
        pad = t.LG if width < t.COMPACT_WIDTH * 0.75 else t.XXL
        margins(self.body, pad)
        self.hero.setFixedHeight(max(t.HERO_MIN, min(t.HERO_MAX, round(width * 0.255))))
        tile_height = max(t.TILE_H, min(t.TILE_LARGE, width // 13)) if wide else t.TILE_H
        for button in self.scope.buttons:
            button.setMinimumHeight(tile_height)


class SettingsView(QWidget):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        body = page_layout(self)
        self.back = Button("HD Cards", "quiet", "back")
        body.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(
            SectionHeader("Sync settings", "Choose what to include in the next artwork sync.")
        )
        panel = Panel(t.SM)
        panel.body.setSpacing(t.XS)
        self.toggles: dict[str, ToggleSwitch] = {}
        for key, title, hint, default in CHECKBOXES:
            if key in ("audio", "my_decks"):
                continue
            checked = not cfg.original_text if key == "hd_era_art" else getattr(cfg, key, default)
            switch = ToggleSwitch(title, hint, checked)
            if self.toggles:
                panel.body.addWidget(line())
            panel.body.addWidget(switch)
            self.toggles[key] = switch
        body.addWidget(panel)
        actions = QHBoxLayout()
        self.coverage = Button("Show coverage", "secondary", "coverage")
        self.done = Button("Done", "primary", "check")
        actions.addWidget(self.coverage)
        actions.addStretch()
        actions.addWidget(self.done)
        body.addLayout(actions)
        body.addStretch()
