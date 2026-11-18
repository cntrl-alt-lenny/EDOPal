"""Resolve historical card versions against the selected game's 2005.4 GOAT whitelist."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path

from card_databases import get_db_files
from config import BUILTIN_MANUAL_MAP

LIST_NAME = "2005.4 GOAT"
HISTORICAL_SUFFIXES = (" (goat)", " (pre-errata)")


def _lists_in_file(path: Path):
    limits = {}
    selected = whitelist = False
    with path.open(encoding="utf-8-sig", errors="replace") as source:
        for raw in source:
            line = raw.rstrip("\r\n")
            if line.startswith("!"):
                if selected and whitelist and limits:
                    yield limits
                selected = line == "!" + LIST_NAME
                limits = {}
                whitelist = False
            elif selected:
                if line.rstrip() == "$whitelist":
                    whitelist = True
                else:
                    values = line.split(maxsplit=2)
                    if len(values) >= 2 and values[0].isascii() and values[0].isdigit():
                        with suppress(ValueError):
                            code, limit = int(values[0]), int(values[1])
                            if 0 < code <= 0xFFFFFFFF:
                                limits[code] = limit
    if selected and whitelist and limits:
        yield limits


def installed_whitelist(game: Path) -> dict[int, int]:
    """Recognize EDOPro's standard list locations and locally configured list repositories."""
    game = game.resolve()
    files = [game / "expansions" / "lflist.conf", game / "lflist.conf"]
    repositories = {}
    for filename in ("configs.json", "user_configs.json"):
        try:
            config = json.loads((game / "config" / filename).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(config, dict) or not isinstance(config.get("repos"), list):
            continue
        for repo in config["repos"]:
            if not isinstance(repo, dict):
                continue
            root, lists = repo.get("repo_path"), repo.get("lflist_path")
            if isinstance(root, str) and isinstance(lists, str):
                candidate = (game / root / lists).resolve()
                if candidate.is_relative_to(game):
                    repositories[candidate] = repo.get("should_read", True) is True
    folders = {game / "lflists"}
    folders.update(path for path, enabled in repositories.items() if enabled)
    for folder in folders:
        with suppress(OSError):
            files.extend(folder.glob("*.conf"))
    recognized = {}
    for file in files:
        with suppress(OSError):
            if not file.is_file() or not file.resolve().is_relative_to(game):
                continue
            for limits in _lists_in_file(file):
                if recognized and limits != recognized:
                    return {}  # Two different lists use the same display name.
                recognized = limits
    return recognized


def installed_aliases(game: Path, names: dict[int, str]) -> dict[int, int | None]:
    aliases = {}
    for path in get_db_files(str(game.resolve())):
        try:
            with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
                join = " FROM datas JOIN texts USING (id) WHERE typeof(texts.name) = 'text'"
                try:
                    rows = db.execute("SELECT datas.id, datas.alias, texts.name" + join)
                except sqlite3.OperationalError:
                    # A later hand-made database can override an earlier card without an alias field.
                    rows = db.execute("SELECT datas.id, NULL, texts.name" + join)
                for code, alias, name in rows:
                    aliases[code] = (
                        alias
                        if (
                            isinstance(alias, int)
                            and 0 <= alias <= 0xFFFFFFFF
                            and names.get(code) == name
                        )
                        else None
                    )
        except sqlite3.Error:
            continue
    return aliases


def _identity(name: str) -> str:
    name = name.casefold()
    for suffix in HISTORICAL_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


@dataclass(frozen=True)
class Conversion:
    sections: tuple[tuple[str, ...], ...]
    changed: int
    replacements: dict[int, str]
    review: dict[int, str]


class GoatVersions:
    def __init__(
        self, limits: dict[int, int], names: dict[int, str], aliases: dict[int, int | None]
    ):
        self.limits = limits
        self.names = names
        self.aliases = aliases
        self.versions = defaultdict(list)
        for code, limit in limits.items():
            alias = aliases.get(code)
            name = names.get(code, "")
            if 0 < limit <= 3 and alias and name.casefold().endswith(HISTORICAL_SUFFIXES):
                self.versions[alias, _identity(name)].append(code)

    def limit(self, code: int) -> int | None:
        # LFList::GetLimitationIterator in EDOPro checks the code, then the
        # alias only for artwork variants within CardDataC's uint32_t offset of 10.
        if code in self.limits:
            return self.limits[code]
        alias = self.aliases.get(code)
        if alias and min((alias - code) & 0xFFFFFFFF, (code - alias) & 0xFFFFFFFF) < 10:
            return self.limits.get(alias)
        return None

    def convert(self, sections: tuple[tuple[str, ...], ...]) -> Conversion:
        all_codes = [int(token) for section in sections for token in section]
        # DeckManager counts copies by alias (or card ID) across main, extra and side.
        totals = Counter(self.aliases.get(code) or code for code in all_codes)
        replacements = {}
        review = {}
        for code in dict.fromkeys(all_codes):
            alias = self.aliases.get(code)
            name = self.names.get(code)
            if not name or alias is None:
                review[code] = "identity unavailable"
                continue
            family = alias or code
            limit = self.limit(code)
            if limit is not None:
                if limit <= 0:
                    review[code] = "forbidden"
                elif totals[family] > min(3, limit):
                    review[code] = "over copy limit"
                continue
            if self.limits.get(family, 1) <= 0:
                review[code] = "forbidden"
                continue
            choices = self.versions.get((family, _identity(name)), [])
            if len(choices) == 1:
                target = choices[0]
            else:
                pinned = [
                    candidate
                    for candidate in choices
                    if BUILTIN_MANUAL_MAP.get(str(candidate)) == str(code)
                ]
                if len(pinned) != 1:
                    review[code] = "ambiguous versions" if choices else "no verified version"
                    continue
                target = pinned[0]
            if totals[family] > self.limits[target]:
                review[code] = "over copy limit"
            else:
                replacements[code] = str(target)
        result = tuple(
            tuple(replacements.get(int(token), token) for token in section) for section in sections
        )
        return Conversion(
            result, sum(code in replacements for code in all_codes), replacements, review
        )
