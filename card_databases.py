"""Find the card databases used by a selected EDOPro installation."""

import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

# Cards with the SPELL and FIELD bits also use separate playmat artwork.
FIELD_SPELL_TYPE = 0x80002


def iter_db_files(
    edopro_path: str,
    *,
    unreadable: list[str] | None = None,
    confined: bool = False,
    deadline: float | None = None,
) -> Iterator[str]:
    """Yield playable databases. The startup probe can stop between directory entries."""

    def expired() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    def record_error(exc: OSError) -> None:
        if unreadable is not None and not isinstance(exc, FileNotFoundError):
            unreadable.append(exc.filename or edopro_path)

    def local_path(path: str) -> bool:
        if not confined:
            return True
        try:
            info = os.lstat(path)
        except OSError as exc:
            record_error(exc)
            return False
        return not (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )

    root_db = os.path.join(edopro_path, "cards.cdb")
    if not expired() and local_path(root_db) and os.path.isfile(root_db):
        yield root_db

    expansions = os.path.join(edopro_path, "expansions")
    directories = [os.path.join(edopro_path, "repositories"), expansions]
    while directories and not expired():
        current = directories.pop()
        if not local_path(current):
            continue
        children = []
        try:
            with os.scandir(current) as scan:
                entries = sorted(scan, key=lambda entry: entry.name) if deadline is None else scan
                for entry in entries:
                    if expired():
                        return
                    if current != expansions and entry.is_dir(follow_symlinks=False):
                        if local_path(entry.path):
                            children.append(entry.path)
                    elif (
                        entry.name.endswith(".cdb")
                        and (
                            current == expansions
                            or entry.name.endswith(".delta.cdb")
                            or entry.name.startswith(("release-", "prerelease-"))
                        )
                        and local_path(entry.path)
                        and entry.is_file()
                    ):
                        yield entry.path
        except OSError as exc:
            record_error(exc)
        directories.extend(reversed(children))


def get_db_files(
    edopro_path: str, *, unreadable: list[str] | None = None, confined: bool = False
) -> list[str]:
    return list(iter_db_files(edopro_path, unreadable=unreadable, confined=confined))


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
