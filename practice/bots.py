"""Match the installed opponents to deck files and check the selected room list."""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from decks.banlists import Banlist, parse_lists
from decks.cards import Catalog, load_catalog
from decks.legality import Verdict, preflight
from decks.model import Preview, Sections, preview
from decks.sources import bounded_text, discover
from doctor.decks import missing_windbot_files
from doctor.game_data import files_in, local_path
from tournament_decks import MAX_YDK, read_ydk

FIT = {"legal": "Fits", "illegal": "Does not fit", "could not verify": "Cannot tell"}


def rule_name(number: int) -> str:
    return {5: "Master Rules (2020)", 6: "Speed Duel", 7: "Rush Duel", 8: "GOAT"}.get(
        number, f"Master Rule {number}"
    )


@dataclass(frozen=True)
class Bot:
    name: str
    deck: str
    difficulty: str
    rules: tuple[int, ...]
    file: str
    preview: Preview | None
    problem: str
    checks: tuple[Verdict, ...]
    search: str

    def fit(self, index: int) -> str:
        return FIT[self.checks[index].status] if 0 <= index < len(self.checks) else "Cannot tell"


@dataclass
class Opponents:
    bots: list[Bot]
    lists: list[str]
    warning: str
    runtime: str


def runtime_warning(game: Path) -> str:
    missing = list(missing_windbot_files(game))
    if sys.platform == "win32":
        system = os.environ.get("SYSTEMROOT")
        if system and not (Path(system) / "Microsoft.NET/Framework/v4.0.30319/clr.dll").is_file():
            missing.append(".NET Framework 4")
    else:
        extra = "/usr/local/bin:/Library/Frameworks/Mono.framework/Versions/Current/Commands"
        for filename in ("configs.json", "user_configs.json"):
            try:
                settings = json.loads(
                    bounded_text(local_path(game, game / "config" / filename), 1 << 20)
                )
                if isinstance(settings, dict) and isinstance(
                    settings.get("posixPathExtension"), str
                ):
                    extra = settings["posixPathExtension"]
            except (OSError, ValueError, RecursionError):
                pass
        if not shutil.which(
            "mono", path=os.pathsep.join((os.defpath, os.environ.get("PATH", ""), extra))
        ):
            missing.append("Mono runtime")
    return (
        "Local AI may be unavailable. Could not find " + ", ".join(missing) + "." if missing else ""
    )


def _key(stem: str) -> str:
    return re.sub(r"[\s_-]", "", stem.casefold())


def match_file(deck: str, files: list[Path]) -> Path | None:
    wanted = "AI_" + deck
    exact = [p for p in files if p.stem.casefold() == wanted.casefold()]
    matches = exact or [p for p in files if _key(p.stem) == _key(wanted)]
    return matches[0] if len(matches) == 1 else None


def _bot(row: dict, files: list[Path], catalog: Catalog, lists: list[Banlist]) -> Bot:
    deck = row.get("deck", "")
    deck = deck if isinstance(deck, str) else ""
    path = match_file(deck, files)
    problem = "No single installed deck file matches this opponent."
    data = None
    if path:
        try:
            sections = read_ydk(bounded_text(path, MAX_YDK))
            data = preview(Sections(*(tuple(map(int, part)) for part in sections)), catalog)
            problem = ""
        except (OSError, ValueError):
            problem = "The matched deck file could not be read."
    checks = tuple(
        preflight(data, item, catalog)
        if data and not item.issues
        else Verdict("could not verify", ())
        for item in lists
    )
    names = [card.name for card in data.cards.values()] if data else []
    rules = row.get("masterRules")
    return Bot(
        row["name"],
        deck,
        str(row["difficulty"]) if type(row.get("difficulty")) is int else "Not stated",
        tuple(v for v in rules if type(v) is int) if isinstance(rules, list) else (),
        path.name if path else "",
        data,
        problem,
        checks,
        "\n".join([row["name"], *names]).casefold(),
    )


def read_bots(game: Path, cancel: Event) -> Opponents:
    runtime = runtime_warning(game)
    try:
        rows = json.loads(bounded_text(local_path(game, game / "WindBot/bots.json"), 2 << 20))
        if not isinstance(rows, list):
            raise ValueError
    except FileNotFoundError:
        return Opponents([], [], "No WindBot opponent catalogue is installed.", runtime)
    except (OSError, ValueError, RecursionError):
        return Opponents([], [], "The WindBot opponent catalogue could not be read.", runtime)
    sources = discover(game)
    lists = []
    for path in sources.lists:
        try:
            lists.extend(parse_lists(path))
        except (ValueError, OSError):
            lists.append(Banlist(path.name, path, 0, False, {}, ("Unreadable list",)))
    duplicates = Counter(item.name for item in lists)
    names = [
        item.name
        if duplicates[item.name] == 1
        else f"{item.name} · {item.path.relative_to(game)} · {item.line}"
        for item in lists
    ]
    files = files_in(game, game / "WindBot/Decks", ".ydk")
    catalog = load_catalog(sources)
    bots = []
    warning = ""
    for row in rows:
        if cancel.is_set():
            raise InterruptedError("Scan cancelled.")
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"].strip():
            bots.append(_bot(row, files, catalog, lists))
        else:
            warning = "Some opponent entries had no readable name."
    return Opponents(bots, names, warning, runtime)


def filter_bots(
    bots: list[Bot], term: str, rule: int | None, level: str, fit: str, index: int
) -> list[Bot]:
    return [
        b
        for b in bots
        if term.strip().casefold() in b.search
        and (rule is None or rule in b.rules)
        and (not level or b.difficulty == level)
        and (not fit or b.fit(index) == fit)
    ]


def random_opponent(rows: list[Bot], previous: Bot | None = None) -> Bot | None:
    if not rows:
        return None
    choices = [bot for bot in rows if bot is not previous] if previous else rows
    return random.choice(choices or rows)
