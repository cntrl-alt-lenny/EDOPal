"""Turn local findings into a player-facing reading order and short evidence samples."""

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from itertools import islice
from pathlib import Path

import config
from doctor import Finding, ListConflict

AREAS = {
    "installation": "Installation",
    "process": "Installation",
    "state": "Previous changes",
    "settings": "Appearance and audio",
    "skin": "Appearance and audio",
    "fonts": "Appearance and audio",
    "music": "Appearance and audio",
    "audio": "Appearance and audio",
    "sound": "Appearance and audio",
    "repositories": "Game updates",
    "databases": "Cards and artwork",
    "storage": "Cards and artwork",
    "artwork": "Cards and artwork",
    "banlists": "Decks and duel rules",
    "decks": "Decks and duel rules",
    "windbot": "Practice duels",
}
RANK = {"problem": 0, "warning": 1, "info": 2, "ok": 3}
PAGE_NAMES = {
    "music": "Music",
    "cards": "HD Cards",
    "sounds": "Sounds",
    "skins": "Skins",
    "sleeves": "Sleeves",
    "mydecks": "My decks",
    "backup": "Backup & transfer",
}
ROUTES = {
    "music.disabled": "music",
    "music.missing": "music",
    "sound.missing": "sounds",
    "sound.disabled": "sounds",
    "skin.missing": "skins",
    "skin.malformed": "skins",
    "skin.images": "skins",
    "skin.stock_malformed": "skins",
    "fonts.missing": "skins",
    "storage.low": "cards",
    "artwork.coverage": "cards",
    "artwork.unavailable": "cards",
    "decks.unreadable": "mydecks",
    "decks.unknown_cards": "mydecks",
    "banlists.missing": "mydecks",
    "banlists.unreadable": "mydecks",
}


def route(item: Finding) -> str | None:
    if item.id == "state.interrupted":
        return {
            "sleeves": "sleeves",
            "effects": "sounds",
            "music": "music",
            "backup": "backup",
            "skins": "skins",
        }.get(str(item.evidence.get("category")))
    if item.id == "artwork.coverage" and all(
        isinstance(have := item.evidence.get(f"{kind}_have"), int)
        and have == item.evidence.get(f"{kind}_total")
        for kind in ("cards", "fields")
    ):
        return None
    return ROUTES.get(item.id)


def heading(item: Finding) -> str:
    if item.id == "decks.unknown_cards":
        return deck_heading(item, "uses unknown cards")
    if item.id == "decks.unreadable":
        return deck_heading(item, "could not be read")
    if item.id == "state.interrupted" and (page := route(item)):
        return f"Interrupted change in {PAGE_NAMES[page]}"
    if item.id == "artwork.coverage":
        have, total = item.evidence.get("cards_have"), item.evidence.get("cards_total")
        if isinstance(have, int) and isinstance(total, int) and total > have:
            return f"{total - have:,} cards are missing artwork"
    return {
        "windbot.list_conflicts": "Some practice decks exceed installed card limits",
        "repositories.config": "Game update sources checked",
        "state.lock_idle": "No update is using this file",
        "process.closed": "EDOPro appears to be closed",
    }.get(item.id, item.title.replace("Studio", config.APP_NAME))


def deck_heading(item: Finding, issue: str) -> str:
    path = item.evidence.get("path")
    return f"{path.stem} {issue}" if isinstance(path, Path) and path.suffix else item.title


def practice_summary(item: Finding) -> str:
    decks = item.evidence.get("decks", 0)
    lists = item.evidence.get("lists", 0)
    conflicts = item.evidence.get("conflicts", 0)
    return (
        f"{decks:,} bundled practice {'deck' if decks == 1 else 'decks'} exceeded card limits "
        f"on {lists:,} installed {'list' if lists == 1 else 'lists'}. "
        f"The {conflicts:,} matches count each card limit separately for every deck and list. "
        "For practice duels, choose a bot suited to the list you play."
    )


def groups(items: tuple[Finding, ...]) -> list[tuple[str, str, list[Finding]]]:
    grouped: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for item in items:
        area = AREAS.get(item.id.partition(".")[0], "Installation")
        grouped[item.severity, area].append(item)
    return [
        (status, area, grouped[status, area])
        for status, area in sorted(grouped, key=lambda key: (RANK[key[0]], key[1]))
    ]


def byte_size(size: int) -> str:
    amount = float(size)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{size:,} bytes" if unit == "bytes" else f"{amount:,.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _sample(value: object, limit: int | None = 4) -> Iterator[str]:
    if isinstance(value, ListConflict):
        yield f"{value.deck.stem} · {value.list_name} · {value.card_name} ({value.copies} {'copy' if value.copies == 1 else 'copies'}, limit {value.limit})"
    elif isinstance(value, Mapping):
        for key, child in islice(value.items(), limit):
            yield f"{key} — {', '.join(_sample(child, limit))}"
        if limit is not None and len(value) > limit:
            yield f"and {len(value) - limit:,} more"
    elif isinstance(value, (tuple, list, set, frozenset)):
        for child in islice(value, limit):
            yield from _sample(child, limit)
        if limit is not None and len(value) > limit:
            yield f"and {len(value) - limit:,} more"
    elif is_dataclass(value) and not isinstance(value, type):
        yield from _sample({f.name: getattr(value, f.name) for f in fields(value)}, limit)
    elif isinstance(value, bool):
        yield "Yes" if value else "No"
    elif isinstance(value, int):
        yield f"{value:,}"
    else:
        yield str(value) if value is not None else "Unknown"


def evidence(item: Finding) -> Iterator[tuple[str, str]]:
    if item.id == "windbot.list_conflicts":
        return
    for key, value in item.evidence.items():
        name = key.replace("_", " ").capitalize()
        if isinstance(value, Path):
            yield name, str(value)
        else:
            yield name, " · ".join(_sample(value)) or "None"


def evidence_page(value: tuple | list | dict, start: int, count: int) -> str:
    rows = value.items() if isinstance(value, dict) else value
    return "\n\n".join(
        " · ".join(_sample(entry, None)) for entry in islice(rows, start, start + count)
    )


# Some categories combine several directories or include files at the game root.
GAME_FOLDERS = {
    "artwork": "pics",
    "decks": "deck",
    "replays": "replay",
    "audio": "sound",
    "settings": "config",
    "windbot": "WindBot",
    "screenshots": "screenshots",
}
PROFILE_FOLDERS = {"soundtracks": "soundtracks", "backups": "extras-backups", "studio_state": ""}


def category_folder(category: str, game: Path, profile: Path) -> Path:
    if category in PROFILE_FOLDERS:
        return profile / PROFILE_FOLDERS[category]
    return game / GAME_FOLDERS.get(category, "")
