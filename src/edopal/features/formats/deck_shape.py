"""A copy-by-copy view of the main deck and its monster level/rank curve."""

from collections import Counter
from math import ceil

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QToolTip, QWidget

from edopal.ui import theme as t

from .widgets import layout

TYPE_COLORS = {"Monster": "#ddac7b", "Spell": "#82bba9", "Trap": "#cc92ae"}
UNKNOWN = ("Unknown", "#96969e")
XYZ_COLOR = "#43464e"
XYZ_OUTLINE = "#a0a0aa"
TYPE_GROUPS = {
    "Monster": {
        "Normal": ("Normal", "#e5c26d"),
        "Effect": ("Effect", "#dea16e"),
        "Ritual": ("Ritual Monster", "#96b8ee"),
        "Fusion": ("Fusion", "#b58ad2"),
        "Synchro": ("Synchro", "#eeefed"),
        "Xyz": ("Xyz", XYZ_COLOR),
        "Pendulum": ("Pendulum", "#62cec2"),
        "Link": ("Link", "#648edb"),
        "": ("Monster", TYPE_COLORS["Monster"]),
    },
    "Spell": {
        "Normal": ("Spell", "#61ad89"),
        "Quick-Play": ("Quick-Play Spell", "#a8db82"),
        "Continuous": ("Continuous Spell", "#52956d"),
        "Equip": ("Equip Spell", "#83cf9d"),
        "Field": ("Field Spell", "#93ad57"),
        "Ritual": ("Ritual Spell", "#53bba5"),
    },
    "Trap": {
        "Normal": ("Trap", "#d47fa8"),
        "Continuous": ("Continuous Trap", "#eda5bf"),
        "Counter": ("Counter Trap", "#b965a5"),
    },
}
ATTRIBUTES = {
    32: ("Dark", "#b79dd7"),
    16: ("Light", "#ebd785"),
    1: ("Earth", "#c19670"),
    2: ("Water", "#79aee0"),
    4: ("Fire", "#e18471"),
    8: ("Wind", "#95c9a3"),
    64: ("Divine", "#e8b25f"),
}
ATTRIBUTE_NON_MONSTERS = {"Spell": ("Spell", "#b8b9bf"), "Trap": ("Trap", "#888990")}
LEGEND_HEIGHT, LEGEND_GAP = 11, 8


def _type_group(card):
    category, *subtypes = card.type_line.split(" · ") if card else ("",)
    groups = TYPE_GROUPS.get(category, {})
    # The type line lists frame-changing flags before secondary flags such as Tuner or Effect.
    for subtype in subtypes:
        if subtype in groups:
            return groups[subtype]
    return groups.get("", groups.get("Normal", UNKNOWN))


def _attribute_group(card):
    if card and card.category == "Monster":
        return ATTRIBUTES.get(card.attribute, UNKNOWN)
    return ATTRIBUTE_NON_MONSTERS.get(card.category, UNKNOWN) if card else UNKNOWN


def _legend_rows(items, width):
    rows, used = [], 0
    for item in items:
        item_width = item[2]
        if not rows or used + item_width > width:
            rows.append([])
            used = 0
        rows[-1].append(item)
        used += item_width + LEGEND_GAP
    return rows


def _draw_dot(painter, shape, color):
    painter.setPen(QColor(XYZ_OUTLINE) if color == XYZ_COLOR else Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(shape)


class ShapePlot(QWidget):
    def __init__(self, main, records):
        super().__init__()
        self.marks = []
        self.legend = []
        self.legend_font = t.font(10)
        self.mode = ""
        types = {group: [] for groups in TYPE_GROUPS.values() for group in groups.values()}
        attributes = {group: [] for group in (*ATTRIBUTES.values(), *ATTRIBUTE_NON_MONSTERS.values())}
        types[UNKNOWN], attributes[UNKNOWN] = [], []
        self.bins = [Counter() for _ in range(12)]
        for code, name in main:
            card = records.get(code)
            types[_type_group(card)].append(name)
            attributes[_attribute_group(card)].append(name)
            if card and (n := card.level_or_rank):
                self.bins[n - 1][name] += 1
        self.groups = {
            mode: [(group, names) for group, names in counts.items() if names]
            for mode, counts in (("Type", types), ("Attribute", attributes))
        }
        metrics = QFontMetrics(self.legend_font)
        self.legend_items = {}
        for mode, groups in self.groups.items():
            items = [(f"{group} {len(names)}", color) for (group, color), names in groups]
            self.legend_items[mode] = [(text, color, metrics.horizontalAdvance(text) + 10) for text, color in items]
        width = 148
        while any(len(_legend_rows(items, width)) > 3 for items in self.legend_items.values()):
            width += LEGEND_GAP
        self.setMinimumSize(width, 44)
        self.set_mode("Type")

    def set_mode(self, mode):
        self.mode = mode
        self.setAccessibleName(f"Main Deck by {mode}")
        if mode == "Level":
            summary = [f"{n}: {names.total()}" for n, names in enumerate(self.bins, 1)]
        else:
            summary = [text for text, _, _ in self.legend_items[mode]]
        self.setAccessibleDescription(", ".join(summary))
        self._arrange()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        self.marks = []
        self.legend = []
        w, h = self.width(), self.height()
        if self.mode == "Level":
            axis = self._level_axis()
            bin_width = (w - axis.width()) / len(self.bins)
            peak = max(1, *(names.total() for names in self.bins))
            for i, names in enumerate(self.bins):
                count = names.total()
                cell = QRectF(axis.right() + i * bin_width, 0, bin_width, h)
                bar_h = max(2, (h - 15) * count / peak)
                shape = QRectF(cell.left() + 2, h - 13 - bar_h, max(1, cell.width() - 4), bar_h)
                entries = [f"{name} ×{n}" if n > 1 else name for name, n in names.items()]
                tip = "\n".join([f"Level / Rank {i + 1} · {count}", *entries])
                self.marks.append((cell, shape, t.GOLD if count else t.EDGE, tip))
            return
        groups = self.groups[self.mode]
        if not groups:
            return
        legend_rows = _legend_rows(self.legend_items[self.mode], w)
        dot_height = h - LEGEND_HEIGHT * len(legend_rows) - 3
        for i, row in enumerate(legend_rows):
            x = 0
            for text, color, width in row:
                self.legend.append((QRectF(x, dot_height + 3 + i * LEGEND_HEIGHT, width, LEGEND_HEIGHT), text, color))
                x += width + LEGEND_GAP

        rows = 2
        # Keep copies in each category together, including 60-card lists.
        group_gaps = 4 * (len(groups) - 1)
        while (
            rows < max(2, int(dot_height / 7)) and sum(ceil(len(names) / rows) for _, names in groups) * 9 + group_gaps > w
        ):
            rows += 1
        columns = sum(ceil(len(names) / rows) for _, names in groups)
        pitch = min((w - group_gaps) / columns, dot_height / rows, 13)
        diameter = max(1, min(9, pitch - 2))
        y = (dot_height - rows * pitch) / 2
        x = 0
        for (group, color), names in groups:
            for i, name in enumerate(names):
                cell = QRectF(x + i // rows * pitch, y + i % rows * pitch, pitch, pitch)
                pad = (pitch - diameter) / 2
                self.marks.append((cell, cell.adjusted(pad, pad, -pad, -pad), color, f"{name}\n{group}"))
            x += ceil(len(names) / rows) * pitch + 4

    def _level_axis(self):
        return QRectF(0, 0, QFontMetrics(self.legend_font).horizontalAdvance("Level") + 8, self.height())

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(self.legend_font)
            if self.mode == "Level":
                painter.setPen(QColor(t.TEXT))
                painter.drawText(self._level_axis(), Qt.AlignmentFlag.AlignBottom, "Level")
            for i, (cell, shape, color, _) in enumerate(self.marks):
                if self.mode == "Level":
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(color))
                    painter.drawRoundedRect(shape, 1.5, 1.5)
                    painter.setPen(QColor(t.MUTED))
                    painter.drawText(cell, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, str(i + 1))
                else:
                    _draw_dot(painter, shape, color)
            for rect, text, color in self.legend:
                _draw_dot(painter, QRectF(rect.left() + 0.5, rect.center().y() - 3, 6, 6), color)
                painter.setPen(QColor(t.INK))
                painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)

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
