from dataclasses import FrozenInstanceError

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLabel, QScrollArea, QToolButton, QWidget

from edopal.ui.widgets.card_detail import CardDetail, show_card_detail


def test_compact_card_detail_wraps_scrolls_and_stays_on_screen(qtbot, qapp, tmp_path):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    image = QImage(813, 1185, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    path = tmp_path / "card.png"
    assert image.save(str(path))
    detail = CardDetail(
        42, "A Long Card Name for the Title", "Spell · Quick-Play", "Effect text. " * 800, path, (("ID", "42"),)
    )
    with pytest.raises(FrozenInstanceError):
        detail.code = 0
    screen = parent.screen().availableGeometry()
    anchor = QRect(screen.bottomRight() - QPoint(45, 60), screen.bottomRight())
    popup = show_card_detail(parent, detail, anchor)
    qapp.processEvents()
    assert qapp.activePopupWidget() is popup
    assert screen.contains(popup.frameGeometry())
    assert popup.frameGeometry().right() < anchor.left()
    assert popup.width() <= 540 and popup.height() <= 320
    art = next(label for label in popup.findChildren(QLabel) if not label.pixmap().isNull())
    assert 185 <= art.pixmap().width() <= 200
    assert popup.findChild(QScrollArea).verticalScrollBar().maximum() > 0
    assert detail.text in {label.text() for label in popup.findChildren(QLabel)}
    qtbot.keyClick(popup, Qt.Key.Key_Escape)
    assert not qapp.activePopupWidget()


def test_missing_art_and_outside_dismissal(qtbot, qapp, tmp_path):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setGeometry(10, 10, 50, 60)
    parent.show()
    detail = CardDetail(42, "Book of Moon", "Spell · Quick-Play", "Turn it face-down.", tmp_path / "missing.jpg")
    popup = show_card_detail(parent, detail)
    assert popup.width() <= 330
    assert all(label.pixmap().isNull() for label in popup.findChildren(QLabel))
    qtbot.mouseClick(parent, Qt.MouseButton.LeftButton)
    assert not qapp.activePopupWidget()
    popup = show_card_detail(parent, detail)
    qtbot.mouseClick(popup.findChild(QToolButton), Qt.MouseButton.LeftButton)
    assert not qapp.activePopupWidget()
