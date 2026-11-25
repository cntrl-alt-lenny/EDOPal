"""The complete tournament deck, with one printed face per copy."""

from __future__ import annotations

from math import ceil

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from edopal.ui import theme as t

from .widgets import ArtPanel, image, label, layout, pretty_date

ASPECT = 1185 / 813
MARKS = {0: ("#d8566d", "#661e2d"), 1: ("#ffacad", "#792f40"), 2: ("#fbc775", "#6a441c")}
HEADINGS = ("Main Deck", "Extra Deck", "Side Deck")


def badge(p, box, limit):
    # Snap both outlines to the same logical pixel grid. The tabular foot on
    # the one balances its short left flag (the stamped numeral in the v7 art).
    box = QRectF(round(box.x()), round(box.y()), 2 * round(box.width() / 2), 2 * round(box.height() / 2))
    bright, dark = MARKS[limit]
    p.setBrush(QColor(dark))
    p.setPen(QPen(QColor(bright), 1))
    p.drawRoundedRect(box, box.width() * 0.28, box.width() * 0.28)
    height = 2 * round(box.height() * 0.295)
    glyph = QPainterPath()
    if limit == 1:
        foot = min(11.9, 13 - 13 / height)  # Preserve a full raster pixel of the horizontal foot.
        glyph.moveTo(0.1, 2.7)
        for x, y in ((4, 0), (6, 0), (6, foot), (9, foot), (9, 13), (0, 13), (0, foot), (3.8, foot), (3.8, 2.9), (1.3, 4.7)):
            glyph.lineTo(x, y)
        glyph.closeSubpath()
    else:
        glyph.addText(QPointF(0, 0), t.font(60, bold=True), str(limit))
    ink = glyph.boundingRect()
    width = 2 * round(height * 0.34)
    glyph = QTransform.fromScale(width / ink.width(), height / ink.height()).map(glyph)
    glyph.translate(box.center() - glyph.boundingRect().center())
    p.fillPath(glyph, QColor("#fffcf4"))


class Marker(QWidget):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.setFixedSize(20, 20)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            badge(p, QRectF(1, 1, 18, 18), self.limit)


class DeckBanner(ArtPanel):
    def __init__(self, profile, event, deck, counts, source):
        super().__init__(deck=True)
        self.summary = None
        self.setFixedHeight(84)
        self.set_art(source)
        row = layout(self, QHBoxLayout, margin=0)
        row.setContentsMargins(18, 6, 18, 6)
        title = QVBoxLayout()
        title.setSpacing(0)
        title.addWidget(label(f"{profile['name']}  /  {event.name}", "deckKicker"))
        name = label(deck.name, "heading")
        name.setStyleSheet("font-size:32px;")
        title.addWidget(name)
        meta = [deck.builder]
        if n := deck.place:
            suffix = "th" if 10 < n % 100 < 14 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            meta.append(f"{n}{suffix}" + (f" of {event.players}" if event.players else ""))
        meta.append(pretty_date(event.date))
        title.addWidget(label("   ·   ".join(meta), "muted"))
        row.addLayout(title, 1)
        if counts:
            self.summary = summary = QWidget()
            summary.setFixedWidth(247)
            stats = layout(summary, spacing=0)
            stats.addWidget(label("Main Deck Composition", "muted"))
            values = QHBoxLayout()
            bars = QHBoxLayout()
            bars.setSpacing(2)
            for name, count, color in zip(
                ("Monsters", "Spells", "Traps"), counts, ("#ddac7b", "#82bba9", "#cc92ae"), strict=True
            ):
                col = QVBoxLayout()
                col.setSpacing(0)
                number = label(str(count), "subheading")
                number.setStyleSheet("font-size:21px;")
                col.addWidget(number)
                caption = label(name)
                caption.setStyleSheet(f"font-size:11px;color:{color};")
                col.addWidget(caption)
                values.addLayout(col, 1)
                if count:
                    bar = QWidget()
                    bar.setFixedHeight(3)
                    bar.setStyleSheet(f"background:{color};border-radius:1px;")
                    bars.addWidget(bar, count)
            stats.addLayout(values)
            stats.addSpacing(3)
            stats.addLayout(bars)
            row.addWidget(summary)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.width() >= 1350
        self.setFixedHeight(89 if wide else 84)
        if self.summary:
            self.summary.setFixedWidth(320 if wide else 247)


class CardGrid(QWidget):
    def __init__(self, preview):
        super().__init__()
        self.setObjectName("deckCards")
        self.sections = preview.detail.printings or tuple(
            tuple((0, name) for name, n in group for _ in range(n)) for group in preview.detail.sections
        )
        self.limits = preview.limits
        self.faces = {}
        self.small = {}
        self.tiles = []
        self.headers = []
        self.active = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Deck Cards")
        self.setAccessibleDescription(
            "\n".join(
                f"{heading}: " + ", ".join(f"{n} × {name}" for name, n in group)
                for heading, group in zip(HEADINGS, preview.detail.sections, strict=True)
            )
        )

    def set_faces(self, faces):
        self.faces.update({code: image(data) for code, data in faces.items()})
        self.small.clear()
        self.update()

    def resizeEvent(self, event):
        self.small.clear()
        self._arrange()
        super().resizeEvent(event)

    def _arrange(self):
        self.tiles, self.headers = [], []
        wide = self.width() > 1400
        gy, gutter, top = (7, 28, 39) if wide else (4, 21, 35)
        w, h = self.width() - 2, max(1, self.height() - top - 1)
        present = [(title, section) for title, section in zip(HEADINGS, self.sections, strict=True) if section]
        counts = [len(section) for _, section in present]
        available = w - (len(present) - 1) * gutter

        # Try row counts against both dimensions. Five rows at 8/3/3 columns fit
        # a 40/15/15 list; larger decks automatically get additional rows.
        def fit(rows):
            cols = [max(1, ceil(n / rows)) for n in counts]
            width = (available - (sum(cols) - len(cols)) * gy) / sum(cols)
            return min((h - (rows - 1) * gy) / rows / ASPECT, width)

        rows = max(range(1, max(counts, default=1) + 1), key=fit)
        cw = max(1, fit(rows))
        ch = cw * ASPECT
        cols = [max(1, ceil(n / rows)) for n in counts]
        gx = (available - sum(cols) * cw) / max(1, sum(cols) - len(cols))
        gy = (h - rows * ch) / max(1, rows - 1)
        x = 1
        for (heading, section), ncol in zip(present, cols, strict=True):
            sw = ncol * cw + (ncol - 1) * gx
            self.headers.append((QRectF(x, 0, sw, top), heading, section))
            for i, (code, name) in enumerate(section):
                pos = QRectF(x + i % ncol * (cw + gx), top + i // ncol * (ch + gy), cw, ch)
                self.tiles.append((pos, code, name))
            x += sw + gutter

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            for box, heading, section in self.headers:
                head = box.adjusted(0, 0, 0, -8)
                font = t.font(20, bold=True)
                font.setFamilies(["Outfit", t.UI])
                p.setFont(font)
                p.setPen(QColor(t.INK))
                p.drawText(head, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, heading)
                title_width = p.fontMetrics().horizontalAdvance(heading)
                p.setFont(t.font(12))
                caption = f"{len(section)} Cards"
                if heading == "Main Deck":
                    empty = [f"{title} 0" for title, cards in zip(HEADINGS, self.sections, strict=True) if not cards]
                    caption = "  ·  ".join([caption, *empty])
                if title_width + p.fontMetrics().horizontalAdvance(caption) + 8 > box.width():
                    caption = str(len(section))
                if title_width + p.fontMetrics().horizontalAdvance(caption) + 2 <= box.width():
                    p.setPen(QColor(t.MUTED))
                    p.drawText(head, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, caption)
                p.setPen(QColor(t.EDGE))
                p.drawLine(box.bottomLeft() - QPointF(0, 6), box.bottomRight() - QPointF(0, 6))
            for i, (box, code, name) in enumerate(self.tiles):
                pix = self.faces.get(code)
                if pix and not pix.isNull():
                    if code not in self.small:
                        dpr = self.devicePixelRatioF()
                        size = QSize(max(1, round(box.width() * dpr)), max(1, round(box.height() * dpr)))
                        self.small[code] = pix.scaled(
                            size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
                        )
                    p.drawPixmap(box, self.small[code], QRectF(self.small[code].rect()))
                else:
                    p.fillRect(box, QColor("#303033"))
                    p.setPen(QColor(t.TEXT))
                    p.setFont(t.font(10))
                    p.drawText(box.adjusted(2, 3, -2, -3), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, name)
                if (limit := self.limits.get(code)) in MARKS:
                    depth = max(2, round(box.width() * 0.067))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    for j in range(depth):
                        color = QColor(MARKS[limit][0])
                        color.setAlpha(round(239 * (1 - j / depth) ** 1.5))
                        p.setPen(QPen(color, 1))
                        p.drawRoundedRect(box.adjusted(j + 0.5, j + 0.5, -j - 0.5, -j - 0.5), 1, 1)
                    size = max(13, min(29, round(box.width() * 0.29)))
                    badge(p, QRectF(box.right() - size - 2, box.top() + 2, size, size), limit)
                if i == self.active:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(t.GOLD), 2))
                    p.drawRoundedRect(box.adjusted(-1, -1, 1, 1), 2, 2)
            if self.active is not None and self.active < len(self.tiles):
                self._zoom(p)

    def _zoom(self, p):
        box, code, name = self.tiles[self.active]
        pix = self.faces.get(code)
        if not pix or pix.isNull():
            return
        height = min(self.height() - 12, round((350 if self.width() > 1350 else 292) * ASPECT))
        width = height / ASPECT
        x = box.right() + 12
        if x + width + 4 > self.width():
            x = box.left() - width - 12
        y = max(5, min(box.center().y() - height / 2, self.height() - height - 5))
        zoom = QRectF(max(5, x), y, width, height)
        p.setBrush(QColor("#242326"))
        p.setPen(QPen(QColor("#a59169"), 1))
        p.drawRoundedRect(zoom.adjusted(-3, -3, 3, 3), 4, 4)
        p.drawPixmap(zoom, pix, QRectF(pix.rect()))

    def mouseMoveEvent(self, event):
        index = next((i for i, (box, _, _) in enumerate(self.tiles) if box.contains(event.position())), None)
        if index != self.active:
            self.active = index
            self.update()

    def leaveEvent(self, event):
        self.active = None
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.active = None
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.tiles:
            step = 1 if event.key() == Qt.Key.Key_Right else -1
            self.active = (self.active + step) % len(self.tiles) if self.active is not None else 0
        else:
            return super().keyPressEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self.active = None
        self.update()
        super().focusOutEvent(event)
