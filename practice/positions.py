"""Conservative summaries of literal starting positions in EDOPro puzzles."""

from __future__ import annotations

import re

_CARD = re.compile(
    r"(?:(?:local\s+)?\w+\s*=\s*)?Debug\.AddCard\(\s*\d+\s*,\s*[01]\s*,\s*([01])\s*,"
    r"\s*LOCATION_(HAND|MZONE|SZONE|FZONE|PZONE|DECK|EXTRA|GRAVE|REMOVED)\s*,"
    r"\s*(\d+)\s*,\s*POS_\w+(?:\s*,\s*(?:true|false))?\s*\)"
)
_MAXIMUM = re.compile(r"Debug\.AddMaximumCard\(\s*([01])(?:\s*,\s*\d+){3}\s*\)")
_SETUP = re.compile(
    r"(?:Debug\.(?:ReloadFieldBegin|ReloadFieldEnd|SetPlayerInfo|SetAIName|ShowHint|PreEquip|PreSummon|PreAddCounter)"
    r"|Duel\.SetLP)\([\w\s,+|]*\)"
)
_BEGIN = "aux.BeginPuzzle()"


def setup_summary(code: str) -> str:
    # A small allowlist of complete statements keeps loops, variable locations,
    # custom effects and snippets inside a function from becoming false totals.
    lines = [s.strip().removesuffix(";").strip() for s in code.splitlines() if s.strip()]
    turn = _BEGIN in lines
    hand = 0
    zones: set[tuple[str, int]] = set()
    reading = False
    plain_position = True
    cards = False
    for line in lines:
        if match := _CARD.fullmatch(line):
            cards = True
            plain_position &= reading
            if match[1] == "0":
                hand += match[2] == "HAND"
                if match[2] in {"MZONE", "SZONE", "PZONE", "FZONE"}:
                    slot = (match[2], int(match[3]))
                    # Reusing a monster slot can attach material to its occupant.
                    plain_position &= slot not in zones
                    zones.add(slot)
        elif maximum := _MAXIMUM.fullmatch(line):
            cards = True
            plain_position &= reading
            if maximum[1] == "0":
                slots = {("MZONE", i) for i in (1, 2, 3)}
                plain_position &= zones.isdisjoint(slots)
                zones.update(slots)
        elif line != _BEGIN and not _SETUP.fullmatch(line):
            return ""
        elif line.startswith("Debug.ReloadFieldBegin("):
            reading = True
        elif line == "Debug.ReloadFieldEnd()":
            reading = False
    parts = ["Win this turn"] if turn else []
    if (
        cards
        and plain_position
        and code.count("Debug.ReloadFieldBegin(") == code.count("Debug.ReloadFieldEnd(") == 1
    ):
        parts.append(f"The setup places {hand} in your hand and {len(zones)} on your field")
    return " · ".join(parts)


def written_goal(comments: list[str], messages: list[str]) -> str:
    # Promote only the goal clause, never the technique from a tutorial hint.
    for line in messages:
        if explicit := re.match(r"(?:Goal|Objective):\s*([^\n]+)", line, re.I):
            goal = re.split(r"\s+(?:hint|solution):", explicit[1], flags=re.I)[0].strip()
            if goal:
                return goal
        if re.search(r"\b(?:can't|cannot|never|not|if|unless)\b", line, re.I):
            continue
        if re.fullmatch(r"(?:Use [^.!?\n]+ to )?win this turn[.!]?", line, re.I):
            return "Win this turn."
        if re.fullmatch(
            r"(?:(?:Summon|Experiment with) [^.!?\n]+ and )?defeat your opponent[.!]?", line, re.I
        ):
            return "Defeat your opponent."
    for comment in comments:
        lines = comment.strip().splitlines()
        if not lines or lines[0].strip().lower() != "message":
            continue
        for line in lines[1:]:
            line = line.strip()
            if (
                not line
                or ":" in line
                or re.search(r"\b(hints?|solutions?|answers?)\b", line, re.I)
            ):
                break
            if re.search(r"\b(?:can't|cannot|never|not|unless)\b", line, re.I):
                continue
            if re.fullmatch(r"Use [^.!?]+ to skip your opponent['’]s turn[.!]?", line, re.I):
                return "Skip your opponent’s turn."
            if re.search(
                r"(?:^|[.!?]\s+)(?:Use [^.!?]+ to win|Start [^.!?]+ and combo your way to victory"
                r"|[Hh]armoni[sz]e [^.!?]+ and beat your opponent)[.!]?$",
                line,
                re.I,
            ):
                return "Defeat your opponent."
            if re.fullmatch(r"Help .+ obliterate the opponent's field[.!]?", line, re.I):
                return "Clear the opponent’s field."
            if re.fullmatch(r"(?:Win this turn|Defeat your opponent)[.!]?", line, re.I):
                return line
    return ""
