"""Live 960 × 600 schematic scenes using the exporter's palette and nine-slices."""

from functools import lru_cache
from zipfile import ZipFile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)

from extras import PACKS

from .cards import NAMES, card
from .colors import WHITE, alpha, derive
from .model import Theme
from .skin import custom
from .wallpaper import wallpaper
from .widgets import draw_widget

SCENES = ("menu", "deck", "duel")


@lru_cache(maxsize=64)
def _font(size: int, bold: bool) -> QFont:
    font = QFont(_preview_family())
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


@lru_cache(maxsize=1)
def _preview_family() -> str:
    # Offscreen Windows has no system fonts. Read just the bundled face once.
    if not QFontDatabase.hasFamily("Inter EDO"):
        with ZipFile(PACKS / "shared.zip") as archive:
            handle = QFontDatabase.addApplicationFontFromData(
                archive.read("fonts/InterEDO-Regular.ttf")
            )
        if handle < 0:
            raise OSError("Could not load the bundled Inter preview font.")
    return "Inter EDO"


def label(
    p: QPainter,
    rect: QRectF,
    words: str,
    color: str,
    size: int = 16,
    *,
    bold: bool = False,
    centered: bool = False,
) -> None:
    p.setFont(_font(size, bold))
    p.setPen(QColor(color))
    p.drawText(
        rect,
        Qt.AlignmentFlag.AlignCenter
        if centered
        else Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        words,
    )


class Scene:
    def __init__(self, p: QPainter, theme: Theme) -> None:
        self.p = p
        self.theme = theme
        self.c = derive(theme)
        self.keys = custom(theme)

    def control(
        self,
        name: str,
        x: int,
        y: int,
        w: int,
        h: int,
        words: str = "",
        *,
        size: int = 16,
        centered: bool = True,
    ) -> None:
        rect = QRectF(x, y, w, h)
        draw_widget(self.p, self.theme, name, rect)
        if words:
            label(
                self.p,
                rect.adjusted(9, 0, -9, 0),
                words,
                self.c.muted if name.endswith("Disabled") else self.c.text,
                size,
                centered=centered,
            )

    def menu(self) -> None:
        c, p = self.c, self.p
        self.control("MenuBar", 0, 0, 960, 33)
        label(p, QRectF(18, 0, 480, 32), "Repositories      About      Version", c.text, 14)
        self.control("Window", 324, 104, 312, 392)
        label(p, QRectF(340, 104, 280, 25), "Project Ignis", c.text, 14, bold=True)
        label(p, QRectF(348, 143, 225, 44), "EDOPro", c.text, 34, bold=True)
        label(p, QRectF(548, 150, 70, 34), "41.0.2", c.muted, 14)
        for i, (name, state) in enumerate(
            (
                ("Servers", "Pressed"),
                ("LAN + AI", "Button"),
                ("Puzzles", "ButtonDisabled"),
                ("Replays", "Button"),
                ("Decks", "Button"),
                ("Exit", "Button"),
            )
        ):
            self.control(state, 347, 200 + i * 47, 266, 39, name)

    def info(self) -> None:
        c, p = self.c, self.p
        p.fillRect(12, 12, 202, 272, QColor(self.keys["CARDINFO_IMAGE_BACKGROUND"]))
        self.control("SunkenPane", 12, 12, 202, 272)
        p.drawImage(QRectF(30, 26, 166, 236), card(0))
        self.control("TabControl", 12, 296, 202, 292)
        self.control("TabButtonPressed", 17, 301, 109, 33, "Card info", size=14)
        self.control("TabButton", 127, 301, 81, 33, "Log", size=14)
        label(p, QRectF(26, 354, 178, 31), NAMES[0], c.text, 22, bold=True)
        label(p, QRectF(27, 386, 178, 27), "Celestial / Effect", c.accent, 15)
        p.fillRect(27, 433, 171, 1, QColor(c.border))
        label(p, QRectF(27, 446, 170, 26), "ATK   2400", c.text, 18, bold=True)
        label(p, QRectF(27, 474, 170, 26), "DEF   1800", c.muted, 18)
        label(p, QRectF(27, 538, 175, 25), "LIGHT     ·     Level 6", c.muted, 14)

    def deck(self) -> None:
        self.info()
        p, c, keys = self.p, self.c, self.keys
        self.control("MenuBar", 225, 12, 723, 45)
        label(p, QRectF(239, 13, 160, 42), "Deck editor", c.text, 19, bold=True)
        self.control("ComboBox", 413, 19, 247, 31, "Celestial", size=15)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.muted))
        p.drawPolygon(QPolygonF([QPointF(637, 33), QPointF(645, 33), QPointF(641, 38)]))
        self.control("Button", 676, 19, 117, 31, "Save", size=15)
        self.control("Button", 801, 19, 139, 31, "Save as", size=15)
        for title, key, y, h, count in (
            ("Main Deck", "MAIN", 68, 344, 21),
            ("Extra Deck", "EXTRA", 422, 99, 6),
            ("Side Deck", "SIDE", 531, 57, 0),
        ):
            prefix = "DECK_WINDOW_" + key
            p.fillRect(225, y, 451, h, QColor(keys[prefix + "_TOP_LEFT"]))
            p.fillRect(225, y, 451, 28, QColor(keys[prefix + "_INFO_TOP_LEFT"]))
            label(p, QRectF(237, y, 320, 27), title, WHITE, 15, bold=True)
            label(p, QRectF(633, y, 40, 27), str(count), WHITE, 14)
            for i in range(count):
                sw, sh = (51, 73) if key == "MAIN" else (39, 55)
                x, cy = 237 + (i % 7) * 62, y + 39 + (i // 7) * 98
                if i == 0 and key == "MAIN":
                    p.fillRect(
                        x - 3,
                        cy - 3,
                        sw + 6,
                        sh + 6,
                        QColor(keys["DECK_WINDOW_HOVERED_CARD_OUTLINE"]),
                    )
                p.drawImage(QRectF(x, cy, sw, sh), card((i + i // 7) % 6))
        p.fillRect(687, 68, 261, 520, QColor(keys["DECK_WINDOW_SEARCH_RESULT_TOP_LEFT"]))
        p.fillRect(687, 68, 261, 28, QColor(keys["DECK_WINDOW_SEARCH_RESULT_INFO_TOP_LEFT"]))
        label(p, QRectF(700, 68, 220, 28), "Search results", WHITE, 15, bold=True)
        for i, name in enumerate(NAMES):
            y = 101 + i * 79
            if i == 0:
                p.fillRect(690, y, 255, 75, QColor(keys["DECK_WINDOW_HOVERED_CARD_RESULT"]))
            p.drawImage(QRectF(701, y + 6, 42, 60), card(i))
            label(p, QRectF(755, y + 9, 183, 27), name, WHITE, 17, bold=True)
            label(p, QRectF(755, y + 37, 183, 24), ("LIGHT", "WATER", "DARK")[i % 3], WHITE, 12)

    def duel(self, opponent_turn: bool) -> None:
        self.info()
        c, p, keys = self.c, self.p, self.keys
        for i, (x, name, life) in enumerate(((245, "You", "8000"), (622, "Opponent", "6200"))):
            if i == int(opponent_turn):
                rect = QRectF(x - 7, 12, 312, 77)
                p.setBrush(QColor(keys["DUELFIELD_TURNPLAYER_COLOR"]))
                p.setPen(QPen(QColor(keys["DUELFIELD_TURNPLAYER_OUTLINE_COLOR"]), 2.4))
                p.drawRoundedRect(rect, 4, 4)
            self.control("MenuPane", x, 18, 298, 64)
            label(p, QRectF(x + 13, 49, 190, 27), name, c.text, 15, bold=True)
            bar = c.bars[i]
            grad = QLinearGradient(0, 23, 0, 49)
            grad.setColorAt(0, QColor(bar.top))
            grad.setColorAt(1, QColor(bar.bottom))
            p.fillRect(x + 11, 23, 276, 26, QColor(c.inset))
            p.fillRect(x + 11 if i == 0 else x + 73, 23, 276 if i == 0 else 214, 26, grad)
            label(p, QRectF(x + 15, 23, 266, 26), life, bar.text, 17, centered=True, bold=True)
            p.fillRect(x + 197, 60, 89, 7, QColor(c.border))
            p.fillRect(
                x + 198 if i == 0 else x + 217,
                61,
                70 if i == 0 else 68,
                5,
                QColor(keys[f"TIMEBAR_{i + 1}_TOP_LEFT"]),
            )

        # Project the empty zones onto a trapezoidal board. Field art is inherited.
        def pos(col: float, row: float) -> QPointF:
            return QPointF(591 + (col - 2.5) * (77 + 8 * row), 143 + row * 79)

        def zone(col: float, row: float, w: float, h: float) -> QPolygonF:
            return QPolygonF(
                [pos(col, row), pos(col + w, row), pos(col + w, row + h), pos(col, row + h)]
            )

        p.setBrush(QColor(alpha(c.inset, 218)))
        p.setPen(QPen(QColor(c.border), 1.3))
        p.drawPolygon(zone(-0.22, -0.16, 5.44, 4.32))
        for row in range(4):
            for col in range(5):
                p.setBrush(
                    QColor(
                        keys["DUELFIELD_LINKED"] if (col, row) == (2, 2) else alpha(c.raised, 105)
                    )
                )
                p.setPen(QPen(QColor(alpha(c.border, 205)), 1.3))
                p.drawPolygon(zone(col + 0.035, row + 0.045, 0.93, 0.91))
        for col, row, index in ((1, 1, 2), (3, 0, 4), (2, 2, 0), (0, 3, 1)):
            center = pos(col + 0.5, row + 0.5)
            rect = QRectF(center.x() - 21, center.y() - 29, 42, 59)
            if (col, row) == (2, 2):
                p.fillRect(
                    rect.adjusted(-3, -3, 3, 3), QColor(keys["DUELFIELD_SELECTABLE_CARD_OUTLINE"])
                )
            p.drawImage(rect, card(index))
        p.fillRect(638, 312, 191, 54, QColor(keys["DUELFIELD_TOOLTIP_TEXT_BACKGROUND_COLOR"]))
        p.fillRect(638, 312, 3, 54, QColor(c.accent))
        label(
            p,
            QRectF(650, 314, 175, 24),
            NAMES[0],
            keys["DUELFIELD_TOOLTIP_TEXT_COLOR"],
            15,
            bold=True,
        )
        label(p, QRectF(650, 338, 175, 24), "2400 ATK  /  1800 DEF", c.muted, 13)
        for i in range(5):
            p.save()
            p.translate(475 + i * 57, 548 + abs(2 - i) * 3)
            p.rotate((i - 2) * 5)
            p.drawImage(QRectF(-30, -42, 60, 85), card(i))
            p.restore()
        self.control("TabButtonPressed", 532, 98, 119, 30, "Main Phase 1", size=14)


def render_preview(
    theme: Theme,
    scene: str,
    size: tuple[int, int] = (960, 600),
    *,
    opponent_turn: bool = False,
    image: QImage | None = None,
    scale_background: bool = True,
) -> QImage:
    if scene not in SCENES:
        raise ValueError("Unknown preview scene.")
    img = wallpaper(theme, size, image=image, scale_background=scale_background)
    p = QPainter(img)
    try:
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        p.scale(size[0] / 960, size[1] / 600)
        draw = Scene(p, theme)
        if scene == "menu":
            draw.menu()
        elif scene == "deck":
            draw.deck()
        else:
            draw.duel(opponent_turn)
    finally:
        p.end()
    return img
