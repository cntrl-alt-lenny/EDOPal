import sqlite3
from collections import Counter
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QPushButton, QToolTip

from edopal.core.jobs import JobContext
from edopal.features.formats.deck import read_cards
from edopal.features.formats.deck_shape import DeckShape
from edopal.features.formats.media import load_faces


def test_shape_uses_cdb_fields_for_each_copy_and_hover(qtbot, tmp_path):
    with closing(sqlite3.connect(tmp_path / "cards.cdb")) as db, db:
        db.executescript("""
            CREATE TABLE datas(id INTEGER PRIMARY KEY, type INTEGER, level INTEGER, attribute INTEGER);
            CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT, desc TEXT);
            INSERT INTO datas VALUES (1,16777249,134742020,16),(2,8388641,4,32),(3,67108897,2,4),
                                     (4,65538,0,0),(5,4,0,0);
            INSERT INTO texts VALUES (1,'Pendulum','Pendulum effect'),(2,'Xyz',''),(3,'Link',''),
                                     (4,'Book of Moon','Change one face-up monster.'),(5,'Trap','');
        """)
    records, errors = read_cards(tmp_path, details_for=range(1, 7))
    assert not errors and records[4].type_line == "Spell · Quick-Play"
    assert records[4].text == "Change one face-up monster."
    assert records[1].level_or_rank == 4 and records[2].level_or_rank == 4 and records[3].level_or_rank is None
    main = [(1, "Pendulum")] * 2 + [(i, records[i].name) for i in (2, 3, 4, 5)] + [(6, "Missing")]
    shape = DeckShape(main, records)
    qtbot.addWidget(shape)
    shape.resize(166, 70)
    shape.show()
    plot = shape.plot

    def hover(index):
        cell = plot.marks[index][0]
        pos = cell.center().toPoint()
        QApplication.sendEvent(plot, QHelpEvent(QEvent.Type.ToolTip, pos, plot.mapToGlobal(pos)))
        return QToolTip.text()

    assert Counter(mark[3].splitlines()[-1] for mark in plot.marks) == {"Monster": 4, "Spell": 1, "Trap": 1, "Unknown": 1}
    assert "Pendulum" in hover(0)
    qtbot.mouseClick(shape.findChild(QPushButton, "shapeLevel"), Qt.MouseButton.LeftButton)
    assert len(plot.marks) == 12 and sum(group.total() for group in plot.bins) == 3
    assert hover(3).splitlines() == ["Level / Rank 4 · 3", "Pendulum ×2", "Xyz"]
    qtbot.mouseClick(shape.findChild(QPushButton, "shapeAttribute"), Qt.MouseButton.LeftButton)
    assert Counter(mark[3].splitlines()[-1] for mark in plot.marks) == {
        "Light": 2,
        "Dark": 1,
        "Fire": 1,
        "No Attribute": 2,
        "Unknown": 1,
    }
    light = next(i for i, mark in enumerate(plot.marks) if mark[3].endswith("Light"))
    assert "Light" in hover(light)
    QToolTip.hideText()


def test_sixty_copy_shape_fits_the_compact_header(qtbot):
    shape = DeckShape([(i, str(i)) for i in range(60)], {})
    qtbot.addWidget(shape)
    shape.resize(150, 68)
    shape.show()
    assert len(shape.plot.marks) == 60
    assert all(shape.plot.rect().contains(dot.toRect()) for _, dot, _, _ in shape.plot.marks)


def test_remote_face_is_available_to_click_details_for_lifetime_of_preview(tmp_path):
    jpeg = BytesIO()
    Image.new("RGB", (200, 290), "teal").save(jpeg, "JPEG")

    class ImageClient:
        def card_face(self, code):
            assert code == 42
            return jpeg.getvalue()

    directory = TemporaryDirectory(dir=tmp_path)
    with directory as folder:
        faces = load_faces(JobContext(), [42, 42], None, ImageClient(), directory)
        pixels, path = faces[42]
        assert path.parent == Path(folder) and path.read_bytes() == pixels
    assert not path.exists()
