"""A session receipt for undoing only files a library operation actually added."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from music.audio import _digest
from state_lock import locked


@dataclass
class Receipt:
    root: Path
    title: str
    files: dict[Path, str] = field(default_factory=dict)

    def record(self, path: Path) -> None:
        self.files[path] = _digest(path)

    def undo(self, checkpoint: Callable[[], None] | None = None) -> None:
        root = self.root.resolve(strict=True)
        with locked(root, ".import.lock"):
            for path, digest in self.files.items():
                if checkpoint:
                    checkpoint()
                if (
                    not path.is_absolute()
                    or path.resolve() != path
                    or not path.is_relative_to(root)
                    or not path.is_file()
                    or _digest(path) != digest
                ):
                    raise ValueError(
                        f"Kept {path.name}. It has moved or changed since it was added."
                    )
                if path.name == "game.json" and any(
                    file.is_file() and file not in self.files and file.name != ".music.lock"
                    for file in path.parent.rglob("*")
                ):
                    raise ValueError("This album now contains other files. Kept its library entry.")
            for path in sorted(self.files, key=lambda f: f.name == "game.json"):
                if checkpoint:
                    checkpoint()
                if path.name == "game.json":
                    # A metadata-less personal directory is still a recoverable game to the engine.
                    retired = root / (".removed-game-" + uuid4().hex)
                    path.parent.rename(retired)
                    manifest = retired / path.name
                    if _digest(manifest) != self.files[path]:
                        retired.rename(path.parent)
                        raise ValueError("The album name changed during undo. Kept its entry.")
                    manifest.unlink()
                    # Never recursively delete a directory another process could still be using.
                    if not any(retired.iterdir()):
                        retired.rmdir()
                else:
                    with locked(path.parent):
                        if _digest(path) != self.files[path]:
                            raise ValueError(f"Kept {path.name}. It changed during undo.")
                        path.unlink()
                del self.files[path]
