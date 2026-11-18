"""Player-owned annotations keyed by the replay bytes, independent of filenames."""

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from config import user_data_dir


@dataclass(frozen=True)
class ReplayNotes:
    label: str = ""
    notes: str = ""
    tags: tuple[str, ...] = ()


class AnnotationStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.path = (
            Path(data_dir if data_dir is not None else user_data_dir()).resolve()
            / "replay-notes.sqlite3"
        )

    def all(self) -> dict[str, ReplayNotes]:
        """Read annotations without creating an empty database."""
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        except sqlite3.OperationalError:
            if not self.path.exists():
                return {}
            raise
        with closing(db):
            if (
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'replay_notes'"
                ).fetchone()
                is None
            ):
                return {}
            rows = db.execute("SELECT replay_id, label, notes, tags FROM replay_notes")
            return {
                replay_id: ReplayNotes(label, notes, tuple(tags.split("\n")) if tags else ())
                for replay_id, label, notes, tags in rows
            }

    def get(self, replay_id: str) -> ReplayNotes:
        return self.all().get(replay_id, ReplayNotes())

    def set(self, replay_id: str, annotation: ReplayNotes) -> None:
        """Replace one annotation, or clear it with an empty ReplayNotes."""
        if not re.fullmatch(r"[a-f0-9]{64}", replay_id):
            raise ValueError("A replay ID must be a SHA-256 digest.")
        tags = tuple(dict.fromkeys(tag.strip() for tag in annotation.tags if tag.strip()))
        if any("\n" in tag or "\r" in tag for tag in tags):
            raise ValueError("A replay tag must fit on one line.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS replay_notes ("
                "replay_id TEXT PRIMARY KEY, label TEXT NOT NULL, notes TEXT NOT NULL, tags TEXT NOT NULL)"
            )
            if annotation.label or annotation.notes or tags:
                db.execute(
                    "INSERT OR REPLACE INTO replay_notes VALUES (?, ?, ?, ?)",
                    (replay_id, annotation.label, annotation.notes, "\n".join(tags)),
                )
            else:
                db.execute("DELETE FROM replay_notes WHERE replay_id = ?", (replay_id,))
