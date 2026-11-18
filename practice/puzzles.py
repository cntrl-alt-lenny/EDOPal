"""Read literal puzzle declarations. No Lua interpreter or game process is involved."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from decks.sources import bounded_text
from doctor.game_data import local_path


@dataclass(frozen=True)
class Puzzle:
    path: str
    digest: str = ""
    objective: str = ""
    difficulty: str = ""
    life: tuple[str, str] = ("", "")
    hints: tuple[str, ...] = ()
    solution: str = ""
    error: str = ""

    @property
    def title(self) -> str:
        return Path(self.path).stem.replace("_", " ")

    @property
    def collection(self) -> str:
        return "/".join(self.path.split("/")[:-1]) or "Loose puzzles"


_LONG = re.compile(r"\[(=*)\[")
_TOKEN = re.compile(r"""--|\[=*\[|["']""")
_FIELD = re.compile(
    r"(?i)^\s*(objective|goal|difficulty|complexity|your starting lp|opponent's starting lp|"
    r"hints?|solution(?: here| \(video\))?)\s*:\s*(.*)$"
)
_LP = re.compile(
    r"\b(?:Debug\s*\.\s*SetPlayerInfo|Duel\s*\.\s*SetLP)\s*\(\s*([01])\s*,\s*(\d+)\s*[,)]"
)
_HINT = re.compile(r"\bDebug\s*\.\s*ShowHint\s*\(\s*$")


def _literals(text: str) -> tuple[str, list[str], list[str]]:
    # Mask all literals before looking for calls. A quoted example or commented-out
    # setup must never supply an objective, a hint, or a player's starting LP.
    code: list[str] = []
    comments: list[list[str]] = []
    hints: list[str] = []
    pos = 0
    last_line_comment = False
    while match := _TOKEN.search(text, pos):
        start = match.start()
        code.append(text[pos:start])
        comment = match[0] == "--"
        index = start + 2 if comment else start
        line_comment = False
        if long := _LONG.match(text, index):
            stop = "]" + long[1] + "]"
            end = text.find(stop, long.end())
            value = text[long.end() : end if end >= 0 else len(text)]
            pos = end + len(stop) if end >= 0 else len(text)
        elif comment:
            line_comment = True
            end = text.find("\n", index)
            pos = end if end >= 0 else len(text)
            value = text[index:pos]
        else:
            quote = text[index]
            pos = index + 1
            while pos < len(text) and text[pos] != quote:
                pos += 2 if text[pos] == "\\" else 1
            value = re.sub(
                r"""\\([\\"'nrt])""",
                lambda m: {"n": "\n", "r": "\r", "t": "\t"}.get(m[1], m[1]),
                text[index + 1 : pos],
            )
            pos += 1
        if line_comment and last_line_comment and not code[-1].strip():
            comments[-1].append(value)
        elif comment:
            comments.append([value])
        elif value.strip() and _HINT.search(code[-1]) and re.match(r"\s*\)", text[pos : pos + 100]):
            hints.append(value.strip())
        code.append(" " if comment else " _ ")
        last_line_comment = line_comment
    code.append(text[pos:])
    return "".join(code), ["\n".join(group) for group in comments], hints


def parse_puzzle(path: str, text: str) -> Puzzle:
    code, comments, hints = _literals(text)
    fields: dict[str, str] = {}
    solutions: list[str] = []
    for comment in comments:
        section = ""
        for line in comment.splitlines():
            stripped = line.strip()
            if section == "solution":
                if any(ch.isalnum() for ch in stripped):
                    solutions.append(stripped)
                continue
            field = _FIELD.fullmatch(stripped)
            if field:
                section, value = field[1].lower(), field[2].strip()
                if section.startswith("solution"):
                    section = "solution"
                if section not in {"hint", "hints", "solution"}:
                    fields.setdefault(section, value)
                    section = ""
                    continue
            elif re.fullmatch(
                r"(?:solutions?|the answer(?: for puzzle \d+)?)\s*:?", stripped, re.I
            ):
                section = "solution"
                continue
            elif stripped.lower() in {"hints", "hint"}:
                section = stripped.lower()
                continue
            else:
                value = stripped
            if value and any(ch.isalnum() for ch in value):
                if section == "solution":
                    solutions.append(value)
                elif section in {"hint", "hints"}:
                    hints.append(value)
    life = [fields.get("your starting lp", ""), fields.get("opponent's starting lp", "")]
    setup = re.split(r"\baux\s*\.\s*BeginPuzzle\s*\(", code, maxsplit=1)[0]
    for side, lp in _LP.findall(setup):
        life[int(side)] = lp
    difficulty = fields.get("difficulty") or fields.get("complexity", "")
    return Puzzle(
        path,
        hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest(),
        fields.get("objective") or fields.get("goal", ""),
        difficulty if "?" not in difficulty else "",
        (life[0], life[1]),
        tuple(dict.fromkeys(hints)),
        "\n".join(solutions),
    )


def read_puzzles(game: Path, cancel: Event) -> tuple[list[Puzzle], str]:
    result: list[Puzzle] = []
    issues: list[Exception] = []
    try:
        root = local_path(game, game / "puzzles")
    except (OSError, ValueError, RuntimeError):
        return [], "The puzzles folder could not be read inside this installation."
    if not root.is_dir():
        return [], "This installation has no puzzles folder."
    for folder, dirs, files in os.walk(root, onerror=issues.append):
        base = Path(folder)
        if cancel.is_set():
            raise InterruptedError("Scan cancelled.")
        children = []
        for name in dirs:
            child = base / name
            try:
                if not name.startswith(".") and child.resolve() == child:
                    children.append(name)
            except (OSError, RuntimeError) as exc:
                issues.append(exc)
        dirs[:] = children
        for name in files:
            if cancel.is_set():
                raise InterruptedError("Scan cancelled.")
            if not name.casefold().endswith(".lua"):
                continue
            file = base / name
            path = file.relative_to(root).as_posix()
            try:
                result.append(parse_puzzle(path, bounded_text(local_path(root, file), 1 << 20)))
            except (ValueError, OSError, RuntimeError):
                result.append(Puzzle(path, error="The puzzle file could not be read."))
    result.sort(
        key=lambda p: (
            "tutorials" not in p.collection.lower().split("/"),
            p.collection.casefold(),
            p.title.casefold(),
        )
    )
    return result, "Some puzzle folders could not be read." if issues else ""
