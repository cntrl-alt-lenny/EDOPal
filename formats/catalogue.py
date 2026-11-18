"""Offline catalogue, list identity and EDOPro host instructions."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from config import APP_NAME
from decks.banlists import Banlist, parse_list_text

MAX_LIST = 4 << 20


def assets() -> Path:
    return (
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "formats"
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def marked(fid: int, body: bytes) -> bytes:
    return f"# Companion format list v1 {fid} {sha(body)}\n".encode() + body


def valid_mark(data: bytes, fid: int) -> bool:
    _, sep, body = data.partition(b"\n")
    return bool(sep) and data == marked(fid, body)


def filename(fid: int) -> str:
    if type(fid) is not int or fid <= 0:
        raise ValueError("Invalid format identity.")
    return f"{APP_NAME}-format-{fid}.lflist.conf"


def validate_data(data: bytes, path: Path, row: dict, *, earlier_digest: str = "") -> Banlist:
    if len(data) > MAX_LIST or b"\0" in data:
        raise ValueError(f"The bundled list for {row['name']} failed validation.")
    lists = list(parse_list_text(data.decode("utf-8-sig"), path))
    if (
        sha(data) != (earlier_digest or row["sha256"])
        or not valid_mark(data, row["id"])
        or len(lists) != 1
        or lists[0].issues
        or not lists[0].whitelist
        or (
            not earlier_digest
            and (lists[0].name != row["display"] or len(lists[0].limits) != sum(row["limits"]))
        )
    ):
        raise ValueError(f"The bundled list for {row['name']} failed validation.")
    return lists[0]


def validate(path: Path, row: dict, *, earlier_digest: str = "") -> None:
    with path.open("rb") as stream:
        validate_data(stream.read(MAX_LIST + 1), path, row, earlier_digest=earlier_digest)


@dataclass
class Catalogue:
    directory: Path
    rows: list[dict]
    recipes: dict[int, dict]

    @classmethod
    def load(cls, directory: Path | None = None) -> Catalogue:
        base = directory or assets()
        rows = json.loads((base / "manifest.json").read_bytes())["formats"]
        recipes = {r["id"]: r for r in json.loads((base / "recipes.json").read_bytes())}
        if {r["id"] for r in rows} != recipes.keys() or len(rows) != len(recipes):
            raise ValueError("The bundled catalogue and host settings do not match.")
        return cls(base, rows, recipes)

    def select(self, region: str = "", era: str = "", query: str = "") -> list[dict]:
        words = query.casefold().split()
        return [
            r
            for r in self.rows
            if (not region or region == r["category"])
            and (not era or era == r["era"])
            and all(
                w in f"{r['name']} {r['era']} {r['date'] or ''} {r['banlist']}".casefold()
                for w in words
            )
        ]

    def bundled_list(self, format_id: int) -> Banlist:
        """Read a verified list in memory. Its source path identifies the ZIP member."""
        row = next((r for r in self.rows if r["id"] == format_id), None)
        if row is None:
            raise KeyError(format_id)
        if "file" not in row:
            raise ValueError(row["unsupported"])
        archive_path = self.directory / "lists.zip"
        with ZipFile(archive_path) as archive, archive.open(row["file"]) as member:
            return validate_data(member.read(MAX_LIST + 1), archive_path / row["file"], row)


def host_steps(row: dict, recipe: dict) -> list[str]:
    chosen = row.get("display") or (
        "a Genesys list" if row["name"] == "Genesys" else f"the current {row['name']} list"
    )
    steps = [
        f"Open LAN Mode → Create Host. On Duel, choose {chosen} under Forbidden List and Anything goes under Allowed Cards.",
        f"Choose 1 vs 1, Best of 3, Time limit 180 and Rule {recipe['base']}. Leave Relay off.",
        "Leave Don’t shuffle Deck, Don’t check Deck contents and Don’t check Deck size unchecked.",
        f"Set Starting LP to {recipe['lp']:,}, Starting Hand to {recipe['hand']} and Cards per Draw to {recipe['draw']}. Leave Extra Rules off.",
    ]
    bounds = ", ".join(
        f"{k.title()} {recipe[k][0]}–{recipe[k][1]}" for k in ("main", "extra", "side")
    )
    steps.append(f"In Deck Options, set {bounds}.")
    switches = [("Tick " if s["on"] else "Untick ") + s["label"] for s in recipe["switches"]]
    if switches or recipe["types"]:
        instructions = "\n".join(switches)
        if recipe["types"]:
            instructions += (
                ("\n" if instructions else "") + "Type filters · " + ", ".join(recipe["types"])
            )
        steps.append(f"Under Custom Rule, keep the other preset settings.\n{instructions}")
    steps.append("Return directly to Duel and confirm the room.")
    return [f"{i}. {s}" for i, s in enumerate(steps, 1)]


def copy_settings(row: dict, recipe: dict) -> str:
    title = row.get("display") or f"Current {row['name']} list"
    settings = [
        f"{row['name']} · {recipe['fidelity']} · EDOPro {recipe['client']}",
        f"LAN Mode → Create Host · {title} · Anything goes",
        f"1 vs 1 · Best of 3 · 180 seconds · Relay off · {recipe['base']}",
        f"LP {recipe['lp']} · Hand {recipe['hand']} · Draw {recipe['draw']} · Extra Rules off",
        "Shuffle and both deck checks enabled",
        " / ".join(f"{k.title()} {recipe[k][0]}–{recipe[k][1]}" for k in ("main", "extra", "side")),
    ]
    settings.extend(("On · " if s["on"] else "Off · ") + s["label"] for s in recipe["switches"])
    if recipe["types"]:
        settings.append("Type filters · " + ", ".join(recipe["types"]))
    return "\n".join([*settings, "Return directly to Duel to confirm.", *recipe["caveats"]])
