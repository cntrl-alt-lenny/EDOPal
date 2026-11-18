"""Installed list sections, each identified by source file and header line."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .cards import passcode
from .sources import bounded_text


@dataclass(frozen=True)
class Banlist:
    name: str
    path: Path
    line: int
    whitelist: bool
    limits: dict[int, int]
    issues: tuple[str, ...]


def parse_lists(path: Path) -> Iterator[Banlist]:
    yield from parse_list_text(bounded_text(path, 4 << 20), path)


def parse_list_text(text: str, path: Path) -> Iterator[Banlist]:
    """Parse list sections, retaining the source identity even for an archive member."""
    name = ""
    header = 0
    whitelist = False
    limits: dict[int, int] = {}
    issues: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("!"):
            if header:
                yield Banlist(name, path, header, whitelist, limits, tuple(issues))
            name, header, whitelist, limits, issues = line[1:].strip(), number, False, {}, []
            if not name:
                issues.append(f"Unnamed list at line {number}")
        elif line == "$whitelist":
            whitelist = True
        elif header and line and not line.startswith("#"):
            fields = line.split(maxsplit=2)
            if (
                len(fields) >= 2
                and fields[0].isascii()
                and fields[0].isdigit()
                and len(fields[0]) <= 10
                and passcode(int(fields[0]))
                and fields[1] in {"-1", "0", "1", "2", "3"}
            ):
                limits[int(fields[0])] = int(fields[1])
            else:
                issues.append(f"Unrecognized list entry on line {number}")
    if not header:
        raise ValueError("Missing banlist header")
    yield Banlist(name, path, header, whitelist, limits, tuple(issues))
