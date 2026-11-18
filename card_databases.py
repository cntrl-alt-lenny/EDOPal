"""Find the card databases used by a selected EDOPro installation."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

# Cards with the SPELL and FIELD bits also use separate playmat artwork.
FIELD_SPELL_TYPE = 0x80002


def get_db_files(edopro_path: str, *, unreadable: list[str] | None = None) -> list[str]:
    """Find card DBs in the EDOPro root, expansions/, and repository delta folders."""
    dbs: list[str] = []
    root_db = os.path.join(edopro_path, "cards.cdb")
    if os.path.exists(root_db):
        dbs.append(root_db)

    exp_path = os.path.join(edopro_path, "expansions")
    if os.path.isdir(exp_path):
        for filename in sorted(os.listdir(exp_path)):
            if filename.endswith(".cdb"):
                dbs.append(os.path.join(exp_path, filename))

    repo_path = os.path.join(edopro_path, "repositories")
    if os.path.isdir(repo_path):

        def record_error(exc: OSError) -> None:
            if unreadable is not None:
                unreadable.append(exc.filename or repo_path)

        for current_root, dirnames, filenames in os.walk(repo_path, onerror=record_error):
            dirnames.sort()
            for filename in sorted(filenames):
                if filename.endswith(".delta.cdb"):
                    dbs.append(os.path.join(current_root, filename))
    return dbs


def installed_card_names(game: Path) -> dict[int, str]:
    """Index locally playable IDs. Read-only connections cannot create missing databases."""
    cards = {}
    for path in get_db_files(str(game.resolve())):
        try:
            with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
                cards.update(
                    db.execute(
                        "SELECT datas.id, texts.name FROM datas JOIN texts USING(id) WHERE typeof(texts.name) = 'text'"
                    )
                )
        except sqlite3.Error:
            continue
    return cards
