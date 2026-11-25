"""Read one EDOPro list without losing duplicate rows used in its room hash."""

from __future__ import annotations

from dataclasses import dataclass

from edopal.core.install import Install

from .builder.pool import list_hash

MAX_LIST = 4 << 20
STOCK_LISTS = {
    "advanced": "repositories/lflists/0TCG.lflist.conf",
    "traditional": "repositories/lflists/Traditional.lflist.conf",
}


@dataclass(frozen=True)
class ListSection:
    name: str
    whitelist: bool
    limits: dict[int, int]
    room_hash: str


def parse_list(data: bytes) -> ListSection:
    name = ""
    whitelist = False
    rows = []
    for line in data.decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--")):
            continue
        if line.startswith("!"):
            if name or not line[1:].strip():
                raise ValueError("Expected one named EDOPro banlist.")
            name = line[1:].strip()
        elif name and line == "$whitelist":
            whitelist = True
        else:
            fields = line.split(maxsplit=2)
            if (
                not name
                or len(fields) < 2
                or not fields[0].isascii()
                or not fields[0].isdigit()
                or fields[1] not in {"0", "1", "2", "3"}
            ):
                raise ValueError("The banlist contains an invalid entry.")
            code, limit = int(fields[0]), int(fields[1])
            if not 0 < code <= 0xFFFFFFFF or not 0 <= limit <= 3:
                raise ValueError("The banlist contains an invalid passcode or copy limit.")
            rows.append((code, limit))
    if not name or not rows:
        raise ValueError("The banlist is incomplete.")
    return ListSection(name, whitelist, dict(rows), list_hash(rows))


def read_stock(install: Install | None, profile: dict) -> ListSection:
    rel = STOCK_LISTS.get(profile["id"])
    if rel is None or rel != profile.get("stock_lflist"):
        raise ValueError("Unrecognized stock EDOPro list.")
    if install is None:
        raise ValueError("Choose an EDOPro installation to use its stock banlist.")
    root = install.root.resolve()
    path = root / rel
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f"EDOPro’s stock list is unavailable. Update game data to restore {rel}.")
    with path.open("rb") as stream:
        data = stream.read(MAX_LIST + 1)
    if len(data) > MAX_LIST:
        raise ValueError("The installed EDOPro banlist is too large.")
    section = parse_list(data)
    if section.whitelist:
        raise ValueError(f"The installed {profile['name']} list is not an ordinary stock banlist.")
    return section
