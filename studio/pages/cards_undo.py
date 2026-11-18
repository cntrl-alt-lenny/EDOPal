"""One restore point per game. Record only files an artwork worker is about to publish."""

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

import config
import extras
from artwork_flow import artwork_change_sink
from state_lock import appearance_lock, locked

_CARD = re.compile(r"pics/(?:field/)?-?[0-9]+\.(?:jpg|png)\Z")


def _history(cfg: config.Config) -> Path:
    key = hashlib.sha256(
        os.fsencode(os.path.normcase(str(Path(cfg.edopro_path).resolve())))
    ).hexdigest()
    return Path(cfg.config_path).resolve().parent / "artwork-restore" / (key + ".sqlite")


def has_restore(cfg: config.Config) -> bool:
    db = _history(cfg)
    if not db.is_file() or db.resolve() != db:
        return False
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        return conn.execute("SELECT 1 FROM changes LIMIT 1").fetchone() is not None


def _target(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("The saved artwork path is invalid.")
    if not _CARD.fullmatch(name) and not (
        name.startswith("textures/") and (extras.ASSETS / relative).is_file()
    ):
        raise ValueError("The saved file does not belong to artwork sync.")
    return extras._regular_path(root, relative)


def _digest(data: bytes | bytearray | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


@contextmanager
def remember_changes(cfg: config.Config) -> Iterator[None]:
    """Flush original bytes before replacement, including on partial/cancelled runs."""
    conn = None
    failure = ""
    cfg.artwork_changed = False
    with ExitStack() as stack:

        def remember(path: str | Path, incoming: bytes | bytearray | None) -> None:
            nonlocal conn, failure
            if failure:
                raise OSError(failure)
            try:
                # Folder recovery can select a different game before the first write.
                root = Path(cfg.edopro_path)
                name = Path(path).relative_to(root).as_posix()
                dest = _target(root.resolve(), name)
                if conn is None:
                    db = _history(cfg)
                    if db.resolve() != db:
                        raise OSError("The artwork restore folder links to another location.")
                    lockfile = extras._regular_path(root.resolve(), ".edopro-artwork.lock")
                    stack.enter_context(locked(lockfile.parent, lockfile.name))
                    db.parent.mkdir(parents=True, exist_ok=True)
                    conn = stack.enter_context(closing(sqlite3.connect(db)))
                    conn.execute("PRAGMA auto_vacuum=FULL")
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS changes(name TEXT PRIMARY KEY, original BLOB, before TEXT, after TEXT)"
                    )
                old = dest.read_bytes() if dest.is_file() else None
                if old == incoming:
                    return
                with conn:
                    if not cfg.artwork_changed:
                        conn.execute("DELETE FROM changes")
                    conn.execute(
                        "INSERT INTO changes VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET after=excluded.after",
                        (name, old, _digest(old), _digest(incoming)),
                    )
                cfg.artwork_changed = True
            except (OSError, sqlite3.Error, ValueError) as exc:
                failure = f"Could not keep a restore copy. Check available space and close any other artwork sync. {exc}"
                raise OSError(failure) from exc

        token = artwork_change_sink.set(remember)
        try:
            yield
            if failure:
                raise OSError(failure)
        finally:
            artwork_change_sink.reset(token)


def restore_artwork(cfg: config.Config) -> int:
    root = Path(cfg.edopro_path).resolve()
    db = _history(cfg)
    if db.resolve() != db or not db.is_file():
        raise ValueError("There is no saved artwork change for this game.")
    lockfile = extras._regular_path(root, ".edopro-artwork.lock")
    with locked(lockfile.parent, lockfile.name), ExitStack() as locks:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute(
                "SELECT 1 FROM changes WHERE name LIKE 'textures/%' LIMIT 1"
            ).fetchone():
                locks.enter_context(appearance_lock(root))

            # Check the whole change before restoring any files. Recheck each write as
            # the player may have edited their game or interrupted a previous restore.
            def entries() -> Iterator[tuple[str, Path, bytes | None, bool]]:
                for name, original, before, after in conn.execute(
                    "SELECT name, original, before, after FROM changes"
                ):
                    dest = _target(root, name)
                    current = extras._signature(dest)
                    if current not in (before, after):
                        raise ValueError(
                            f"{dest.name} has changed since this sync. Your edit has been kept."
                        )
                    if _digest(original) != before:
                        raise ValueError(f"The restore copy of {dest.name} could not be verified.")
                    yield name, dest, original, current != before

            for _ in entries():
                pass
            restored = 0
            ids = set()
            for name, dest, original, needed in entries():
                if name.startswith("pics/") and not name.startswith("pics/field/"):
                    ids.add(dest.stem)
                if needed:
                    if original is None:
                        dest.unlink()
                    else:
                        extras._atomic_write(dest, original)
                    restored += 1
        # A reverted era image must be eligible for the next artwork-policy refresh.
        applied = config._load_config_file(cfg.config_path).get("era_art_applied", {})
        folder = os.path.normcase(os.path.abspath(cfg.edopro_path))
        if isinstance(applied, dict) and isinstance(applied.get(folder), dict):
            policies = applied[folder]
            if ids.intersection(policies):
                applied[folder] = {key: val for key, val in policies.items() if key not in ids}
                if not config.save_setting(cfg.config_path, "era_art_applied", applied):
                    raise OSError("Could not finish saving artwork preferences. Retry the restore.")
            cfg.era_art_applied = applied
        db.unlink()
        return restored
