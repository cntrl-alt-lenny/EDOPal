"""Paint the 15 client textures and stretch their nine slices for previews."""

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen

from .colors import alpha, derive, mix
from .imaging import ensure_gui
from .model import Theme


@dataclass(frozen=True)
class Widget:
    path: str
    width: int = 48
    height: int = 48
    # EDOPro's tlx, tly, brx, bry order is top, left, bottom, right.
    border: tuple[int, int, int, int] = (8, 8, 8, 8)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


WIDGETS = {
    w.name: w
    for w in (
        Widget("Button"),
        Widget("Button/Pressed"),
        Widget("Button/ButtonDisabled"),
        Widget("MenuBar", border=(3, 3, 3, 3)),
        Widget("MenuBar/MenuPane"),
        Widget("MenuBar/MenuPressed"),
        Widget("CheckBox", 20, 20, (4, 4, 4, 4)),
        Widget("CheckBox/CheckBoxDisabled", 20, 20, (4, 4, 4, 4)),
        Widget("ComboBox"),
        Widget("ComboBox/ComboBoxDisabled"),
        Widget("SunkenPane", border=(2, 2, 2, 2)),
        Widget("TabControl"),
        Widget("TabControl/TabButton"),
        Widget("TabControl/TabButtonPressed"),
        Widget("Window", 64, 70, (30, 13, 13, 13)),
    )
}


@lru_cache(maxsize=240)
def _texture(theme: Theme, name: str) -> QImage:
    w = WIDGETS[name]
    c = derive(theme)
    active = name in ("Pressed", "MenuPressed", "TabButtonPressed")
    disabled = name.endswith("Disabled")
    inset = name in ("SunkenPane", "TabControl", "ComboBox", "CheckBox")
    top, bottom, edge = c.raised, c.surface, c.border
    if active:
        top, bottom, edge = c.inset, c.selection, c.accent
    elif disabled:
        top, bottom, edge = c.surface, c.inset, mix(c.surface, c.border, 0.55)
    elif inset:
        top, bottom = c.inset, c.surface
    elif name in ("Window", "MenuBar", "MenuPane"):
        top, bottom = c.surface, c.surface
    if name == "SunkenPane":
        # The client draws this texture over each lobby row's status colour.
        # An opaque centre would erase the dark bed for its fixed white text.
        top, bottom = alpha(top, 18), alpha(bottom, 12)
    img = QImage(w.width, w.height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bounds = QRectF(0.75, 0.75, w.width - 1.5, w.height - 1.5)
    radius = min(theme.roundness, min(w.border) - 1)
    gradient = QLinearGradient(0, 0, 0, w.height)
    gradient.setColorAt(0, QColor(top))
    gradient.setColorAt(1, QColor(bottom))
    p.setBrush(gradient)
    p.setPen(QPen(QColor(edge), 1.2))
    p.drawRoundedRect(bounds, radius, radius)
    if name == "Window":
        p.setClipRect(QRectF(1, 1, w.width - 2, 24))
        p.setBrush(QColor(c.raised))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bounds.adjusted(1, 1, -1, -1), radius, radius)
        p.setClipping(False)
        p.setPen(QPen(QColor(c.border), 1))
        p.drawLine(1, 25, w.width - 2, 25)
        p.setPen(QPen(QColor(c.accent), 2))
        p.drawLine(2, 8, 2, 18)
    elif active:
        p.fillRect(QRectF(5, w.height - 3.5, w.width - 10, 2), QColor(c.accent))
    elif not disabled and not inset:
        p.setPen(QPen(QColor(alpha(c.text, 12 + theme.depth // 3)), 1))
        p.drawLine(radius + 1, 2, w.width - radius - 2, 2)
    p.end()
    return img


def widget_image(theme: Theme, name: str) -> QImage:
    ensure_gui()
    return QImage(_texture(theme, name))


def draw_widget(p: QPainter, theme: Theme, name: str, dest: QRectF) -> None:
    spec = WIDGETS[name]
    img = _texture(theme, name)
    top, left, bottom, right = spec.border
    sx = (0, left, spec.width - right, spec.width)
    sy = (0, top, spec.height - bottom, spec.height)
    dx, dy = dest.x(), dest.y()
    w, h = dest.width(), dest.height()
    kx, ky = min(1, w / (left + right)), min(1, h / (top + bottom))
    xs = (dx, dx + left * kx, dx + w - right * kx, dx + w)
    ys = (dy, dy + top * ky, dy + h - bottom * ky, dy + h)
    antialias = p.testRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for j in range(3):
        for i in range(3):
            p.drawImage(
                QRectF(xs[i], ys[j], xs[i + 1] - xs[i], ys[j + 1] - ys[j]),
                img,
                QRectF(sx[i], sy[j], sx[i + 1] - sx[i], sy[j + 1] - sy[j]),
            )
    p.setRenderHint(QPainter.RenderHint.Antialiasing, antialias)
