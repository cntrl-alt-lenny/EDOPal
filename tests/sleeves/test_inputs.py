import struct
import zlib
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from sleeves import Layout, compose, decode, png_bytes, read_source
from sleeves.imaging import MAX_BYTES


def test_crop_and_fit_in_oriented_pixels(blocks: bytes) -> None:
    fit = compose(blocks, Layout("fit", background="#abcabc"))
    assert fit.pixelColor(354, 0).name() == "#abcabc"
    assert fit.pixelColor(50, 560).name() == "#bb3322"
    assert fit.pixelColor(650, 560).name() == "#3344cc"
    for left, col in [(0, "#bb3322"), (30, "#22aa44"), (60, "#3344cc")]:
        filled = compose(blocks, Layout("fill", (left, 0, 30, 40), "#abcabc"))
        assert filled.pixelColor(354, 0).name() == "#abcabc"
        assert filled.pixelColor(354, 508).name() == col
        raw = png_bytes(filled)
        assert raw[24:26] == b"\x08\x02"  # IHDR 8-bit RGB
        assert (filled.width(), filled.height()) == (708, 1016)
    for crop in [(0, 0, 0, 12), (1, 0, 90, 40), (-1, 0, 14, 9)]:
        with pytest.raises(ValueError, match="crop"):
            compose(blocks, Layout("fill", crop))


def test_framed_whole_art_and_chosen_inks(blocks: bytes) -> None:
    auto = compose(blocks, Layout("framed"))
    manual = compose(blocks, Layout("framed", palette=("#230000", "#ffceee")))
    assert auto != manual
    for result in (auto, manual):
        assert result.pixelColor(60, 515).name() == "#bb3322"
        assert result.pixelColor(648, 515).name() == "#3344cc"


def test_exif_precedes_crop() -> None:
    im = QImage(60, 30, QImage.Format.Format_RGB888)
    im.fill(QColor("#dd3311"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert im.save(buffer, "JPEG")
    # Tiny little-endian TIFF containing only EXIF Orientation=6 (90° CW).
    tiff = b"II\x2a\x00" + struct.pack("<IH", 8, 1) + struct.pack("<HHIHHI", 0x112, 3, 1, 6, 0, 0)
    exif = b"Exif\x00\x00" + tiff
    data = bytes(buffer.data())
    data = data[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + data[2:]
    assert (decode(data).width(), decode(data).height()) == (30, 60)
    assert not compose(data, Layout("fill", (1, 30, 20, 25))).isNull()


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def test_limits_before_decode_and_static_only(tmp_path: Path, blocks: bytes) -> None:
    for w, h in [(8193, 1), (4000, 4000)]:
        forged = (
            blocks[:8] + _chunk(b"IHDR", struct.pack(">II", w, h) + blocks[24:29]) + blocks[33:]
        )
        with pytest.raises(ValueError, match="Resize"):
            decode(forged)
    animated = blocks[:33] + _chunk(b"acTL", struct.pack(">II", 2, 0)) + blocks[33:]
    with pytest.raises(ValueError, match="single frame"):
        decode(animated)
    for data in (blocks[:-13], blocks[:42] + b"oops" + blocks[46:], b"GIF89a", b"hello"):
        with pytest.raises(ValueError, match="PNG|JPEG"):
            decode(data)
    path = tmp_path / "oversized.png"
    with path.open("wb") as stream:
        stream.truncate(MAX_BYTES + 1)
    with pytest.raises(ValueError, match="16 MiB"):
        read_source(path)
