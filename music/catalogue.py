"""Game metadata, including portable player-defined entries."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from uuid import uuid4

from config import user_data_dir
from music.storage import sync_directory

ART = Path(__file__).resolve().parents[1] / "assets" / "music"
LOCAL = "local"
SCENES = {
    "menu": "Main menu",
    "deck": "Deck editor",
    "duel": "Duels",
    "advantage": "LP advantage",
    "disadvantage": "LP disadvantage",
    "win": "Victory",
    "lose": "Defeat",
}
ID = re.compile(r"[a-z0-9]+")
CUSTOM = re.compile(r"user[a-f0-9]{32}")


@dataclass(frozen=True)
class Game:
    id: str
    title: str
    platform: str = ""
    year: int = 0  # Edition pictured when cover art is supplied.
    aliases: tuple[str, ...] = ()
    cover_page: str = ""
    fan_cover: bool = False
    region: str = ""
    first_year: int = 0
    first_region: str = ""
    series: str = "Personal"


def library_root() -> Path:
    return Path(user_data_dir()) / "soundtracks"


def atomic_json(path: Path, data: object) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
        temporary = Path(staged.name)
    try:
        with temporary.open("w", encoding="utf-8") as writer:
            json.dump(data, writer, ensure_ascii=False)
            writer.flush()
            os.fsync(writer.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@cache
def _official() -> dict[str, Game]:
    games = {LOCAL: Game(LOCAL, "EDOPro playlist", "On this installation")}
    for row in json.loads((ART / "catalog.json").read_bytes()):
        key = row["id"]
        if not ID.fullmatch(key) or key in games:
            raise ValueError(f"Invalid or repeated game ID {key!r}.")
        games[key] = Game(
            key,
            row["title"],
            row["platform"],
            row.get("year", row["first_year"]),
            tuple(row["aliases"]),
            row.get("cover_page", ""),
            "fanart" in row.get("cover_description", "").casefold(),
            row.get("cover_region", ""),
            row["first_year"],
            row["first_region"],
            row["series"],
        )
    return games


@cache
def official_games() -> frozenset[str]:
    """IDs that a portable content pack can address on every installation."""
    return frozenset(_official()) - {LOCAL}


def personal_games(root: Path) -> dict[str, Game]:
    games = {}
    for folder in root.glob("user*"):
        if not CUSTOM.fullmatch(folder.name) or not folder.is_dir() or folder.is_symlink():
            continue
        try:
            data = json.loads((folder / "game.json").read_bytes())
            title = data["title"]
            if not isinstance(title, str) or not title.strip():
                raise ValueError
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            title = f"Recovered game ({folder.name[4:12]})"
        games[folder.name] = Game(folder.name, title)
    return games


def catalog(*, root: Path | None = None) -> dict[str, Game]:
    return _official() | personal_games(root or library_root())


def save_personal_game(game: Game, root: Path) -> None:
    if not CUSTOM.fullmatch(game.id) or not game.title.strip():
        raise ValueError("Choose a name for this personal game.")
    folder = root / game.id
    if not folder.resolve().is_relative_to(root.resolve()):
        raise OSError("The game folder points outside the personal library.")
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / "game.json", {"title": game.title})


def create_game(title: str, *, root: Path | None = None) -> Game:
    game = Game("user" + uuid4().hex, title.strip())
    save_personal_game(game, root or library_root())
    return game


@lru_cache(maxsize=4096)
def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("’", "").replace("'", "")
    return " ".join(re.findall(r"[^\W_]+", value))


@lru_cache(maxsize=512)
def _patterns(game: Game) -> tuple[tuple[int, re.Pattern], ...]:
    aliases = {normalized(a) for a in (*game.aliases, game.title)} - {""}
    patterns = []
    for alias in aliases:
        # Generic series names must not claim an unknown sequel or edition.
        suffix = ""
        if alias in {"gx tag force", "duel monsters", "monster capsule"}:
            suffix = r"(?=$|\s+(?:ost|soundtrack|battle|theme|duel|menu|deck|wav|flac|mp3|ogg)\b)"
        if alias == "bam":
            # A one-word sound effect is ambiguous; accept it as a whole album name.
            patterns.append((len(alias), re.compile(r"^bam$")))
        else:
            patterns.append(
                (
                    len(alias.replace(" ", "")),
                    re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)" + suffix),
                )
            )
    return tuple(patterns)


def identify(name: str, games: dict[str, Game]) -> str:
    plain = normalized(name)
    best = 0
    candidates: set[str] = set()
    for game in games.values():
        for length, pattern in _patterns(game):
            if length >= best and pattern.search(plain):
                if length > best:
                    best = length
                    candidates.clear()
                candidates.add(game.id)
    return candidates.pop() if len(candidates) == 1 else LOCAL
