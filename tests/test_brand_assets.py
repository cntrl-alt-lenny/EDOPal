from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from tools import build_icon


def test_vector_brand_has_recolourable_ink_and_transparent_features() -> None:
    svg = build_icon.SOURCE_ICON.with_name("logo.svg").read_bytes()
    (path,) = ElementTree.fromstring(svg)
    assert path.tag == "{http://www.w3.org/2000/svg}path"
    renderer = QSvgRenderer(QByteArray(svg.replace(b"<svg ", b'<svg color="#ea82cb" ', 1)))
    assert renderer.isValid()
    image = QImage(1254, 1254, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    for x, y in ((300, 750), (565, 810), (811, 810)):
        assert image.pixelColor(x, y) == QColor("#ea82cb")
    for x, y in (
        (0, 0),
        (265, 455),
        (500, 476),
        (850, 476),
        (414, 585),
        (950, 585),
        (470, 809),
        (680, 809),
        (930, 809),
    ):
        assert image.pixelColor(x, y).alpha() == 0


def test_packaged_icons_decode_at_every_size(tmp_path: Path) -> None:
    build_icon.build_icons(tmp_path)
    with (
        Image.open(build_icon.SOURCE_ICON) as original,
        Image.open(build_icon.SOURCE_ICON.with_name("app-icon-small.png")) as small,
        Image.open(tmp_path / "app-icon.png") as png,
        Image.open(tmp_path / "app-icon.ico") as ico,
        Image.open(tmp_path / "app-icon.icns") as icns,
    ):
        assert original.size == png.size == (1024, 1024)
        assert original.tobytes() == png.tobytes()
        assert ico.ico.sizes() == {(s, s) for s in (16, 20, 24, 32, 48, 64, 128, 256)}
        for size in ico.ico.sizes():
            expected = small if size[0] <= 24 else original
            assert (
                ico.ico.getimage(size).tobytes()
                == expected.resize(size, Image.Resampling.LANCZOS).tobytes()
            )
        assert (512, 512, 2) in icns.info["sizes"]
        for width, height, scale in icns.info["sizes"]:
            frame = icns.icns.getimage((width, height, scale))
            assert frame.size == (width * scale, height * scale)
            assert frame.getpixel((0, 0))[3] == 0
            assert frame.getchannel("A").getextrema() == (0, 255)


def test_replacement_art_cannot_inherit_stale_small_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacement = tmp_path / "app-icon.png"
    Image.new("RGBA", (256, 256), "red").save(replacement)
    small = build_icon.SOURCE_ICON.with_name("app-icon-small.png")
    replacement.with_name(small.name).write_bytes(small.read_bytes())
    monkeypatch.setattr(build_icon, "SOURCE_ICON", replacement)
    build_icon.build_icons(tmp_path / "icons")
    with Image.open(tmp_path / "icons/app-icon.ico") as ico:
        assert ico.ico.getimage((16, 16)).getpixel((8, 8)) == (255, 0, 0, 255)
