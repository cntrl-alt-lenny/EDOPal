"""Replay display metadata and rolling-copy detection, independent of Qt."""

import csv
from pathlib import Path

from replays import ReplayEntry, ReplayNotes, find_last_replays
from replays.format import HAND_TEST, SINGLE
from replays.shelf import ReplaySort

SORTS: dict[str, ReplaySort] = {"Newest first": "date", "Name": "name", "Most turns": "turns"}

# Exact presets in Project Ignis script/constant.lua. Modified presets have no reliable name.
_RULES = {
    0x620000: "Speed Duel",
    0x807F20200: "Rush Duel",
    0x7F80D072C: "GOAT",
    0xD0700: "Master Rule 1",
    0xD0600: "Master Rule 2",
    0xD1800: "Master Rule 3",
    0xD2800: "Master Rule 4",
    0x2E800: "Master Rule 5",
}


def rules(entry: ReplayEntry) -> str:
    if entry.details is None:
        return "Rules unknown"
    return _RULES.get(entry.details.rule_flags, "Rules unspecified")


def players(entry: ReplayEntry) -> str:
    if entry.details:
        teams = [" & ".join(n for n in team if n) for team in entry.details.teams]
        if any(teams):
            return " vs ".join(team for team in teams if team)
    return "Players unknown"


def title(entry: ReplayEntry) -> str:
    if entry.annotation.label:
        return entry.annotation.label
    if entry.is_last:
        return "Last duel"
    if entry.path.stem.startswith("Replay-") and entry.details:
        return players(entry)
    return entry.path.stem


def played(entry: ReplayEntry) -> str:
    date = entry.header.date if entry.header else None
    return date.astimezone().strftime("%d %b %Y · %H:%M") if date else "Date unknown"


def details_line(entry: ReplayEntry) -> str:
    if entry.error or not entry.details:
        return "Could not read this replay"
    header = entry.details.header
    parts = [rules(entry)]
    if header.flags & HAND_TEST:
        parts.append("Hand test")
    elif header.flags & SINGLE:
        parts.append("Solo duel")
    elif any(len(team) > 1 for team in entry.details.teams):
        parts.append("Team duel")
    turns = entry.details.turns
    parts.append(f"{turns} turns" if turns is not None else "Turns unknown")
    return "  ·  ".join(parts)


def last_to_save(entries: tuple[ReplayEntry, ...]) -> ReplayEntry | None:
    archived = {e.replay_id for e in entries if not e.is_last and e.replay_id}
    pending = [
        entry
        for entry in entries
        if entry.is_last and entry.details and entry.replay_id and entry.replay_id not in archived
    ]
    return max(
        pending,
        key=lambda e: (
            e.header.timestamp if e.header else 0,
            e.path.suffix.casefold(),
        ),
        default=None,
    )


def folder_stamp(game: Path) -> tuple[int, ...]:
    """Directory changes catch renames and new files; rolling files also change in place."""
    rolling = find_last_replays(game)
    try:
        changed = (game / "replay").stat().st_mtime_ns
    except FileNotFoundError:
        return ()
    return (changed, *(p.stat().st_mtime_ns for p in rolling))


def format_tags(tags: tuple[str, ...]) -> str:
    return ", ".join(
        '"' + tag.replace('"', '""') + '"' if "," in tag or '"' in tag else tag for tag in tags
    )


def notes_from_fields(label: str, notes: str, tags: str) -> ReplayNotes:
    tags = tags.replace("\r\n", ",").replace("\r", ",").replace("\n", ",")
    values = next(csv.reader([tags], skipinitialspace=True))
    return ReplayNotes(
        label, notes, tuple(dict.fromkeys(tag.strip() for tag in values if tag.strip()))
    )


def watch_instructions(entry: ReplayEntry) -> str:
    steps = "Open this installation of EDOPro. From the main menu choose Replays, select the filename below, then choose Load Replay."
    if entry.header and entry.header.format == "yrpX":
        return steps + " You can also drag the file onto the game’s main menu."
    if entry.header and entry.header.format == "yrp1":
        return (
            steps
            + " Older recordings may need the same game core and card scripts used for the duel."
        )
    return steps
