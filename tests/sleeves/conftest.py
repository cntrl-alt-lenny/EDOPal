from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from sleeves import Library, ensure_gui, png_bytes


@pytest.fixture(scope="session", autouse=True)
def gui() -> None:
    ensure_gui()


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library(tmp_path / "state" / "sleeves")


@pytest.fixture
def blocks() -> bytes:
    image = QImage(90, 40, QImage.Format.Format_ARGB32)
    for x in range(90):
        for y in range(40):
            value = ("#bb3322", "#22aa44", "#3344cc")[x // 30]
            image.setPixelColor(x, y, QColor(value) if y > 3 else QColor(0, 0, 0, 0))
    return png_bytes(image)
