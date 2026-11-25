"""A copy-by-copy view of the main deck and its monster level/rank curve."""

from collections import Counter
from math import ceil

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QToolTip, QWidget

from edopal.ui import theme as t

from .widgets import layout

TYPE_COLORS = {"Monster": "#ddac7b", "Spell": "#82bba9", "Trap": "#cc92ae", "Unknown": "#818188"}
ATTRIBUTES = {
    1: ("Earth", "#c19670"),
    2: ("Water", "#79aee0"),
    4: ("Fire", "#e18471"),
    8: ("Wind", "#95c9a3"),
    16: ("Light", "#ebd785"),
    32: ("Dark", "#b79dd7"),
    64: ("Divine", "#e8b25f"),
}
NO_ATTRIBUTE = ("No Attribute", "#79797f")


class ShapePlot(QWidget):
    def __init__(self, main, records):
        super().__init__()
        self.main = main
        self.records = records
        self.marks = []
        self.bins = []
        self.group_dots = {}
        self.mode = ""
        self.setMinimumHeight(34)
        self.set_mode("Type")

    def set_mode(self, mode):
        self.mode = mode
        self.group_dots = {}
        self.bins = [Counter() for _ in range(12)]
        for code, name in self.main:
            card = self.records.get(code)
            category = card.category if card else "Unknown"
            if mode == "Level":
                if card and (n := card.level_or_rank):
                    self.bins[n - 1][name] += 1
                continue
            if mode == "Type":
                group, color = category, TYPE_COLORS[category]
            elif category in ("Spell", "Trap"):
                group, color = NO_ATTRIBUTE
            else:
                group, color = ATTRIBUTES.get(card.attribute if card else None, ("Unknown", TYPE_COLORS["Unknown"]))
            self.group_dots.setdefault((group, color), []).append(name)
        self.setAccessibleName(f"Main Deck by {mode}")
        if mode == "Level":
            summary = [f"{n}: {names.total()}" for n, names in enumerate(self.bins, 1)]
        else:
            summary = [f"{group}: {len(names)}" for (group, _), names in self.group_dots.items()]
        self.setAccessibleDescription(", ".join(summary))
        self._arrange()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        self.marks = []
        w, h = self.width(), self.height()
        if self.mode == "Level":
            peak = max(1, *(names.total() for names in self.bins))
            for i, names in enumerate(self.bins):
                count = names.total()
                cell = QRectF(i * w / 12, 0, w / 12, h)
                bar_h = max(2, (h - 15) * count / peak)
                shape = QRectF(cell.left() + 2, h - 13 - bar_h, max(1, cell.width() - 4), bar_h)
                entries = [f"{name} ×{n}" if n > 1 else name for name, n in names.items()]
                tip = "\n".join([f"Level / Rank {i + 1} · {count}", *entries])
                self.marks.append((cell, shape, t.GOLD if count else t.EDGE, tip))
            return
        if not self.group_dots:
            return
        order = (
            list(TYPE_COLORS) if self.mode == "Type" else [a[0] for a in ATTRIBUTES.values()] + ["No Attribute", "Unknown"]
        )
        groups = sorted(self.group_dots.items(), key=lambda item: order.index(item[0][0]))
        rows = 2
        # Keep copies in each category together, including 60-card lists.
        while (
            rows < max(2, int(h / 6)) and sum(ceil(len(names) / rows) for _, names in groups) * 9 + 4 * (len(groups) - 1) > w
        ):
            rows += 1
        columns = sum(ceil(len(names) / rows) for _, names in groups)
        pitch = min((w - (len(groups) - 1) * 4) / columns, h / rows, 13)
        diameter = max(1, min(9, pitch - 2))
        y = (h - rows * pitch) / 2
        x = 0
        for (group, color), names in groups:
            for i, name in enumerate(names):
                cell = QRectF(x + i // rows * pitch, y + i % rows * pitch, pitch, pitch)
                pad = (pitch - diameter) / 2
                self.marks.append((cell, cell.adjusted(pad, pad, -pad, -pad), color, f"{name}\n{group}"))
            x += ceil(len(names) / rows) * pitch + 4

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for i, (cell, shape, color, _) in enumerate(self.marks):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                if self.mode == "Level":
                    painter.drawRoundedRect(shape, 1.5, 1.5)
                    painter.setPen(QColor(t.MUTED))
                    painter.setFont(t.font(9))
                    painter.drawText(cell, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, str(i + 1))
                else:
                    painter.drawEllipse(shape)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            for cell, _, _, tip in self.marks:
                if cell.contains(event.pos()):
                    QToolTip.showText(event.globalPos(), tip, self, cell.toAlignedRect())
                    return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().event(event)


class DeckShape(QWidget):
    def __init__(self, main, records):
        super().__init__()
        self.setObjectName("deckShape")
        column = layout(self, spacing=3)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(1)
        self.selector = QButtonGroup(self)
        self.plot = ShapePlot(main, records)
        for mode in ("Type", "Level", "Attribute"):
            button = QPushButton(mode)
            button.setObjectName(f"shape{mode}")
            button.setAccessibleName(f"Group Main Deck by {mode}")
            button.setCheckable(True)
            button.setChecked(mode == "Type")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(21)
            self.selector.addButton(button)
            controls.addWidget(button)
        self.selector.buttonClicked.connect(self._selected)
        column.addLayout(controls)
        column.addWidget(self.plot, 1)
        self.setStyleSheet(f"""
            QWidget#deckShape QPushButton {{ background: #242426; color:{t.MUTED}; border:1px solid {t.EDGE};
                border-radius:3px; padding:0 5px; font-size:11px; }}
            QWidget#deckShape QPushButton:checked {{ background:{t.ACTIVE}; color:{t.GOLD}; border-color:#796849; }}
            QWidget#deckShape QPushButton:hover, QWidget#deckShape QPushButton:focus {{ border-color:{t.GOLD}; }}
        """)

    def _selected(self, button):
        self.plot.set_mode(button.text())
