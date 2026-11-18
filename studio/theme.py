"""Studio's palette, type, dimensions and interaction states (logical pixels)."""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parents[1] / "assets"
CANVAS, RAIL, PANEL, RAISED = "#191a1b", "#151618", "#232426", "#2c2d2e"
EDGE, HOVER, ACTIVE = "#3e3e3d", "#343536", "#383328"
INK, TEXT, MUTED = "#f5f1e9", "#d5d1c8", "#aaa9a6"
GOLD, PALE, PRESSED, ON_GOLD = "#ebc987", "#f7dfac", "#d5af6a", "#251f15"
GREEN, RED = "#99cfb2", "#f1a499"
HERO_DARK, HERO_LIGHT, BRONZE = "#272326", "#594539", "#af885a"
FOIL = {
    "folder": ("#20262a", "#44545b"),
    "download": ("#252833", "#515774"),
    "spark": ("#242732", "#485e78"),
    "arrow": ("#242b2a", "#40574e"),
    "brand": ("#24262c", "#565363"),
    "mydecks": ("#262532", "#555979"),
    "replays": ("#222c31", "#386477"),
    "backup": ("#28282a", "#6d5949"),
    "health": ("#202927", "#416953"),
    "check": ("#202927", "#41574a"),
    "warning": ("#302327", "#674747"),
    "skins": ("#20252f", "#45526c"),
    "sleeves": ("#2c222a", "#684451"),
    "sounds": ("#1b2929", "#405d59"),
    "music": ("#27232f", "#574968"),
    "decks": ("#292421", "#6c523b"),
    "settings": ("#20262a", "#44545b"),
}
CLEAR = "transparent"
HAIR, FOCUS = 1, 2
XS, SM, MD, LG, XL, XXL, HUGE = 4, 8, 12, 16, 24, 32, 48
R_SMALL, R_PANEL = 6, 12
CAPTION, SMALL, BODY, SUBTITLE, HEADING, DISPLAY = 10, 12, 14, 17, 23, 42
ICON, ICON_BIG, BRAND_ICON = 20, 28, 38
BUTTON_H, TILE_H, NAV_H = 40, 84, 44
RAIL_WIDTH, RAIL_COMPACT = 218, 208
HERO_MIN, HERO_MAX = 218, 600
CARDS_COLUMNS = 930
WIDE_CONTENT, TILE_LARGE, PICKER_ROWS = 1250, 144, 5
CONTENT_MAX, ROOMY_WIDTH, TALL_HEIGHT = 1920, 1750, 1000
DEFAULT_SIZE, MIN_SIZE = (1180, 760), (960, 600)
COMPACT_WIDTH = 1100
TILE_SIZE = (154, 176)
THUMB_SIZE = (130, 132)
MOTION_MS, NOTICE_MS = 150, 6500
UI = "Inter"
SERIF = "Georgia"


def landing_hero_height(width: int, viewport_height: int) -> int:
    extra = max(0, viewport_height - (DEFAULT_SIZE[1] - XL))
    return max(HERO_MIN, min(HERO_MAX, round(width * 0.20 + extra * 0.65)))


def font(size: int = BODY, *, bold: bool = False, serif: bool = False) -> QFont:
    f = QFont()
    f.setFamilies([SERIF, "DejaVu Serif", "serif"] if serif else [UI, "Segoe UI", "sans-serif"])
    f.setPixelSize(size)
    f.setBold(bold)
    return f


def apply(app: QApplication) -> None:
    global UI
    app.setStyle("Fusion")
    # The Windows offscreen plugin has no system font catalogue. Register the OS
    # display face for the renderer just as the native Windows plugin would.
    if (
        app.platformName() == "offscreen"
        and "WINDIR" in os.environ
        and SERIF not in QFontDatabase.families()
    ):
        for filename in ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    face = QFontDatabase.addApplicationFont(str(ASSETS / "window/fonts/Inter-Medium.ttf"))
    if families := QFontDatabase.applicationFontFamilies(face):
        UI = families[0]
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    palette = app.palette()
    for role, colour in (
        (QPalette.ColorRole.Window, CANVAS),
        (QPalette.ColorRole.WindowText, INK),
        (QPalette.ColorRole.Base, PANEL),
        (QPalette.ColorRole.AlternateBase, RAISED),
        (QPalette.ColorRole.Text, INK),
        (QPalette.ColorRole.Button, RAISED),
        (QPalette.ColorRole.ButtonText, INK),
        (QPalette.ColorRole.Highlight, GOLD),
        (QPalette.ColorRole.HighlightedText, ON_GOLD),
        (QPalette.ColorRole.Link, GOLD),
        (QPalette.ColorRole.ToolTipBase, PANEL),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
    ):
        palette.setColor(role, QColor(colour))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(MUTED))
    app.setPalette(palette)
    app.setFont(font())
    app.setStyleSheet(stylesheet())


def stylesheet() -> str:
    return f"""
    QWidget {{ color: {INK}; font-family: "{UI}", "Segoe UI", sans-serif; font-size: {BODY}px; }}
    QMainWindow, QWidget#canvas, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {CANVAS}; }}
    QWidget#rail {{ background: {RAIL}; border-right: 1px solid {EDGE}; }}
    QScrollArea#railScroll, QWidget#railItems {{ background: {RAIL}; }}
    QWidget#installBar {{ background: {CANVAS}; border-bottom: 1px solid {EDGE}; }}
    QLabel {{ background: transparent; border: none; }}
    QLabel[role="muted"] {{ color: {MUTED}; font-size: {SMALL}px; }}
    QLabel[role="body"] {{ color: {TEXT}; }}
    QLabel[role="small"] {{ color: {TEXT}; font-size: {SMALL}px; }}
    QLabel[role="kicker"], QLabel[role="goldKicker"] {{
        color: {MUTED}; font-size: {CAPTION}px; font-weight: 700; letter-spacing: 1.6px;
    }}
    QLabel[role="goldKicker"] {{ color: {GOLD}; }}
    QLabel[role="heading"] {{ font-size: {HEADING}px; font-weight: 650; }}
    QLabel[role="subheading"] {{ font-size: {SUBTITLE}px; font-weight: 650; }}
    QLabel[role="display"], QLabel[role="metric"], QLabel[role="warnMetric"] {{
        font-family: "{SERIF}", "DejaVu Serif", serif; font-size: {DISPLAY}px;
    }}
    QLabel[role="brand"] {{ font-size: {HEADING}px; font-weight: 700; letter-spacing: 0.4px; }}
    QLabel[role="heroTitle"] {{ font-family: "{SERIF}", "DejaVu Serif", serif; font-weight: bold; font-size: {HUGE}px; }}
    QLabel[role="metric"] {{ color: {GOLD}; }}
    QLabel[role="warnMetric"] {{ color: {RED}; }}
    QFrame[role="panel"] {{ background: {PANEL}; border-radius: {R_PANEL}px; }}
    QFrame[role="line"] {{ background: {EDGE}; }}
    QLabel[role="notice"] {{ background: {ACTIVE}; color: {PALE}; padding: {MD}px; border-radius: {R_SMALL}px; }}
    QPushButton, QLineEdit, QListView {{ background: {RAISED}; border: {HAIR}px solid {EDGE};
        border-radius: {R_SMALL}px; padding: {SM}px {MD}px; }}
    QPushButton {{ color: {INK}; text-align: center; font-weight: 550; }}
    QPushButton:hover, QPushButton[role="quiet"]:hover, QPushButton[role="nav"]:hover,
    QPushButton[role="action"]:hover, QPushButton[role="scope"]:hover {{ background: {HOVER}; }}
    QPushButton:pressed, QPushButton[role="action"]:pressed {{ background: {EDGE}; }}
    QPushButton[role="primary"] {{ background: {GOLD}; color: {ON_GOLD}; border-color: {GOLD};
        padding: {SM}px {LG}px; font-weight: 700; }}
    QPushButton[role="primary"]:hover {{ background: {PALE}; }}
    QPushButton[role="primary"]:pressed {{ background: {PRESSED}; }}
    QPushButton[role="quiet"], QPushButton[role="nav"] {{ background: transparent; border-color: transparent; }}
    QPushButton[role="nav"] {{ text-align: left; color: {TEXT}; padding: {SM}px {MD}px; }}
    QPushButton[role="nav"]:checked {{ color: {GOLD}; background: {ACTIVE}; border-left: 3px solid {GOLD}; }}
    QPushButton[role="scope"], QPushButton[role="action"] {{ text-align: left; background: {PANEL};
        border-radius: {R_PANEL}px; padding: 0; }}
    QPushButton[role="scope"]:checked {{ background: {ACTIVE}; border-color: {GOLD}; }}
    QPushButton[role="action"] {{ border-color: {PANEL}; }}
    QPushButton[role="action"]:hover {{ border-color: {EDGE}; }}
    QPushButton[role="quiet"]:pressed, QPushButton[role="nav"]:pressed,
    QPushButton[role="scope"]:pressed, QPushButton[role="action"]:pressed {{ background: {EDGE}; }}
    QPushButton[role="scope"]:checked:hover, QPushButton[role="nav"]:checked:hover {{ border-color: {GOLD}; }}
    QPushButton:focus, QPushButton[role]:focus, QLineEdit:focus, QListView:focus {{
        border: {FOCUS}px solid {PALE}; }}
    QPushButton:disabled, QPushButton[role]:disabled {{ background: {PANEL}; color: {MUTED}; border-color: {EDGE}; }}
    QLineEdit:hover {{ border-color: {MUTED}; }}
    QLineEdit:disabled, QListView:disabled {{ color: {MUTED}; background: {PANEL}; }}
    QLineEdit {{ color: {INK}; selection-background-color: {GOLD}; selection-color: {ON_GOLD}; }}
    QListWidget#installList {{ background: {PANEL}; padding: 0; border: 1px solid {EDGE}; }}
    QListWidget#installList::item {{ padding: {XS}px {MD}px; border-radius: {R_SMALL}px; }}
    QListWidget#installList::item:selected {{ background: {ACTIVE}; color: {PALE}; }}
    QListWidget#installList:focus {{ border: {FOCUS}px solid {PALE}; }}
    QListView::item:selected {{ background: {ACTIVE}; color: {GOLD}; }}
    QProgressBar {{ background: {EDGE}; border: none; border-radius: {XS}px; }}
    QProgressBar::chunk {{ background: {GOLD}; border-radius: {XS}px; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: {SM}px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {EDGE}; min-height: {XXL}px; border-radius: {XS}px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QToolTip {{ background: {PANEL}; color: {INK}; border: 1px solid {EDGE}; padding: {XS}px; }}
    QDialog, QMessageBox {{ background: {CANVAS}; }}
    """
