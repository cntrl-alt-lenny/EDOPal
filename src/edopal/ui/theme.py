"""EDOPal design tokens, icons and Qt widget roles. Dimensions use logical pixels."""

import os
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget

ASSETS = Path(__file__).resolve().parents[1] / "assets"
CANVAS, RAIL, PANEL, RAISED = "#191a1b", "#151618", "#232426", "#2c2d2e"
EDGE, HOVER, ACTIVE = "#3e3e3d", "#343536", "#383328"
INK, TEXT, MUTED = "#f5f1e9", "#d5d1c8", "#aaa9a6"
GOLD, PALE, PRESSED, ON_GOLD = "#ebc987", "#f7dfac", "#d5af6a", "#251f15"
GREEN, RED = "#99cfb2", "#f1a499"
HERO_DARK, HERO_LIGHT, BRONZE = "#272326", "#594539", "#af885a"
FOIL = {
    "hdcards": (HERO_DARK, HERO_LIGHT),
    "themes": ("#20252f", "#45526c"),
    "music": ("#27232f", "#574968"),
    "formats": ("#292421", "#6c523b"),
    # The Formats sleeve renderer references these named inks for individual eras.
    "backup": ("#28282a", "#6d5949"),
    "replays": ("#222c31", "#386477"),
    "decks": ("#292421", "#6c523b"),
    "health": ("#202927", "#416953"),
    "sleeves": ("#2c222a", "#684451"),
    "skins": ("#20252f", "#45526c"),
    "brand": ("#24262c", "#565363"),
}
CLEAR = "transparent"
HAIR, FOCUS = 1, 2
XS, SM, MD, LG, XL, XXL, HUGE = 4, 8, 12, 16, 24, 32, 48
R_SMALL, R_PANEL = 6, 12
CONTENT_MARGIN = XL
PAGE_TITLE = 36
CONTROL_H = 32
CAPTION, SMALL, BODY, SUBTITLE, HEADING, DISPLAY = 10, 12, 14, 17, 23, 42
ICON, ICON_BIG, BRAND_ICON = 20, 28, 38
BUTTON_H, TILE_H, NAV_H = 40, 84, 44
RAIL_WIDTH, RAIL_COMPACT, RAIL_ICONS = 218, 208, NAV_H + XL
WIDE_CONTENT, TILE_LARGE = 1250, 144
CONTENT_MAX, ROOMY_WIDTH, TALL_HEIGHT = 1920, 1750, 1000
DEFAULT_SIZE, MIN_SIZE = (1280, 800), (1024, 640)
COMPACT_WIDTH = 1100
TILE_SIZE = (154, 176)
MOTION_MS, NOTICE_MS = 150, 6500
UI = "Inter"
SERIF = "Georgia"


@lru_cache(maxsize=96)
def icon(name: str, color: str = TEXT) -> QIcon:
    """Render a named asset in the requested ink at standard and high-DPI sizes."""
    if not name or Path(name).name != name:
        return QIcon()
    path = ASSETS / "icons" / f"{name}.svg"
    try:
        svg = path.read_bytes().replace(b"currentColor", QColor(color).name().encode())
    except FileNotFoundError:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg))
    result = QIcon()
    for side in (16, 20, 24, 32, 40, 48, 64, 96):
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.GlobalColor.transparent)
        with QPainter(pixmap) as painter:
            renderer.render(painter)
        result.addPixmap(pixmap)
    return result


def font(size: int = BODY, *, bold: bool = False, serif: bool = False) -> QFont:
    f = QFont()
    f.setFamilies([SERIF, "DejaVu Serif", "serif"] if serif else [UI, "Segoe UI", "sans-serif"])
    f.setPixelSize(size)
    f.setBold(bold)
    return f


class _KeyboardFocus(QObject):
    """Keep keyboard focus visible independently of the checked/selected state."""

    def __init__(self, app):
        super().__init__(app)
        self.keyboard = False

    @staticmethod
    def mark(widget, visible):
        if isinstance(widget, QWidget) and bool(widget.property("keyboardFocus")) != visible:
            widget.setProperty("keyboardFocus", visible)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            self.keyboard = True
            self.mark(QApplication.focusWidget(), True)
        elif kind in (QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin):
            self.keyboard = False
            self.mark(QApplication.focusWidget(), False)
        elif kind == QEvent.Type.FocusIn and isinstance(watched, QWidget):
            reason = event.reason()
            by_key = reason in (
                Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.BacktabFocusReason,
                Qt.FocusReason.ShortcutFocusReason,
            )
            self.mark(watched, by_key or (self.keyboard and reason == Qt.FocusReason.OtherFocusReason))
        elif kind == QEvent.Type.FocusOut:
            self.mark(watched, False)
        return False


def apply(app: QApplication) -> None:
    global UI
    app.setStyle("Fusion")
    # The Windows offscreen plugin has no system font catalogue. Register the OS
    # display face for the renderer just as the native Windows plugin would.
    if app.platformName() == "offscreen" and "WINDIR" in os.environ and SERIF not in QFontDatabase.families():
        for filename in ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    face = QFontDatabase.addApplicationFont(str(ASSETS / "fonts/Inter-Medium.ttf"))
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
    if not app.findChild(_KeyboardFocus):
        app.installEventFilter(_KeyboardFocus(app))


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
    QLabel[role="pageTitle"] {{ font-family: "{SERIF}", "DejaVu Serif", serif; font-size: {PAGE_TITLE}px; }}
    QLabel[role="heroTitle"] {{ font-family: "{SERIF}", "DejaVu Serif", serif; font-weight: bold; font-size: {HUGE}px; }}
    QLabel[role="heroTitle"][compact="true"] {{ font-size: {XXL}px; }}
    QLabel[role="metric"] {{ color: {GOLD}; }}
    QLabel[role="warnMetric"] {{ color: {RED}; }}
    QFrame[role="panel"] {{ background: {PANEL}; border: {HAIR}px solid {EDGE}; border-radius: {R_PANEL}px; }}
    QFrame[role="line"] {{ background: {EDGE}; }}
    QLabel[role="notice"] {{ background: {ACTIVE}; color: {PALE}; padding: {MD}px; border-radius: {R_SMALL}px; }}
    QLabel[role="notice"][kind] {{ background: {PANEL}; color: {TEXT}; border-left: 3px solid {MUTED}; }}
    QLabel[role="notice"][kind="success"] {{ border-color: {GREEN}; }}
    QLabel[role="notice"][kind="warning"] {{ border-color: {GOLD}; }}
    QLabel[role="notice"][kind="error"] {{ border-color: {RED}; }}
    QLabel[role="tileTitle"] {{ color: {INK}; font-weight: 650; }}
    QLabel[role="progressPercent"] {{ color: {GOLD}; font-size: {SMALL}px; font-weight: 650; }}
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
    QPushButton[role="tile"] {{ background: {PANEL}; border: 2px solid {EDGE}; border-radius: {R_PANEL}px; padding: 0; }}
    QPushButton[role="tile"]:hover {{ background: {RAISED}; border-color: {MUTED}; }}
    QPushButton[role="tile"]:checked {{ background: {ACTIVE}; border-color: {GOLD}; }}
    QPushButton[role="tile"]:pressed {{ background: {HOVER}; }}
    QPushButton[role="quiet"]:pressed, QPushButton[role="nav"]:pressed,
    QPushButton[role="scope"]:pressed, QPushButton[role="action"]:pressed {{ background: {EDGE}; }}
    QPushButton[role="scope"]:checked:hover, QPushButton[role="nav"]:checked:hover {{ border-color: {GOLD}; }}
    QPushButton[keyboardFocus="true"]:focus, QPushButton[role][keyboardFocus="true"]:focus,
    QLineEdit[keyboardFocus="true"]:focus, QListView[keyboardFocus="true"]:focus {{
        border: {FOCUS}px solid {PALE}; }}
    QPushButton:disabled, QPushButton[role]:disabled {{ background: {PANEL}; color: {MUTED}; border-color: {EDGE}; }}
    QLineEdit:hover {{ border-color: {MUTED}; }}
    QLineEdit:disabled, QListView:disabled {{ color: {MUTED}; background: {PANEL}; }}
    QLineEdit {{ color: {INK}; selection-background-color: {GOLD}; selection-color: {ON_GOLD}; }}
    QListWidget#installList {{ background: {PANEL}; padding: 0; border: 1px solid {EDGE}; }}
    QListWidget#installList::item {{ padding: {XS}px {MD}px; border-radius: {R_SMALL}px; }}
    QListWidget#installList::item:selected {{ background: {ACTIVE}; color: {PALE}; }}
    QListWidget#installList[keyboardFocus="true"]:focus {{ border: {FOCUS}px solid {PALE}; }}
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
