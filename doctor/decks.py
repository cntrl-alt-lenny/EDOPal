"""Check saved card IDs and installed list restrictions without running the duel engine."""

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .game_data import Card, Repository, files_in, local_path
from .model import Finding, finding


@dataclass(frozen=True)
class Banlist:
    path: Path
    name: str
    whitelist: bool
    limits: dict[int, int]


@dataclass(frozen=True)
class ListConflict:
    deck: Path
    list_path: Path
    list_name: str
    card_id: int
    card_name: str
    copies: int
    limit: int


def _read_lists(path: Path) -> tuple[list[Banlist], tuple[int, ...]]:
    lists: list[Banlist] = []
    bad_lines = []
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", "--")):
            continue
        if line.startswith("!") and line[1:].strip():
            lists.append(Banlist(path, line[1:].strip(), False, {}))
        elif lists and line.startswith("$whitelist"):
            current = lists[-1]
            lists[-1] = Banlist(path, current.name, True, current.limits)
        elif lists and (match := re.match(r"([0-9]{1,10})\s+(-1|[0-4])(?:\s|$)", line)):
            code = int(match[1])
            if 0 < code <= 0xFFFFFFFF:
                # The shipped Rush Prereleases list also uses -1 for a forbidden card.
                lists[-1].limits[code] = min(3, max(0, int(match[2])))
            else:
                bad_lines.append(number)
        else:
            bad_lines.append(number)
    return lists, tuple(bad_lines)


def scan_banlists(root: Path, repos: list[Repository], findings: list[Finding]) -> list[Banlist]:
    lists: list[Banlist] = []
    seen: set[Path] = set()

    def load(path: Path) -> bool:
        try:
            path = local_path(root, path)
            if path in seen:
                return any(item.path == path for item in lists)
            if not path.is_file():
                return False
            seen.add(path)
            parsed, bad_lines = _read_lists(path)
            lists.extend(parsed)
            if not parsed or bad_lines:
                findings.append(finding("banlists.unreadable", path=path, lines=bad_lines))
            return bool(parsed)
        except (OSError, ValueError):
            findings.append(finding("banlists.unreadable", path=path))
            return False

    load(root / "expansions" / "lflist.conf")
    load(root / "lflist.conf")
    for path in files_in(root, root / "lflists", ".conf"):
        load(path)
    for repo in repos:
        if not load(repo.data / "lflist.conf"):
            for path in files_in(root, repo.lists, ".conf"):
                load(path)
    if lists:
        findings.append(
            finding(
                "banlists.available",
                files=len({item.path for item in lists}),
                lists=len(lists),
                whitelists=sum(item.whitelist for item in lists),
                sources=tuple((item.path, item.name, item.whitelist) for item in lists),
            )
        )
    else:
        findings.append(finding("banlists.missing"))
    return lists


def _deck(path: Path) -> tuple[list[int], tuple[int, ...]]:
    ids = []
    bad_lines = []
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if re.fullmatch(r"[0-9]{1,10}", line) and 0 < (code := int(line)) <= 0xFFFFFFFF:
            ids.append(code)
        else:
            bad_lines.append(number)
    return ids, tuple(bad_lines)


def _resolve(code: int, cards: dict[int, Card], mappings: dict[int, int]) -> int:
    return code if code in cards else mappings.get(code, code)


def scan_decks(
    root: Path, cards: dict[int, Card], mappings: dict[int, int], findings: list[Finding]
) -> None:
    folder = root / "deck"
    paths: list[Path] = []
    try:
        local_path(root, folder)
        paths = sorted(
            p
            for p in folder.rglob("*")
            if p.suffix.casefold() == ".ydk" and not p.is_symlink() and p.is_file()
        )
    except (OSError, ValueError):
        findings.append(finding("decks.unreadable", path=folder))
    parsed = 0
    for path in paths:
        try:
            ids, bad_lines = _deck(local_path(root, path))
        except (OSError, ValueError):
            findings.append(finding("decks.unreadable", path=path))
            continue
        if bad_lines or not ids:
            findings.append(finding("decks.unreadable", path=path, lines=bad_lines))
        else:
            parsed += 1
        unknown = tuple(
            sorted({code for code in ids if _resolve(code, cards, mappings) not in cards})
        )
        if cards and unknown:
            findings.append(finding("decks.unknown_cards", path=path, card_ids=unknown))
    findings.append(finding("decks.inventory", files=len(paths), parsed=parsed, path=folder))


def _conflicts(
    path: Path,
    codes: list[int],
    lists: list[Banlist],
    cards: dict[int, Card],
) -> list[ListConflict]:
    results = []
    for item in lists:
        counts: Counter[int] = Counter()
        violations: dict[int, ListConflict] = {}
        for code in codes:
            card = cards[code]
            identity = card.alias or code
            counts[identity] += 1
            limit = item.limits.get(code)
            if limit is None and card.alias and (not item.whitelist or abs(card.alias - code) < 10):
                limit = item.limits.get(card.alias)
            if limit is None and item.whitelist:
                limit = 0
            copies = counts[identity]
            if limit is not None and copies > limit:
                violations[code] = ListConflict(
                    path, item.path, item.name, code, card.name, copies, limit
                )
        results.extend(violations.values())
    return results


def missing_windbot_files(root: Path) -> tuple[str, ...]:
    return tuple(
        name
        for name in ("WindBot.exe", "ExecutorBase.dll")
        if not (root / "WindBot" / name).is_file()
    )


def scan_windbot(
    root: Path,
    cards: dict[int, Card],
    mappings: dict[int, int],
    lists: list[Banlist],
    findings: list[Finding],
) -> None:
    folder = root / "WindBot"
    missing = missing_windbot_files(root)
    if "WindBot.exe" in missing:
        findings.append(finding("windbot.missing", path=folder))
        return
    paths = files_in(root, folder / "Decks", ".ydk")
    names = {path.stem.casefold() for path in paths}
    try:
        catalogue = json.loads(
            local_path(root, folder / "bots.json").read_text(encoding="utf-8-sig")
        )
        if not isinstance(catalogue, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("deck"), str) for row in catalogue
        ):
            raise ValueError("Invalid bot catalogue")
        entries = [row["deck"] for row in catalogue]
    except (OSError, ValueError):
        entries = []
    expected = [name for name in entries if name.casefold() != "lucky"]
    unmatched = tuple(name for name in expected if ("AI_" + name).casefold() not in names)
    details = dict(
        path=folder,
        decks=len(paths),
        catalogue_entries=len(entries),
        matched_entries=len(expected) - len(unmatched),
    )
    complete = bool(paths and entries and not missing)
    findings.append(finding("windbot.available" if complete else "windbot.incomplete", **details))
    if unmatched:
        findings.append(finding("windbot.unmatched", affected=len(unmatched), entries=unmatched))
    conflicts: list[ListConflict] = []
    unverified = []
    for path in paths:
        try:
            ids, bad = _deck(path)
        except (OSError, ValueError):
            unverified.append((path, (), ()))
            continue
        resolved = [_resolve(code, cards, mappings) for code in ids]
        unknown = tuple(sorted({code for code in resolved if code not in cards}))
        if bad or unknown or not ids:
            unverified.append((path, bad, unknown))
        conflicts.extend(
            _conflicts(path, [code for code in resolved if code in cards], lists, cards)
        )
    if unverified:
        findings.append(
            finding("windbot.deck_unreadable", affected=len(unverified), decks=tuple(unverified))
        )
    if conflicts:
        findings.append(
            finding(
                "windbot.list_conflicts",
                decks=len({row.deck for row in conflicts}),
                lists=len({(row.list_path, row.list_name) for row in conflicts}),
                conflicts=len(conflicts),
                details=tuple(conflicts),
            )
        )
