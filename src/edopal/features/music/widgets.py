"""Music gallery controls painted in logical pixels using the shared design tokens."""

from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QPushButton, QSizePolicy, QWidget

from edopal.ui import theme as t

_ICONS = {
    "left": '<path d="m14 6-6 6 6 6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "right": '<path d="m10 6 6 6-6 6"/>',
    "play": '<path d="m8 4 12 8-12 8z" fill="currentColor" stroke="none"/>',
    "pause": '<path d="M7 5h3v14H7zm7 0h3v14h-3z" fill="currentColor" stroke="none"/>',
    "volume": '<path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    "mute": '<path d="M3 9h4l5-4v14l-5-4H3zM16 9l5 6m0-6-5 6"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    "menu": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18m-14 5h6"/>',
    "deck": '<rect x="9" y="4" width="11" height="15" rx="1.5"/><path d="m6 7-3 1 3 13 9-1"/>',
    "duel": '<path d="M4 3l4 1 11 13-2 2L5 7zM4 20l4-5m9-5 3-3 1-4-4 1-3 4M3 14l6 6m6-1 5-5"/>',
    "win": '<path d="M7 4h10v5a5 5 0 0 1-10 0zM7 6H3v2a4 4 0 0 0 4 4m10-6h4v2a4 4 0 0 1-4 4m-5 2v5m-4 1h8"/>',
    "lose": '<path d="M5 21V4c5-4 9 4 15 0v10c-6 4-10-4-15 0"/>',
}


def icon(name: str, color: str = t.TEXT) -> QIcon:
    shared = {"music": "music", "local": "folder", "restore": "undo"}
    if name in shared:
        return t.icon(shared[name], color)
    body = _ICONS[name].replace("currentColor", color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    pix = QPixmap(48, 48)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pix)


def icon_button(name: str, description: str, *, color=t.TEXT, parent=None) -> QPushButton:
    button = QPushButton(parent)
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(t.CONTROL_H, t.CONTROL_H)
    button.setStyleSheet("padding: 0px;")
    button.setAccessibleName(description)
    button.setToolTip(description)
    return button


def _text(painter, rect, text, size, color=t.INK, *, bold=False, align=Qt.AlignmentFlag.AlignLeft):
    painter.setFont(t.font(size, bold=bold))
    painter.setPen(QColor(color))
    label = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(rect.width())))
    painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | align, label)


def metadata(album):
    return "  ·  ".join(str(value) for value in (album.platform, album.year) if value)


def track_label(title):
    """Make lowercase filename slugs readable without altering the on-disk track."""
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", title):
        return title.replace("-", " ").title()
    return title


def mmss(seconds):
    minutes, seconds = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{seconds:02d}"


def _mat(painter, rect):
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, QColor(t.FOIL["music"][0]))
    gradient.setColorAt(1, QColor(t.RAISED))
    painter.fillRect(rect, gradient)


def _draw_disc(p, rect):
    """Grooves and a paper label, also used as the sleeve for tracks discovered on disk."""
    s = rect.width()
    p.setPen(QPen(QColor(t.EDGE), 1))
    p.setBrush(QColor(t.RAIL))
    p.drawEllipse(rect)
    for i in range(4, 23, 2):
        d = i * s / 90
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(t.RAISED if i % 4 else t.EDGE), 0.8))
        p.drawEllipse(rect.adjusted(d, d, -d, -d))
    d = s * 0.32
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(t.BRONZE))
    p.drawEllipse(rect.adjusted(d, d, -d, -d))
    p.setBrush(QColor(t.RAIL))
    p.drawEllipse(rect.center(), s * 0.023, s * 0.023)


def _art(p, rect, pix, *, record=False):
    _mat(p, rect)
    if rect.height() < 6:
        return
    margin = min(16, max(4, rect.height() * 0.07))
    box = rect.adjusted(margin, margin, -margin, -margin)
    if pix.isNull():
        side = min(box.height(), box.width()) * 0.86
        _draw_disc(p, QRectF(box.center().x() - side / 2, box.center().y() - side / 2, side, side))
        return
    ratio = pix.width() / pix.height()
    if record:
        # The sleeve overlaps a partially exposed record.
        h = min(box.height(), box.width() / (ratio + 0.32))
        w = h * ratio
        x = box.center().x() - (w + h * 0.32) / 2
        y = box.center().y() - h / 2
        _draw_disc(p, QRectF(x + w - h * 0.65, y + h * 0.018, h * 0.965, h * 0.965))
    else:
        h = min(box.height(), box.width() / ratio)
        w = h * ratio
        x, y = box.center().x() - w / 2, box.center().y() - h / 2
    target = QRectF(x, y, w, h)
    p.fillRect(target.translated(2, 3), QColor(t.RAIL))
    p.drawPixmap(target, pix, QRectF(pix.rect()))


class Sleeve(QWidget):
    def __init__(self):
        super().__init__()
        self.pix = QPixmap()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(56)

    def set_image(self, pix):
        self.pix = pix
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, t.R_SMALL, t.R_SMALL)
        p.setClipPath(clip)
        _art(p, r, self.pix, record=True)


class CoverTile(QAbstractButton):
    moved = Signal(int)

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(0, 0)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.album = None
        self.pix = QPixmap()
        self.selected = False
        self.columns = 4

    def set_album(self, album, pix):
        self.album, self.pix = album, pix
        state = self.availability()
        self.setAccessibleName(f"{album.name}, {metadata(album)}, {state}")
        self.setToolTip(f"{album.game}\n{metadata(album)}\n{state}")
        self.update()

    def availability(self):
        if self.album.pack is None:
            return f"{self.album.track_count} local track" + ("s" if self.album.track_count != 1 else "")
        if not self.album.track_count:
            return "Cover only"
        return "Cached" if self.album.cached else "Available"

    def keyPressEvent(self, e):
        directions = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -self.columns,
            Qt.Key.Key_Down: self.columns,
        }
        if e.key() in directions:
            self.moved.emit(directions[e.key()])
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        if self.album is None:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(r, t.R_PANEL, t.R_PANEL)
        p.setClipPath(clip)
        p.fillRect(r, QColor(t.ACTIVE if self.selected else t.HOVER if self.underMouse() else t.PANEL))
        large = r.height() >= 295
        caption = 68 if large else 56
        art = r.adjusted(0, 0, 0, -caption)
        _art(p, art, self.pix)
        state = self.availability()
        font = t.font(10, bold=True)
        badge_width = QFontMetrics(font).horizontalAdvance(state) + 14
        badge = QRectF(art.right() - badge_width - 5, art.top() + 5, badge_width, 18)
        p.setPen(Qt.PenStyle.NoPen)
        fill = QColor(t.RAIL)
        fill.setAlpha(232)
        p.setBrush(fill)
        p.drawRoundedRect(badge, 4, 4)
        _text(
            p, badge, state, 10, t.PALE if self.album.track_count else t.TEXT, bold=True, align=Qt.AlignmentFlag.AlignCenter
        )
        cap = QRectF(r.left() + 10, art.bottom() + 3, r.width() - 20, caption - 7)
        meta_size = 13 if large else t.SMALL
        meta_height = QFontMetrics(t.font(meta_size)).height()
        name_box = cap.adjusted(0, 0, 0, -meta_height)
        p.setFont(t.font(t.SUBTITLE if large else t.BODY, bold=True))
        p.setPen(QColor(t.INK))
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap
        name = self.album.name
        if p.boundingRect(name_box, flags, name).height() > name_box.height():
            p.setFont(t.font(t.SMALL, bold=True))
            if p.boundingRect(name_box, flags, name).height() > name_box.height():
                name = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, int(name_box.width()))
        p.drawText(name_box, flags, name)
        _text(p, QRectF(cap.left(), name_box.bottom(), cap.width(), meta_height), metadata(self.album), meta_size, t.MUTED)
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        focused = self.property("keyboardFocus")
        p.setPen(
            QPen(QColor(t.PALE if focused else t.GOLD if self.selected else t.EDGE), 2 if self.selected or focused else 1)
        )
        p.drawRoundedRect(r, t.R_PANEL, t.R_PANEL)


class TrackRow(QPushButton):
    """A single compact track with independently aligned number, title and duration."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedHeight(33)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.number = self.title = self.time = ""
        self.setStyleSheet(
            "QPushButton {border:1px solid transparent; background:transparent;}"
            f"QPushButton:checked {{background:{t.ACTIVE}; border-color:{t.EDGE};}}"
            f"QPushButton:hover {{background:{t.HOVER};}} QPushButton[keyboardFocus=true]:focus {{border-color:{t.PALE};}}"
        )

    def set_track(self, index, track, selected):
        self.number = f"{index + 1:02d}"
        self.title = track_label(track.title) if track.role else track.title
        self.time = mmss(track.duration_seconds) if track.duration_seconds else ""
        self.setToolTip(self.title)
        self.setAccessibleName(f"Preview {self.title}")
        self.setChecked(selected)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        w, h = self.width(), self.height()
        ink = t.PALE if self.isChecked() else t.TEXT
        _text(p, QRectF(9, 0, 24, h), self.number, 11, t.GOLD if self.isChecked() else t.MUTED)
        _text(p, QRectF(35, 0, w - 82, h), self.title, 14, ink)
        _text(p, QRectF(w - 45, 0, 35, h), self.time, 12, t.MUTED, align=Qt.AlignmentFlag.AlignRight)


class RoleButton(QAbstractButton):
    def __init__(self, role, title):
        super().__init__()
        self.role, self.title = role, title
        self.assignment = ""
        self.active = False
        self.glyph = icon(role, t.GOLD)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(65)

    def set_assignment(self, name, pool, selected, active):
        self.assignment = name if active else track_label(name)
        if len(pool) > 1:
            self.assignment = f"{self.assignment}  +{len(pool) - 1}" if active else f"{len(pool)} local tracks"
        self.active = active
        tracks = "\n".join(track_label(track) for track in pool)
        tip = f"{self.title} · sound/BGM/{self.role}\n" + (tracks or "No tracks in this folder.")
        if len(pool) > 1:
            tip += "\nEDOPro picks randomly from these tracks."
        if selected:
            tip += f"\nAssign {selected} to this role."
        self.setToolTip(tip)
        self.setAccessibleName(f"{self.title}, {self.assignment}")
        self.setAccessibleDescription(tip)
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        focused = self.property("keyboardFocus")
        p.setPen(QPen(QColor(t.PALE if focused else t.GOLD if self.active else t.EDGE), 2 if focused else 1))
        p.setBrush(QColor(t.ACTIVE if self.active else t.HOVER if self.underMouse() and self.isEnabled() else t.PANEL))
        p.drawRoundedRect(r, t.R_PANEL, t.R_PANEL)
        gap = 12 if r.width() > 145 else 8
        self.glyph.paint(p, gap, 12, 16, 16)
        _text(p, QRectF(gap + 22, 6, r.width() - gap - 24, 27), self.title, 14, t.INK, bold=True)
        _text(p, QRectF(gap, 32, r.width() - gap * 2, 23), self.assignment, 13, t.PALE if self.active else t.MUTED)
