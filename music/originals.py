"""Validate the playlist's original-file inventory before moving or retiring audio."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from music.audio import FORMATS, _audio, _digest
from music.catalogue import atomic_json
from music.discovery import _folders, _state, backup_folder, read_json, validate_records
from music.storage import check_volume


@dataclass(frozen=True)
class Original:
    folder: str
    name: str
    size: int
    digest: str

    @classmethod
    def capture(cls, folder: Path, path: Path) -> Original:
        if path.is_symlink() or not path.is_file():
            raise OSError(f"Expected a regular original recording at {path}.")
        return cls(folder.name, path.name, path.stat().st_size, _digest(path))

    def verify(self, path: Path) -> None:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != self.size
            or _digest(path) != self.digest
        ):
            raise OSError(
                f"The original recording {self.folder}/{self.name} is missing or changed."
            )


class Originals:
    def __init__(self, game: Path, scene: str) -> None:
        self.folders = {p.name: p for p in _folders(game, scene)}
        self.backups = {
            key: backup_folder(game, scene, folder) for key, folder in self.folders.items()
        }
        self.state = _state(game, scene)
        check_volume(self.state, [*self.folders.values(), *self.backups.values()])
        self.expected: dict[Path, Original] = {}

    def load(self, data: dict) -> None:
        rows = data.get("originals")
        if not isinstance(rows, list):
            raise ValueError(
                f"This playlist has no complete original-file inventory. "
                f"Recovery files are in {self.state}."
            )
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid original recording record.")
            folder, name, size, digest = (row.get(k) for k in ("folder", "name", "size", "digest"))
            if (
                not isinstance(folder, str)
                or folder not in self.folders
                or not isinstance(name, str)
                or Path(name).name != name
                or "\0" in name
                or (os.name == "nt" and ":" in name)
                or Path(name).suffix.lower() not in FORMATS
                or type(size) is not int
                or size < 0
                or not isinstance(digest, str)
                or not re.fullmatch("[a-f0-9]{64}", digest)
            ):
                raise ValueError("Invalid original recording record.")
            original = Original(folder, name, size, digest)
            live = self.folders[folder] / name
            if live in seen or self.expected.setdefault(live, original) != original:
                raise ValueError(f"Conflicting original recording records for {name}.")
            seen.add(live)

    def journal(self) -> list[dict]:
        return [asdict(row) for row in self.expected.values()]

    def collect_parked(self) -> None:
        # Extras can legitimately add a new bundled track to the parked duel playlist.
        for key, backup in self.backups.items():
            for parked in _audio(backup):
                live = self.folders[key] / parked.name
                if live not in self.expected:
                    self.expected[live] = Original.capture(self.folders[key], parked)

    def add_live(self, path: Path, digest: str) -> None:
        if path in self.expected:
            raise FileExistsError(f"An original recording already has the name {path.name}.")
        if path.is_symlink():
            raise OSError(f"Cannot move linked track {path.name}.")
        self.expected[path] = Original(path.parent.name, path.name, path.stat().st_size, digest)

    def parked(self, live: Path) -> Path:
        return self.backups[live.parent.name] / live.name

    def verify_parked(self) -> None:
        for live, original in self.expected.items():
            original.verify(self.parked(live))

    def restore_sources(self, main: Path, managed: list[dict]) -> list[tuple[Path, Path]]:
        removable = {(main / r["name"], r["digest"]) for r in managed}
        moves = []
        for live, original in self.expected.items():
            parked = self.parked(live)
            if parked.exists() or parked.is_symlink():
                original.verify(parked)
                if live.is_symlink() or (live.exists() and (live, _digest(live)) not in removable):
                    raise FileExistsError(f"Another recording already exists at {live}.")
                moves.append((parked, live))
            else:
                # After a process exit an original may already have reached its destination.
                original.verify(live)
        return moves


def _checksum(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def write_journal(path: Path, data: dict) -> None:
    atomic_json(path, {**data, "checksum": _checksum(data)})


def read_journal(path: Path) -> dict:
    try:
        data = read_json(path)
        digest = data.pop("checksum", None)
        if digest is None and "originals" not in data:
            raise ValueError(
                "This older or incomplete record has no verified original-file inventory."
            )
        if digest != _checksum(data):
            raise ValueError("The recovery record has changed.")
    except ValueError as exc:
        raise ValueError(
            f"Cannot verify {path.name}. Recovery files are in {path.parent}. {exc}"
        ) from exc
    return data


def read_selection(state: Path, filename: str, originals: Originals) -> tuple[dict, list[dict]]:
    data = read_journal(state / filename)
    originals.load(data)
    if "previous" in data:
        for row in validate_records(data["previous"]):
            undo = state / "undo" / row["name"]
            if not undo.is_file() or _digest(undo) != row["digest"]:
                raise OSError(f"The undo copy {row['name']} is missing or changed.")
    rows = validate_records(data.get("tracks"))
    if not rows and data.get("cleared") is not True:
        raise ValueError("Empty soundtrack selection.")
    return data, rows
