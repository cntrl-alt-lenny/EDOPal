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
from edopal.features.formats.deck import CardRecord, read_cards
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

    assert [text for _, text, _ in plot.legend] == [
        "Xyz 1",
        "Pendulum 2",
        "Link 1",
        "Quick-Play Spell 1",
        "Trap 1",
        "Unknown 1",
    ]
    assert hover(4) == "Book of Moon\nQuick-Play Spell"
    qtbot.mouseClick(shape.findChild(QPushButton, "shapeLevel"), Qt.MouseButton.LeftButton)
    assert not plot.legend and len(plot.marks) == 12 and sum(group.total() for group in plot.bins) == 3
    assert hover(3).splitlines() == ["Level / Rank 4 · 3", "Pendulum ×2", "Xyz"]
    assert hover(0) == "Level / Rank 1 · 0"
    qtbot.mouseClick(shape.findChild(QPushButton, "shapeAttribute"), Qt.MouseButton.LeftButton)
    assert Counter(mark[3].splitlines()[-1] for mark in plot.marks) == {
        "Light": 2,
        "Dark": 1,
        "Fire": 1,
        "Spell": 1,
        "Trap": 1,
        "Unknown": 1,
    }
    assert [text for _, text, _ in plot.legend] == ["Dark 1", "Light 2", "Fire 1", "Spell 1", "Trap 1", "Unknown 1"]
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


def test_type_frames_are_ordered_and_match_the_wrapped_legend(qtbot):
    frames = (
        (0x11, "Normal"),
        (0x1021, "Effect"),
        (0xA1, "Ritual Monster"),
        (0x61, "Fusion"),
        (0x2021, "Synchro"),
        (0x800021, "Xyz"),
        (0x1000011, "Pendulum"),
        (0x4000021, "Link"),
        (0x2, "Spell"),
        (0x10002, "Quick-Play Spell"),
        (0x20002, "Continuous Spell"),
        (0x40002, "Equip Spell"),
        (0x80002, "Field Spell"),
        (0x82, "Ritual Spell"),
        (0x4, "Trap"),
        (0x20004, "Continuous Trap"),
        (0x100004, "Counter Trap"),
    )
    records = {i: CardRecord(name, None, None, flags) for i, (flags, name) in enumerate(frames)}
    main = [(i, card.name) for i, card in reversed(records.items()) for _ in range(3)] + [(0, "Another Normal")] * 9
    shape = DeckShape(main, records)
    qtbot.addWidget(shape)
    plot = shape.plot
    shape.resize(plot.minimumWidth(), 72)
    shape.show()
    groups = {tip.splitlines()[-1]: color for _, _, color, tip in plot.marks}
    assert list(groups) == [name for _, name in frames]
    assert len(set(groups.values())) == len(frames)
    assert [text for _, text, _ in plot.legend] == ["Normal 12", *(f"{name} 3" for _, name in frames[1:])]
    assert [color for _, _, color in plot.legend] == list(groups.values())
    assert len(plot.marks) == 60
    assert max(dot.bottom() for _, dot, _, _ in plot.marks) < plot.legend[0][0].top()
    assert all(plot.rect().contains(item.toAlignedRect()) for item, _, _ in plot.legend)


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
