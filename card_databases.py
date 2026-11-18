"""Find the card databases used by a selected EDOPro installation."""

import os
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

# Cards with the SPELL and FIELD bits also use separate playmat artwork.
FIELD_SPELL_TYPE = 0x80002


class ArtworkIndex(dict[int, str]):
    """Names of requestable cards and the database attributes used by artwork sync."""

    def __init__(self) -> None:
        super().__init__()
        self.types: dict[int, int] = {}
        self.aliases: dict[int, int] = {}
        self.excluded: set[int] = set()
        self.tokens: set[int] = set()
        self.field_donors: dict[int, int] = {}

    def field_layout(self, cid: int, rush_ids: set[int]) -> str:
        if cid in rush_ids:
            return "rush"
        if self.types.get(cid, 0) & 0x8000000:
            return "skill"
        return "card"


# These Skill cards reproduce another illustration under an unrelated name.
# Shrine's prerelease alias identifies Sanctuary, a different illustration.
FIELD_PRINTS = {
    300101002: 23424603,  # Straight to the Grave / Wasteland
    300101005: 47355498,  # Tomb of the Pharaoh
    300102002: 22702055,  # Mythic Depths / Umi
    300201001: 59197169,  # Power of Dark / Yami
    300202001: 50913601,  # Peak Performance / Mountain
    300203001: 10080320,  # Dinosaur Kingdom / Jurassic World
    300304003: 90011152,  # Land of the Ojamas
    300304020: 12644061,  # Consumed By Darkness
    300305012: 44139064,  # Small Roid Big City
    101403062: 19832229,  # Salamangreat Shrine
}
# Rush redraws are common even with a matching name and alias. These two
# share their original illustration. The three anime aliases below were
# compared side by side and share rules but depict different artwork.
MATCHING_RUSH_PRINTS = {160203015, 160320032}
DIFFERENT_ANIME_PRINTS = {511030054, 511310036, 511756002}


def field_donor(
    cid: int, name: str, alias: int, official: dict[str, list[int]], names: dict[int, str]
) -> int | None:
    if cid in FIELD_PRINTS:
        return FIELD_PRINTS[cid]
    if cid in MATCHING_RUSH_PRINTS:
        return alias or None
    if (
        alias
        and name.endswith(" (Anime)")
        and cid not in DIFFERENT_ANIME_PRINTS
        and names.get(alias) == name.removesuffix(" (Anime)")
    ):
        return alias
    for suffix in (" (Action Field)", " (GOAT)", " (Skill Card)"):
        if name.endswith(suffix):
            candidates = official.get(name.removesuffix(suffix), [])
            if alias in candidates:
                return alias
            if len(candidates) == 1:
                return candidates[0]
    return None


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
    repositories = os.path.join(edopro_path, "repositories")
    update = os.path.join(repositories, "delta-bagooska")
    directories = [repositories, expansions]
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
                            or (
                                current == update
                                and entry.name.startswith(("release-", "prerelease-"))
                            )
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
    files = iter_db_files(edopro_path, unreadable=unreadable, confined=confined)
    # Preserve the precedence of the existing release database over provisional definitions.
    update = Path(edopro_path) / "repositories" / "delta-bagooska"

    def provisional(path: str) -> bool:
        db = Path(path)
        return (
            db.parent == update
            and db.name.startswith(("prerelease-", "release-"))
            and not db.name.endswith(".delta.cdb")
        )

    return sorted(files, key=lambda path: not provisional(path))


SMALL_DECK_INDEX_LIMIT = 500


def read_artwork_index(
    db_files: list[str],
    *,
    unreadable: list[str] | None = None,
    only_ids: set[int] | None = None,
    on_error: Callable[[str], None],
) -> tuple[ArtworkIndex, dict[str, list[int]], set[int], set[int]]:
    """
    Read every .cdb and return:

      id_to_name       - card_id -> card name (every card we know about)
      name_to_official - name -> official ids (IDs < 100,000,000 only)
      rush_ids         - card IDs that came from Rush Duel databases
      field_ids        - card IDs of Field Spells (they also get playmat art)
    """
    id_to_name = ArtworkIndex()
    name_to_official: dict[str, list[int]] = {}
    rush_ids: set[int] = set()
    field_ids: set[int] = set()

    # Deck passcodes are unbounded Python ints. SQLite only stores signed 64-bit IDs.
    # Use bound IN queries in sorted batches to retain database/ID precedence.
    join = " FROM datas d INNER JOIN texts t ON d.id = t.id"
    queries = [(join + " ORDER BY d.id", ())]
    if only_ids is not None:
        ids = sorted(cid for cid in only_ids if 0 < cid <= 2**63 - 1)
        queries = []
        for start in range(0, len(ids), SMALL_DECK_INDEX_LIMIT):
            batch = tuple(ids[start : start + SMALL_DECK_INDEX_LIMIT])
            placeholders = ",".join("?" for _ in batch)
            queries.append((f"{join} WHERE d.id IN ({placeholders}) ORDER BY d.id", batch))

    for db in db_files:
        # Skip empty placeholder files (EDOPro ships a 0-byte cards.cdb)
        if os.path.getsize(db) == 0:
            continue
        is_rush = "rush" in os.path.basename(db).lower()
        conn = None
        try:
            conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(datas)")}
            type_col = "d.type" if "type" in columns else "NULL"
            alias_col = "d.alias" if "alias" in columns else "NULL"
            for query, batch in queries:
                rows = conn.execute(f"SELECT d.id, t.name, {type_col}, {alias_col}" + query, batch)
                for card_id, name, card_type, alias in rows:
                    id_to_name[card_id] = name
                    if isinstance(card_type, int):
                        id_to_name.types[card_id] = card_type
                    if isinstance(alias, int):
                        id_to_name.aliases[card_id] = alias
                    if is_rush:
                        rush_ids.add(card_id)
        except sqlite3.Error as exc:
            if unreadable is not None:
                unreadable.append(db)
            on_error(f"Error reading {db}: {exc}")
        finally:
            if conn is not None:
                conn.close()

    for cid, kind in id_to_name.types.items():
        if kind & FIELD_SPELL_TYPE == FIELD_SPELL_TYPE:
            field_ids.add(cid)
        # Zero, bare TOKEN, and ACTION|TOKEN are records used by scripts.
        # Custom spells, traps and characters can have playable faces of their own.
        if kind in (0, 0x4000, 0x10004000):
            id_to_name.excluded.add(cid)
            del id_to_name[cid]
        elif kind & 0x4001 == 0x4001:
            id_to_name.tokens.add(cid)
    rush_ids.intersection_update(id_to_name)
    for cid, name in id_to_name.items():
        if cid < 100_000_000:
            name_to_official.setdefault(name, []).append(cid)
    for official_ids in name_to_official.values():
        official_ids.sort()

    # Some unofficial originals have no alias themselves, but the matching
    # anime printing points back to them (e.g. Sacred Defense Barrier).
    reverse: dict[int, list[int]] = {}
    for cid in field_ids:
        base = id_to_name.aliases.get(cid, 0)
        if (
            base >= 100_000_000
            and base in field_ids
            and id_to_name[cid] == f"{id_to_name[base]} (Anime)"
        ):
            reverse.setdefault(base, []).append(cid)

    for cid in field_ids:
        donor = field_donor(
            cid, id_to_name[cid], id_to_name.aliases.get(cid, 0), name_to_official, id_to_name
        )
        if not donor and len(reverse.get(cid, [])) == 1:
            donor = reverse[cid][0]
        if donor and donor != cid:
            id_to_name.field_donors[cid] = donor

    return id_to_name, name_to_official, rush_ids, field_ids


def iter_installed_printings(game: Path) -> Iterator[tuple[int, str, int | None]]:
    """Read card identities in database override order, including legacy name-only databases."""
    for path in get_db_files(str(game.resolve())):
        try:
            with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
                columns = {col[1] for col in db.execute("PRAGMA table_info(datas)")}
                kind = "datas.type" if "type" in columns else "NULL"
                yield from db.execute(
                    f"SELECT datas.id, texts.name, {kind} FROM datas JOIN texts USING(id) WHERE typeof(texts.name) = 'text'"
                )
        except sqlite3.Error:
            continue


def installed_card_names(game: Path) -> dict[int, str]:
    return {code: name for code, name, _ in iter_installed_printings(game)}
