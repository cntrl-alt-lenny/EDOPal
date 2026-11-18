"""Static artwork inputs. Crop coordinates are pixels in the EXIF-oriented image."""

import math
import re
import struct
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QColorSpace, QGuiApplication, QImage, QImageReader, QPainter

WIDTH, HEIGHT = 708, 1016
MAX_BYTES, MAX_SIDE, MAX_PIXELS = 16 * 1024**2, 8192, 12_000_000
Crop = tuple[float, float, float, float]
_app: QGuiApplication | None = None


def ensure_gui() -> None:
    """Initialize on the main thread for headless use. Studio already owns a QApplication."""
    global _app
    if QGuiApplication.instance() is None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Call ensure_gui() on the main thread before starting a worker.")
        _app = QGuiApplication(["sleeves", "-platform", "offscreen"])
    elif not isinstance(QGuiApplication.instance(), QGuiApplication):
        raise RuntimeError("Sleeve rendering needs a QGuiApplication or QApplication.")


def colour(value: str) -> QColor:
    if not isinstance(value, str) or not re.fullmatch(r"#[\da-fA-F]{6}", value, re.ASCII):
        raise ValueError("Choose a six-digit RGB colour such as #184052.")
    return QColor(value)


def read_source(path: Path) -> bytes:
    """Read at most the compressed input limit, even if a file grows during the read."""
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Choose a picture smaller than 16 MiB.")
    return data


def _check_png(data: bytes) -> None:
    # Qt's PNG reader can silently show just the first frame of an APNG.
    offset = 8
    view = memoryview(data)
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 8 + length
        if end + 4 > len(data):
            break
        name = data[offset + 4 : offset + 8]
        if zlib.crc32(view[offset + 4 : end]) != struct.unpack_from(">I", data, end)[0]:
            break
        if name == b"acTL":
            raise ValueError("Export a single frame of this animation as a PNG or JPEG.")
        if name == b"IEND" and length == 0:
            return
        offset = end + 4
    raise ValueError("This PNG is damaged. Export a fresh copy and try again.")


def decode(data: bytes) -> QImage:
    """Decode a bounded static PNG/JPEG to oriented sRGB. Raises player-facing ValueError."""
    ensure_gui()
    if len(data) > MAX_BYTES:
        raise ValueError("Choose a picture smaller than 16 MiB.")
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    fmt = bytes(reader.format()).lower()
    if fmt not in (b"jpeg", b"png"):
        raise ValueError("Choose a still PNG or JPEG, or export a single frame in that format.")
    size = reader.size()
    w, h = size.width(), size.height()
    if min(w, h) <= 0:
        raise ValueError("This image is damaged. Export a fresh PNG or JPEG.")
    if max(w, h) > MAX_SIDE or w * h > MAX_PIXELS:
        raise ValueError("Resize the picture to at most 8192 px per side and 12 megapixels.")
    if fmt == b"png":
        _check_png(data)
    elif data.rfind(b"\xff\xd9") < 2:
        raise ValueError("This JPEG is incomplete. Export a fresh copy.")
    if reader.imageCount() > 1:
        raise ValueError("Export a single frame of this animation as a PNG or JPEG.")
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        raise ValueError("This image is damaged. Export a fresh PNG or JPEG.")
    if img.colorSpace().isValid():
        img = img.convertedToColorSpace(QColorSpace.NamedColorSpace.SRgb)
    return img


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("Could not encode sleeve image.")
    return bytes(buffer.data())


@dataclass(frozen=True)
class Layout:
    """Fill centers a portrait cut inside crop (x,y,w,h). Fit and framed show all the art."""

    mode: Literal["fill", "fit", "framed"] = "fill"
    crop: Crop | None = None
    background: str = "#142330"
    palette: tuple[str, ...] = ()  # Empty derives the frame inks from the art.

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        values = dict(data)
        values["palette"] = tuple(values.get("palette", ()))
        if values.get("crop") is not None:
            values["crop"] = tuple(values["crop"])
        return cls(**values)


DEFAULT_LAYOUT = Layout()


def compose(original: bytes, layout: Layout = DEFAULT_LAYOUT) -> QImage:
    """Make an opaque sleeve. The crop is relative to the picture after EXIF rotation."""
    from .ornaments import art_frame

    source = decode(original)
    area = QRectF(source.rect())
    if layout.crop is not None:
        if layout.mode != "fill":
            raise ValueError("Use fill mode to crop your picture.")
        if len(layout.crop) != 4 or not all(math.isfinite(x) for x in layout.crop):
            raise ValueError("Choose a crop inside the picture.")
        crop = QRectF(*layout.crop)
        if crop.isEmpty() or not area.contains(crop):
            raise ValueError("Choose a crop inside the picture.")
        area = crop
    dest = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB888)
    dest.fill(colour(layout.background))
    with QPainter(dest) as painter:
        painter.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        box = QRectF(dest.rect())
        if layout.mode == "framed":
            box = art_frame(painter, source, layout.palette)
            painter.fillRect(box, colour(layout.background))
        elif layout.mode not in ("fit", "fill"):
            raise ValueError("Choose fill, fit, or framed.")
        factor = min if layout.mode != "fill" else max
        scale = factor(box.width() / area.width(), box.height() / area.height())
        rect = QRectF(0, 0, area.width() * scale, area.height() * scale)
        rect.moveCenter(box.center())
        painter.setClipRect(box)
        painter.drawImage(rect, source, area)
    return dest
