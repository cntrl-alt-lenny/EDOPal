"""Offline gallery for My decks, including malformed files and retired printings."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import main
from config import Config
from decks import DeckIndexer, Sections, encode_ydke
from studio import theme
from studio.context import AppContext, Installation
from studio.pages.mydecks import MyDecksPage
from studio.pages.mydecks_checks import Comparison, FormatCheck
from studio.pages.mydecks_library import Collection
from studio.pages.mydecks_widgets import CardGrid
from studio.shell import StudioWindow
from studio.widgets import Button
from tools.studio_screenshots import DemoSync, create_demo, settle

CARDS = (
    "Dark Magician",
    "Blue-Eyes White Dragon",
    "Gaia the Fierce Knight",
    "Summoned Skull",
    "Dark Armed Dragon",
    "Cyber Commander",
    "Marshmallon",
    "Monster Reborn",
    "Pot of Greed",
    "Mirror Force",
    "Dark Hole",
    "Fissure",
    "Mystical Space Typhoon",
    "Call of the Haunted",
    "Swords of Revealing Light",
    "Lightning Vortex",
    "Magic Cylinder",
    "Torrential Tribute",
    "Buster Blader",
    "Jinzo",
    "Graceful Charity",
    "Premature Burial",
    "Sangan",
    "Witch of the Black Forest",
    "Morphing Jar",
    "Cyber Jar",
    "Magician of Faith",
    "Heavy Storm",
    "Black Luster Soldier",
    "Thousand Dragon",
    "Dark Paladin",
    "Night Assailant",
)
SPELLS = {7, 8, 10, 11, 12, 14, 15, 20, 21, 27}
TRAPS = {9, 13, 16, 17}


def seed_game(root: Path) -> Config:
    cfg = create_demo(root)
    game = Path(cfg.edopro_path)
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        for field, default in (("alias", 0), ("ot", 3), ("level", 4)):
            db.execute(f"ALTER TABLE datas ADD COLUMN {field} INTEGER DEFAULT {default}")
        for i, name in enumerate(CARDS):
            kind = 2 if i in SPELLS else 4 if i in TRAPS else 0x41 if i in (29, 30) else 0x21
            db.execute("UPDATE datas SET type=? WHERE id=?", (kind, 1000 + i))
            db.execute("UPDATE texts SET name=? WHERE id=?", (name, 1000 + i))
    with Image.open(theme.ASSETS / "window/gallery/large/abyss-deck.png") as source:
        for i in range(len(CARDS)):
            dest = game / "pics" / f"{1000 + i}.jpg"
            if i < 28:
                x, y = 275 + round((i % 10) * 42.3), 144 + (i // 10) * 59.5
                source.crop((x, round(y), x + 38, round(y) + 54)).convert("RGB").save(dest)
            else:
                dest.unlink()
    (game / "lflist.conf").write_text(
        "!2005.4 GOAT\n$whitelist\n"
        + "".join(f"{1000 + i} {1 if i == 8 else 3}\n" for i in range(32))
        + "!Traditional\n1008 1\n",
        encoding="utf-8",
    )
    (game / "mappings.json").write_text('{"mappings":[[999,1024]]}', encoding="utf-8")
    folder = game / "deck"
    base = [
        1000 + i
        for i in (24, 22, 26, 8, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 23, 25, 27, 31)
    ]
    main_cards = base + [c for c in base if c != 1008] + [1024]
    sections = Sections(tuple(main_cards), (1029, 1030), (1018, 1019, 1028))

    def add(relative: str, sections: Sections, days: int) -> None:
        path = folder / f"{relative}.ydk"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                marker + "\n" + "".join(f"{c}\n" for c in cards)
                for marker, cards in zip(("#main", "#extra", "!side"), sections, strict=True)
            ),
            encoding="utf-8",
        )
        timestamp = 1794700800 - days * 86400
        os.utime(path, (timestamp, timestamp))

    add("Goat/Empty Jar - Athens", sections, 2)
    newer = list(main_cards)
    newer.remove(1026)
    newer.remove(1024)
    newer.remove(1025)
    newer.extend((1018, 1019, 1000))
    add("Goat/Empty Jar - Locals", Sections(tuple(newer), (1030,), (1026, 1028)), 1)
    for i, (name, offset) in enumerate(
        (
            ("Chaos Return", 0),
            ("Dark Magician", 3),
            ("Dragon Control", 4),
            ("Goat/Empty Jar - Spring", 2),
            ("Goat/Empty Jar - Summer", 5),
            ("Goat/Empty Jar - Winter", 7),
            ("Goat/Goat Control", 6),
            ("Tournament/Warrior Toolbox", 3),
            ("Tournament/Thunder Dragon", 1),
        )
    ):
        cards = tuple(1000 + ((c - 1000 + offset) % 29) for c in main_cards)
        add(name, Sections(cards, (1029,), tuple(1000 + j for j in range(i % 6))), i + 3)
    add("Archive/Relinquished", Sections((999,) + sections.main[1:], (), sections.side), 18)
    add("Spellcasters", Sections((1000, 1003, 1005) * 3), 22)
    (folder / "Archive/Damaged copy.ydk").write_text("a broken deck\n", encoding="utf-8")
    return cfg


def render(out: Path, size: tuple[int, int], root: Path) -> list[Path]:
    app = QApplication.instance()
    theme.apply(app)
    cfg = seed_game(root)
    ctx = AppContext(cfg, main.VERSION, DemoSync(), main._apply_gui_choices)
    win = StudioWindow(ctx, startup=False)
    selected = Installation.inspect(cfg.edopro_path)
    ctx.set_installation(selected)
    win.resize(*size)
    win.show()
    win.navigate("mydecks")
    page: MyDecksPage = win.current
    out.mkdir(parents=True, exist_ok=True)
    files = []

    def shot(name: str, *, held: bool = False) -> None:
        app.processEvents()
        if not held:
            settle(lambda: not ctx.tasks.jobs)
        win.animation.setCurrentTime(theme.MOTION_MS)
        page.slide.setCurrentTime(theme.MOTION_MS)
        win.setFocus(Qt.FocusReason.MouseFocusReason)

        def thumbs_ready() -> bool:
            for grid in page.view.findChildren(CardGrid):
                if grid.isVisible():
                    for i, tile in enumerate(grid.tiles.items):
                        idx = grid.model().index(i, 0)
                        if (
                            grid.viewport().rect().intersects(grid.visualRect(idx))
                            and tile.path not in grid.tiles.cache
                        ):
                            idx.data(Qt.ItemDataRole.DecorationRole)
                            grid.viewport().update()
                            return False
            return True

        settle(thumbs_ready)
        app.processEvents()
        target = out / f"mydecks-{name}.png"
        if not win.grab().save(str(target)):
            raise OSError(f"Could not save {target}")
        files.append(target)

    def collection() -> Collection:
        settle(lambda: isinstance(page.view, Collection))
        return page.view

    def verdict_ready() -> None:
        settle(lambda: isinstance(page.view, FormatCheck) and bool(page.view.verdicts))

    try:
        lib_view = collection()
        shot("01-library")
        lib_view.mode.setCurrentIndex(1)
        lib_view.search.setText("Magician of Faith")
        shot("02-card-search")
        lib_view.search.setText("Nothing like this")
        shot("03-no-matches")
        lib_view.search.clear()
        lib_view.mode.setCurrentIndex(0)
        lib_view.check.click()
        verdict_ready()
        shot("04-format-overview")
        matrix = page.view
        matrix.filter.setCurrentText("Legal")
        shot("24-legal-decks")
        matrix.filter.setCurrentIndex(0)
        for status, filename in (
            ("legal", "05-legal"),
            ("illegal", "06-illegal"),
            ("could not verify", "07-unverified"),
        ):
            row = next(
                i
                for i, (d, v) in enumerate(zip(page.lib.decks, matrix.verdicts, strict=True))
                if v.status == status and d.preview
            )
            matrix.results.itemActivated.emit(matrix.results.item(row))
            verdict_ready()
            shot(filename)
            page.escape()
        page.escape()
        lib_view.compare.click()
        settle(lambda: isinstance(page.view, Comparison) and not ctx.tasks.jobs)
        for box, name in (
            (page.view.before, "Goat/Empty Jar - Athens"),
            (page.view.after, "Goat/Empty Jar - Locals"),
        ):
            box.setCurrentText(name)
        shot("08-compare")
        page.escape()

        def open_named(name: str) -> None:
            row = next(i for i, r in enumerate(lib_view.model.rows) if r.deck.name == name)
            lib_view.rows.activated.emit(lib_view.model.index(row))

        open_named("Empty Jar - Athens")
        shot("09-main-deck")
        page.view.contents.sections.button(1).click()
        shot("10-extra-deck")
        page.view.contents.sections.button(2).click()
        shot("11-side-deck")
        page.escape()
        open_named("Damaged copy")
        shot("12-unreadable")
        page.escape()
        open_named("Relinquished")
        next(b for b in page.view.findChildren(Button) if b.text() == "Repair a copy").click()
        shot("13-repair")
        page.escape()
        page.escape()
        lib_view.import_btn.click()
        review = page.view
        shot("14-import")
        review.code.setText("incomplete")
        review.preview_btn.click()
        shot("15-invalid-code")
        review.code.setText(
            encode_ydke(
                next(d.preview.sections for d in page.lib.decks if d.name == "Empty Jar - Athens")
            )
        )
        review.preview_btn.click()
        shot("16-preview")
        with patch("studio.pages.mydecks_files._game_running", return_value=True):
            review.save_btn.click()
            settle(lambda: not page.writing)
            shot("17-game-open")
        gate = threading.Event()
        with patch(
            "studio.pages.mydecks_files._game_running",
            side_effect=lambda _: gate.wait(5) and False,
        ):
            page.retry.click()
            try:
                shot("18-saving", held=True)
            finally:
                gate.set()
            collection()
        shot("19-saved")
        ctx.set_installation(Installation(selected.path))
        shot("20-waiting-for-cards")
        ctx.set_installation(Installation())
        shot("21-no-installation")
        ctx.set_installation(Installation(str(root / "absent"), True))
        settle(lambda: page.view.initial is not None)
        shot("22-scan-error")
        empty = create_demo(root / "empty")
        (Path(empty.edopro_path) / "deck/Spellcasters.ydk").unlink()
        ctx.set_installation(Installation.inspect(empty.edopro_path))
        collection()
        shot("23-empty-library")
        (Path(empty.edopro_path) / "deck/First deck.ydk").write_text(
            "#main\n999\n", encoding="utf-8"
        )
        lib_view = page.view
        lib_view.refresh.click()
        lib_view = collection()
        lib_view.check.click()
        shot("25-no-lists")
        page.escape()
        open_named("First deck")
        next(b for b in page.view.findChildren(Button) if b.text() == "Repair a copy").click()
        shot("26-unavailable-repair")
        gate.clear()
        scan = DeckIndexer.scan

        def delayed(index: DeckIndexer) -> object:
            gate.wait(8)
            return scan(index)

        with patch.object(DeckIndexer, "scan", delayed):
            ctx.set_installation(selected)
            try:
                shot("27-scanning", held=True)
            finally:
                gate.set()
            collection()
        (Path(selected.path) / "lflists").mkdir(exist_ok=True)
        (Path(selected.path) / "lflists/incomplete.conf").write_text("unfinished", encoding="utf-8")
        page.view.refresh.click()
        collection().issue_btn.click()
        shot("28-scan-warning")
    finally:
        settle(lambda: not ctx.tasks.jobs)
        win.close()
        app.processEvents()
    return files
