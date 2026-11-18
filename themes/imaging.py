"""Headless Qt image utilities. No widgets or platform-specific drawing APIs."""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QImageReader

_app: QGuiApplication | None = None
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 24_000_000


def ensure_gui() -> QGuiApplication:
    """Initialize on the main thread before starting background rendering jobs."""
    global _app
    existing = QGuiApplication.instance()
    if existing is None:
        _app = QGuiApplication([])
        return _app
    if not isinstance(existing, QGuiApplication):
        raise RuntimeError("Theme rendering requires a QGuiApplication.")
    return existing


def png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("Could not encode skin image.")
    return bytes(data)


def decode_photo(data: bytes) -> QImage:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Wallpaper exceeds the 20 MiB file limit or is empty.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        fmt = b"png"
    elif data.startswith(b"\xff\xd8\xff"):
        fmt = b"jpeg"
    else:
        raise ValueError("Select a PNG or JPEG wallpaper.")
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, fmt)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > MAX_PIXELS or max(size.toTuple()) > 16384:
        raise ValueError("Wallpaper dimensions exceed the limit or cannot be read.")
    image = reader.read()
    if image.isNull():
        raise ValueError("Damaged wallpaper image.")
    return image
