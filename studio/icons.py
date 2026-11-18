"""Original monoline glyphs on a common 24-unit grid."""

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from studio import theme as t

PATHS = {
    "mydecks": "M3 9l9-5 9 5-9 5z M3 13l9 5 9-5 M3 17l9 5 9-5 M9 8l3-1.5L15 8",
    "replays": "M3 5h18v14H3z M3 9h18 M6 5l3 4 M12 5l3 4 M10 12l4 2-4 2z",
    "backup": "M3 12l2-8h14l2 8v7H3z M3 12h5l2 3h4l2-3h5 M12 11V6 M9 8l3-3 3 3",
    "health": "M12 2l8 3v6c0 5-4 8-8 11-4-3-8-6-8-11V5z M6 12h3l2-4 2 7 2-4h3",
    "cards": "M3 6l4-1 M2 8l3 12 4-1 M8 4l4-1 M7 6l2 15 M12 4h9v16h-9z M16.5 8l2 4-2 4-2-4z",
    "skins": "M3 7l9-5 9 5-9 5z M3 12l9 5 9-5 M3 17l9 5 9-5",
    "sleeves": "M6 3h12a1 1 0 011 1v16a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1z M8 6h8v12H8z M12 9l2 3-2 3-2-3z",
    "sounds": "M3 10v4 M7 6v12 M12 3v18 M17 8v8 M21 10v4",
    "music": "M9 17V6l11-3v12 M9 9l11-3 M9 17c0 4-7 5-7 2 0-3 7-5 7-2z M20 15c0 4-7 5-7 2 0-3 7-5 7-2z",
    "decks": "M7 3h10v5c0 4-2 6-5 7-3-1-5-3-5-7z M7 5H3v2c0 3 2 5 5 5 M17 5h4v2c0 3-2 5-5 5 M12 15v4 M8 21h8 M10 19h4",
    "settings": "M4 6h5m5 0h6 M4 12h9m5 0h2 M4 18h2m5 0h9 M10 4h3v4h-3z M14 10h3v4h-3z M7 16h3v4H7z",
    "folder": "M3 6h7l2 2h9v11H3z M3 6V4h7l2 2",
    "library": "M3 5h4v15H3z M10 4h4v16h-4z M16 6l3-1 3 14-3 1z",
    "deck": "M3 11l9-5 9 5-9 5z M3 15l9 5 9-5 M7 7l5-3 5 3",
    "coverage": "M3 19h18 M5 15v-4 M10 15V7 M15 15V4 M20 15v-6",
    "download": "M12 3v11 M7 10l5 5 5-5 M4 16v4h16v-4",
    "arrow": "M5 12h14 M14 7l5 5-5 5",
    "chevron": "M9 5l7 7-7 7",
    "back": "M19 12H5 M10 7l-5 5 5 5",
    "warning": "M12 3L2 20h20z M12 9v5 M12 17v.5",
    "check": "M4 12l5 5L20 6",
    "search": "M16 16l5 5 M18 10a8 8 0 11-16 0 8 8 0 1116 0z",
    "spark": "M12 2l2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5z",
    "link": "M6 18L19 5 M9 5h10v10",
}


@lru_cache(maxsize=96)
def icon(name: str, colour: str = t.TEXT) -> QIcon:
    if name == "mark":
        # The transparent mark reads better than the tiled app icon at sidebar size.
        mark = t.ASSETS / "logo-mark.png"
        return QIcon(str(mark)) if mark.is_file() else icon("brand", colour)
    if name == "brand":
        # Normal launches already resolved the source or one-file application icon.
        mark = QApplication.windowIcon()
        return QIcon(str(t.ASSETS / "app-icon.png")) if mark.isNull() else mark
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<path d="{PATHS[name]}" fill="none" stroke="{colour}" stroke-width="1.55" '
        'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    result = QIcon()
    # Qt selects the nearest resolution after a window moves between screens.
    for side in (16, 20, 24, 28, 38, 48, 56, 76, 96, 192):
        pix = QPixmap(side, side)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        renderer.render(painter, QRectF(pix.rect()))
        painter.end()
        result.addPixmap(pix)
    return result
