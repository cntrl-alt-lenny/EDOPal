"""Render Practice with invented opponents, exercises and journal entries."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

import main
from formats.catalogue import Catalogue
from practice.library import Library
from practice.matchups import Matchups, check_format
from studio import theme as t
from studio.context import AppContext, Installation
from studio.shell import StudioWindow
from tools.studio_screenshots import DemoSync, create_demo, grab_window, settle


def seed_practice(game: Path) -> None:
    folder = game / "WindBot/Decks"
    folder.mkdir(parents=True)
    for name in ("WindBot.exe", "ExecutorBase.dll"):
        (folder.parent / name).touch()
    names = (
        "Moonlit Knights",
        "Glass Garden",
        "Clockwork Court",
        "Amber Tide",
        "Lantern Keeper",
        "Bright Horizon",
        "Mirror Gate",
        "Silent Orchard",
        "Wandering Scholar",
        "Silver Chorus",
    )
    cards = (
        "Moonlit Sentry",
        "Dark Magician",
        "Mystical Space Typhoon",
        "Mirror Force",
        "Glass Wyvern",
        "Clockwork Owl",
        "Amber Bell",
        "Garden Keeper",
        "Fallen Crown",
        "Tidal Messenger",
        "Warden of Dawn",
        "Echo Herald",
        "Quiet Compass",
        "Dusk Courier",
        "Moon Gate",
        "Mirror Guard",
    )
    ids = [1000, 46986414, 5318639, 44095762, *range(1004, 1016)]
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        db.execute("ALTER TABLE datas ADD COLUMN alias INTEGER DEFAULT 0")
        db.execute("ALTER TABLE datas ADD COLUMN ot INTEGER DEFAULT 3")
        for i, name in enumerate(cards):
            db.execute("UPDATE texts SET id=?,name=? WHERE id=?", (ids[i], name, 1000 + i))
            db.execute(
                "UPDATE datas SET id=?,type=? WHERE id=?", (ids[i], (1, 1, 2, 4)[i % 4], 1000 + i)
            )
    with Image.open(t.ASSETS / "window/gallery/large/abyss-deck.png") as art:
        for i in range(len(cards)):
            x, y = 275 + round((i % 10) * 42.3), round(144 + (i // 10) * 59.5)
            art.crop((x, y, x + 38, y + 54)).convert("RGB").save(game / "pics" / f"{ids[i]}.jpg")
    rows = []
    for i, name in enumerate(names):
        rows.append(
            dict(name=name, deck=name, difficulty=i % 4, masterRules=[4, 5] if i % 2 else [5])
        )
        if i == len(names) - 1:
            continue
        codes = [ids[(j % 14) + i % 3] for j in range(40)]
        (folder / f"AI_{name}.ydk").write_text(
            "#main\n" + "\n".join(map(str, codes)) + "\n#extra\n!side\n", encoding="utf-8"
        )
    (folder.parent / "bots.json").write_text(json.dumps(rows), encoding="utf-8")
    (game / "lflist.conf").write_text(
        "!Practice League\n--League limits\n1015 0\n!Open League\n", encoding="utf-8"
    )
    puzzles = {
        "Tutorials": [
            "A light in the dark",
            "The second summon",
            "Cross the river",
            "A careful tribute",
        ],
        "Challenge Room/Endgames": ["The empty field", "Borrowed time", "One last draw"],
        "Challenge Room/Combos": ["Twin lanterns", "The mirror gate", "Around the clock"],
    }
    for collection, titles in puzzles.items():
        base = game / "puzzles" / collection
        base.mkdir(parents=True)
        for i, title in enumerate(titles):
            if title == "One last draw":
                (base / "One_last_draw.lua").write_text(
                    "Debug.ReloadFieldBegin(0)\nDebug.AddCard(12,0,0,LOCATION_HAND,0,POS_FACEDOWN)\n"
                    "Debug.AddCard(14,0,0,LOCATION_MZONE,1,POS_FACEUP_ATTACK)\nDebug.ReloadFieldEnd()\naux.BeginPuzzle()\n",
                    encoding="utf-8",
                )
                continue
            (base / (title.replace(" ", "_") + ".lua")).write_text(
                f"--[[message\nObjective: {'Win this turn with only one attack.' if not i else 'Finish the duel before your next draw.'}\nComplexity: {1 + i}/5\n]]\n"
                f"Debug.SetPlayerInfo(0,{2000 + i * 500},0,0)\nDebug.SetPlayerInfo(1,1600,0,0)\n"
                'Debug.ShowHint("Look at the card that can attack directly.")\n'
                f"--[[Solution:\nSummon the {title.lower()} guardian and attack directly.\n]]",
                encoding="utf-8",
            )
    (game / "puzzles/Challenge Room/Endgames/Unfinished_study.lua").write_bytes(b"\xff")


def seed_charmer_deck(game: Path) -> None:
    # An original practice fixture spanning the two snapshots, so the same
    # installed opponent can demonstrate both an exact fit and a close miss.
    cards = {
        759393: "Hiita the Fire Charmer",
        4376658: "Familiar-Possessed - Hiita",
        8814959: "Evilswarm Mandragora",
        15025844: "Mystical Elf",
        19327348: "Dharc the Dark Charmer",
        30608985: "Magical Undertaker",
        31764353: "Familiar-Possessed - Wynn",
        31887905: "Familiar-Possessed - Aussa",
        37744402: "Wynn the Wind Charmer",
        37970940: "Aussa the Earth Charmer",
        68881649: "Familiar-Possessed - Eria",
        69140098: "Gemini Elf",
        73318863: "Lyna the Light Charmer",
        74364659: "Eria the Water Charmer",
    }
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        db.executemany("INSERT INTO texts VALUES (?,?)", cards.items())
        db.executemany("INSERT INTO datas(id,type) VALUES (?,1)", ((key,) for key in cards))
    folder = game / "WindBot"
    path = folder / "bots.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries.append(dict(name="Elemental Lessons", deck="Charmers", difficulty=2, masterRules=[3]))
    path.write_text(json.dumps(entries), encoding="utf-8")
    (folder / "Decks/AI_Charmers.ydk").write_text(
        "#main\n" + "\n".join(map(str, (list(cards) * 3)[:40])), encoding="utf-8"
    )


def render(out: Path, size: tuple[int, int], root: Path) -> list[Path]:
    app = QApplication.instance()
    t.apply(app)
    cfg = create_demo(root)
    game = Path(cfg.edopro_path)
    seed_practice(game)
    seed_charmer_deck(game)
    ctx = AppContext(cfg, main.VERSION, DemoSync(), main._apply_gui_choices)
    window = StudioWindow(ctx, startup=False)
    selected = Installation.inspect(str(game))
    ctx.set_installation(selected)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    window.resize(*size)
    window.show()
    page = window.pages["practice"]

    def shot(name: str) -> None:
        target = out / (name + ".png")
        pixmap = grab_window(window)
        if pixmap.width() != size[0] or pixmap.height() != size[1]:
            raise ValueError(f"Window expanded to {pixmap.width()} × {pixmap.height()} for {name}")
        if not pixmap.save(str(target)):
            raise OSError(target)
        files.append(target)

    hold = Event()

    def waiting(*_: object) -> Library:
        hold.wait(8)
        raise InterruptedError("Scan cancelled.")

    try:
        window.navigate("practice")
        settle(lambda: page.browser is not None and not ctx.tasks.jobs)
        b = page.browser
        shot("01-opponents")
        b.puzzles.click()
        shot("02-puzzles")
        b.open.click()
        shot("03-journal")
        journal = page.journal
        journal.hint.click()
        journal.solution.click()
        journal.state.setCurrentText("Attempted")
        journal.note.setPlainText("Save the direct attack for the last move.")
        journal.played.setText("2026-11-15")

        def keep_editing() -> None:
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QMessageBox)
            overlay = QLabel(window)
            overlay.setPixmap(dialog.grab())
            overlay.adjustSize()
            overlay.move(
                (window.width() - overlay.width()) // 2, (window.height() - overlay.height()) // 2
            )
            overlay.show()
            shot("28-discard-confirmation")
            overlay.deleteLater()
            dialog.reject()

        QTimer.singleShot(0, keep_editing)
        page.escape()
        journal.save.click()
        settle(lambda: not ctx.tasks.jobs)
        shot("04-revealed-and-saved")
        page.escape()
        b.progress_filter.setCurrentText("Attempted")
        shot("05-progress-filter")
        b.progress_filter.setCurrentIndex(0)
        b.search.setText("Unfinished study")
        shot("06-unreadable-puzzle")
        b.bots.click()
        b.fit.setCurrentText("Does not fit")
        b.open.click()
        shot("07-list-conflict")
        page.escape()
        b.fit.setCurrentText("Cannot tell")
        shot("08-unmatched-deck")
        b.search.setText("No such opponent")
        shot("09-no-results")
        b.reset_filters()
        with patch("practice.bots.random.choice", side_effect=lambda rows: rows[len(rows) // 2]):
            b.pick.click()
        settle(lambda: not ctx.tasks.jobs)
        shot("10-random-opponent")
        b.list_choice.click()
        shot("22-list-picker")
        page.detail.search.setText("Edison")
        shot("23-find-format")
        ready = Event()

        def checking(*args: object) -> Matchups:
            ready.wait(8)
            return check_format(*args)

        with patch("studio.pages.practice.check_format", checking):
            page.detail.use.click()
            shot("29-checking-format")
            ready.set()
        settle(lambda: page.detail is None and not ctx.tasks.jobs)
        shot("24-closest-opponents")
        b.open.click()
        shot("25-format-guide")
        page.escape()
        b.list_choice.click()
        page.detail.search.setText("No such format")
        shot("26-no-matching-formats")
        page.detail.search.setText("Edison")
        page.format_cache.clear()
        with patch.object(Catalogue, "bundled_list", side_effect=OSError()):
            page.detail.use.click()
            settle(lambda: page.format_job is None)
            shot("27-unreadable-format")
        page.escape()
        b.list_choice.click()
        page.detail.search.setText("HAT")
        page.detail.use.click()
        settle(lambda: page.detail is None and not ctx.tasks.jobs)
        shot("30-historical-fit")
        ctx.set_installation(Installation())
        shot("11-choose-folder")
        with patch("studio.pages.practice.scan", waiting):
            ctx.set_installation(selected)
            shot("12-scanning")
            page.busy.cancel.click()
            hold.set()
            shot("13-cancelled")
        with patch(
            "studio.pages.practice.scan",
            side_effect=OSError("The selected game folder is unavailable."),
        ):
            page.action.click()
            settle(lambda: page.state == "error")
            shot("14-scan-error")
        empty = root / "Games/New installation"
        empty.mkdir()
        ctx.set_installation(Installation(str(empty)))
        settle(lambda: page.browser is not None)
        shot("15-missing-opponents")
        page.browser.puzzles.click()
        shot("16-missing-puzzles")
        (game / "lflist.conf").write_bytes(b"\xff")
        (game / "WindBot/ExecutorBase.dll").unlink()
        ctx.set_installation(selected)
        settle(lambda: page.browser is not None and not ctx.tasks.jobs)
        b = page.browser
        b.open.click()
        shot("17-unreadable-list")
        page.escape()
        b.puzzles.click()
        b.search.setText("One last draw")
        b.open.click()
        shot("18-unstated-fields")
        page.escape()
        b.search.setText("A careful tribute")
        b.open.click()
        page.journal.state.setCurrentText("Solved")
        page.journal.save.click()
        settle(lambda: not page.saving)
        page.escape()
        b.search.clear()
        b.collection.setCurrentIndex(1)
        shot("19-collection-progress")
        page.store.write_bytes(b"Unreadable synthetic journal")
        (game / "WindBot/bots.json").write_text("incomplete", encoding="utf-8")
        b.refresh.click()
        settle(lambda: page.browser is not None and not ctx.tasks.jobs)
        shot("20-damaged-catalogue")
        page.browser.puzzles.click()
        page.browser.open.click()
        shot("21-unavailable-journal")
    finally:
        hold.set()
        settle(lambda: not ctx.tasks.jobs)
        window.close()
        app.processEvents()
    return files
