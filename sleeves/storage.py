"""Small atomic catalogue and immutable content objects. One lock per collection."""

import hashlib
import json
import math
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zipfile import ZipFile

import extras
from state_lock import locked

from .catalog import Recipe
from .imaging import Layout, decode, read_source

Source = Literal["preset", "designed", "imported", "card art", "recovered"]
_HASH = re.compile("[a-f0-9]{64}")
_ID = re.compile("[a-f0-9]{32}")
_guard = threading.RLock()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    source: Source
    image: str
    original: str | None = None
    recipe: Recipe | None = None
    layout: Layout | None = None
    passcode: int | None = None
    tags: tuple[str, ...] = ()
    favourite: bool = False
    last_used: float = 0


def _entry(row: dict) -> Entry:
    e = Entry(**row)
    if not (
        _ID.fullmatch(e.id)
        and isinstance(e.name, str)
        and e.name.strip()
        and e.source in ("preset", "designed", "imported", "card art", "recovered")
        and _HASH.fullmatch(e.image)
        and (e.original is None or _HASH.fullmatch(e.original))
        and isinstance(e.tags, (tuple, list))
        and all(isinstance(t, str) for t in e.tags)
        and isinstance(e.favourite, bool)
        and math.isfinite(e.last_used)
        and (e.passcode is None or isinstance(e.passcode, int))
        and (e.recipe is None or isinstance(e.recipe, dict))
        and (e.layout is None or isinstance(e.layout, dict))
    ):
        raise ValueError("Damaged sleeve entry.")
    return Entry(
        **{
            **row,
            "tags": tuple(e.tags),
            "recipe": None if e.recipe is None else Recipe.from_dict(row["recipe"]),
            "layout": None if e.layout is None else Layout.from_dict(row["layout"]),
        }
    )


class _NewerCatalogue(RuntimeError):
    pass


def _parse(raw: bytes) -> dict:
    def reject_number(value: str) -> None:
        raise ValueError(f"Invalid JSON number {value}")

    obj = json.loads(raw, parse_constant=reject_number)
    if not isinstance(obj, dict):
        raise ValueError("Damaged catalogue.")
    if isinstance(obj.get("version"), int) and obj["version"] > 1:
        raise _NewerCatalogue("This collection needs a newer version of EDOPro Studio.")
    if obj.get("version") != 1:
        raise ValueError("Unrecognised catalogue.")
    return obj


@dataclass
class Catalog:
    entries: dict[str, Entry] = field(default_factory=dict)
    assignments: dict[str, dict[str, str]] = field(default_factory=dict)
    undo: dict[str, dict[str, str]] = field(default_factory=dict)

    def retain(self, newer: "Catalog") -> None:
        self.entries.update(newer.entries)
        self.assignments.update(newer.assignments)
        self.undo.update(newer.undo)

    def encode(self) -> bytes:
        return json.dumps(
            {"version": 1, **asdict(self)},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def load(cls, obj: dict, *, salvage: bool = False) -> "Catalog":
        state = cls()
        rows = obj.get("entries")
        if not isinstance(rows, dict):
            raise ValueError("Damaged entry list.")
        for key, row in rows.items():
            try:
                e = _entry(row)
                if key != e.id:
                    raise ValueError("Damaged entry ID.")
                state.entries[key] = e
            except (TypeError, ValueError, OverflowError):
                if not salvage:
                    raise
        for attr in ("assignments", "undo"):
            mapping = obj.get(attr)
            if not isinstance(mapping, dict):
                if salvage:
                    continue
                raise ValueError("Damaged installation records.")
            for install, slots in mapping.items():
                if not isinstance(slots, dict) or any(
                    slot not in ("mine", "opponent")
                    or not isinstance(ref, str)
                    or ref not in state.entries
                    for slot, ref in slots.items()
                ):
                    if salvage:
                        continue
                    raise ValueError("Damaged installation assignment.")
                getattr(state, attr)[install] = slots
        return state


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = root / "manifest.json"
        self.backup = root / "manifest.backup.json"

    def blob(self, key: str, *, original: bool = False) -> Path:
        if not _HASH.fullmatch(key):
            raise ValueError("Invalid sleeve file reference.")
        return (
            self.root
            / ("originals" if original else "images")
            / (key if original else key + ".png")
        )

    def put(self, data: bytes, *, original: bool = False) -> str:
        key = digest(data)
        path = self.blob(key, original=original)
        previous = extras._signature(path)
        if previous != key:
            if previous is not None:
                # Keep user-edited bytes even when restoring a known immutable object.
                path.replace(path.with_name(f"{path.name}.damaged-{previous}"))
            extras._atomic_write(path, data)
        return key

    def save(self, state: Catalog) -> None:
        raw = state.encode()
        extras._atomic_write(self.manifest, raw)
        extras._atomic_write(self.backup, raw)

    def _read(self, *, writable: bool) -> Catalog:
        salvage = Catalog()
        for path in (self.manifest, self.backup):
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            try:
                obj = _parse(raw)
                state = Catalog.load(obj)
                if path == self.backup:
                    state.retain(salvage)
                    self._recover_images(state)
                    self.save(state)
                return state
            except _NewerCatalogue:
                if writable:
                    raise
                # A newer app owns this schema. Its immutable PNGs can still be browsed.
                self._recover_images(salvage)
                return salvage
            except (TypeError, ValueError, OverflowError, RecursionError):
                # Preserve the exact damaged bytes before publishing a repaired index.
                extras._atomic_write(self.root / f"damaged-{digest(raw)}.json", raw)
                try:
                    partial = Catalog.load(_parse(raw), salvage=True)
                    partial.retain(salvage)
                    salvage = partial
                except (TypeError, ValueError, OverflowError, RecursionError):
                    pass
        self._presets(salvage)
        self._recover_images(salvage)
        self.save(salvage)
        return salvage

    def _presets(self, state: Catalog) -> None:
        with ZipFile(extras.PACKS / "shared.zip") as archive:
            for name, member in extras.SLEEVES.items():
                key = self.put(archive.read(member))
                uid = digest(name.encode())[:32]
                state.entries.setdefault(uid, Entry(uid, name, "preset", key))

    def _recover_images(self, state: Catalog) -> None:
        known = {e.image for e in state.entries.values()}
        for path in sorted((self.root / "images").glob("*.png")):
            if path.stem in known or not _HASH.fullmatch(path.stem):
                continue
            try:
                data = read_source(path)
                if digest(data) != path.stem:
                    continue
                decode(data)
            except (OSError, ValueError):
                continue
            uid = path.stem[:32]
            if uid in state.entries:
                uid = uuid4().hex
            state.entries[uid] = Entry(uid, f"Recovered {path.stem[:8]}", "recovered", path.stem)

    @contextmanager
    def open(self, *, writable: bool = False) -> Iterator[Catalog]:
        with _guard, locked(self.root, ".library.lock"):
            yield self._read(writable=writable)

    @contextmanager
    def transaction(self) -> Iterator[Catalog]:
        with self.open(writable=True) as state:
            before = state.encode()
            yield state
            if state.encode() != before:
                self.save(state)
