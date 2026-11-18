import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from decks import Sections


def write_database(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE datas(id INTEGER PRIMARY KEY, alias INTEGER, ot INTEGER, type INTEGER,
                               race INTEGER, attribute INTEGER, level INTEGER, setcode INTEGER);
            CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT);
        """)
        for code, name, alias, scope, flags in rows:
            db.execute(
                "INSERT INTO datas VALUES(?,?,?,?,?,?,?,?)",
                (code, alias, scope, flags, 2, 16, 0x08080004, 0x20001),
            )
            db.execute("INSERT INTO texts VALUES(?,?)", (code, name))
        db.commit()


def write_deck(path: Path, sections: Sections) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#created by player"]
    for marker, section in zip(("#main", "#extra", "!side"), sections, strict=True):
        lines.extend((marker, *map(str, section)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


@pytest.fixture
def game(tmp_path: Path) -> Path:
    game = tmp_path / "Portable Game"
    (game / "config").mkdir(parents=True)
    (game / "config/strings.conf").write_text("!setname 0x1 Alpha\n!setname 0x2 Beta\n")
    repo = game / "repositories" / "Local"
    data = repo / "data"
    (repo / "rules").mkdir(parents=True)
    (game / "config/configs.json").write_text(
        json.dumps(
            {
                "repos": [
                    {"repo_name": "Local", "data_path": "data", "lflist_path": "rules"},
                    {"repo_path": "./repositories/disabled", "should_read": False},
                    {"repo_path": "./repositories/locale", "is_language": True},
                ]
            }
        )
    )
    write_database(game / "cards.cdb", [(c, f"Standard {c}", 0, 3, 0x21) for c in range(1, 45)])
    write_database(
        game / "expansions/a.cdb",
        [
            (100, "Moon Witch", 0, 3, 0x21),
            (101, "Moon Witch", 100, 3, 0x21),
            (120, "Historical Witch", 100, 3, 0x21),
            (130, "Moon Witch", 0, 3, 0x21),
            (140, "Unknown Alias", None, 3, 0x21),
            (150, "Custom", 0, 4, 0x21),
            (160, "Dragon Token", 0, 3, 0x4011),
            (170, "OCG", 0, 1, 0x21),
            (180, "Ritual", 0, 3, 0x81),
            (190, "Spell", 0, 3, 2),
            (200, "Trap", 0, 3, 4),
        ],
    )
    write_database(
        data / "non-delta.cdb",
        [
            (210, "Xyz", 0, 3, 0x800021),
            (220, "Link", 0, 3, 0x4000021),
            (230, "Former", 900, 3, 1),
            (240, "First", 901, 3, 1),
            (250, "Second", 901, 3, 1),
        ],
    )
    write_database(game / "repositories/legacy/cards.delta.cdb", [(260, "Legacy", 0, 3, 1)])
    for name in ("disabled", "locale"):
        write_database(game / "repositories" / name / "skip.delta.cdb", [(270, name, 0, 3, 1)])
    (data / "mappings.json").write_text(
        json.dumps(
            {"mappings": [[902, 210], [902, 210], [903, 100], [903, 101], [904, 9999], [1, 100]]}
        )
    )
    all_ids = "".join(f"{code} 3\n" for code in range(1, 45))
    (repo / "rules/season.conf").write_text(
        "!TCG\n1 0 --Banned\n100 1\n200 2\n!2005.4 GOAT\n$whitelist\n" + all_ids + "100 3\n",
        encoding="utf-8-sig",
    )
    write_deck(
        game / "deck/Family/Trial v1.YDK", Sections((100, 190, 200, 902), (210,), (101, 100))
    )
    write_deck(game / "deck/Family/Trial v2.ydk", Sections((100, 190, 200, 1), (210,), (101, 100)))
    write_deck(game / "deck/Family/Trial v3.ydk", Sections((30, 31, 32)))
    return game
