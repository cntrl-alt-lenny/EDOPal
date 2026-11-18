"""One restore point per game. Record only files an artwork worker is about to publish."""

import hashlib
import logging
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, closing, contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

import config
import extras
from artwork_flow import ArtworkStorageError, artwork_change_sink
from state_lock import appearance_lock, locked

_CARD = re.compile(r"pics/(?:field/)?-?[0-9]+\.(?:jpg|png)\Z")


def _history(cfg: config.Config) -> Path:
    key = hashlib.sha256(
        os.fsencode(os.path.normcase(str(Path(cfg.edopro_path).resolve())))
    ).hexdigest()
    return Path(cfg.config_path).resolve().parent / "artwork-restore" / (key + ".sqlite")


def _target(root: Path, name: str) -> Path:
    relative = Path(name)
    if (
        relative.is_absolute()
        or relative.drive
        or ".." in relative.parts
        or ":" in name
        or relative.is_reserved()
    ):
        raise ValueError("The saved artwork path is invalid.")
    bundled = name.startswith(("textures/", "sound/BGM/")) and (extras.ASSETS / relative).is_file()
    texture = relative.parent == Path("textures") and relative.suffix.lower() in (
        ".jpg",
        ".jpeg",
        ".png",
    )
    if not (_CARD.fullmatch(name) or bundled or texture):
        raise ValueError("The saved file does not belong to artwork sync.")
    return extras._regular_path(root, relative)


def _digest(data: bytes | bytearray | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _lock(root: Path) -> AbstractContextManager[None]:
    if not root.is_dir():
        raise OSError("The selected installation folder could not be found.")
    path = extras._regular_path(root, ".edopro-artwork.lock")
    return locked(path.parent, path.name)


def _pending(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending'").fetchone()
        and conn.execute("SELECT 1 FROM pending LIMIT 1").fetchone()
    )


def _reconcile(conn: sqlite3.Connection, root: Path) -> bool:
    """One synchronous publication can be in flight. SQLite promotes it atomically."""
    row = conn.execute("SELECT name,before,after,first,stage FROM pending").fetchone()
    if row is None:
        return False
    name, before, after, first, stage = row
    dest = _target(root, name)
    current = extras._signature(dest)
    if stage:
        if not re.fullmatch(r"[0-9a-f]{32}", stage):
            raise ArtworkStorageError("Unrecognized staging file in the artwork restore data.")
        temp = extras._regular_path(root, dest.with_name(f".{dest.name}.{stage}.part"))
        extras._unlink_stage(temp)
    with conn:
        if current == after:
            if first:
                conn.execute("DELETE FROM changes")
            conn.execute(
                "INSERT INTO changes SELECT name,original,before,after FROM pending WHERE 1 "
                "ON CONFLICT(name) DO UPDATE SET after=excluded.after"
            )
        conn.execute("DELETE FROM pending")
    if current not in (before, after):
        logging.getLogger(__name__).warning(
            "Kept the newer edit to %s during artwork recovery.", name
        )
    return current == after


def has_restore(cfg: config.Config) -> bool:
    db = _history(cfg)
    if not db.is_file() or db.resolve() != db:
        return False
    try:
        if not (getattr(cfg, "dry_run", False) or getattr(cfg, "stats", False)):
            recover_publication(cfg)
    except ArtworkStorageError:
        # An active sync holds the lock. Only previously committed rows are visible here.
        pass
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            return conn.execute("SELECT 1 FROM changes LIMIT 1").fetchone() is not None
    except sqlite3.DatabaseError:
        return False


def recover_publication(cfg: config.Config) -> None:
    db = _history(cfg)
    if not db.is_file():
        return
    try:
        if db.resolve() != db:
            raise OSError("The artwork restore folder links to another location.")
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                if not _pending(conn):
                    return
        except sqlite3.OperationalError:
            # SQLite needs write access to recover its own interrupted transaction.
            pass
        root = Path(cfg.edopro_path).resolve()
        with _lock(root), closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)) as conn:
            if _pending(conn):
                _reconcile(conn, root)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise ArtworkStorageError(str(exc)) from exc


class _Journal:
    def __init__(self, cfg: config.Config, stack: ExitStack) -> None:
        self.cfg = cfg
        self.stack = stack
        self.conn: sqlite3.Connection | None = None
        self.root: Path | None = None
        self.failure: ArtworkStorageError | None = None
        cfg.artwork_changed = False

    def finish(self) -> None:
        if self.conn is not None and self.root is not None:
            try:
                if _reconcile(self.conn, self.root):
                    self.cfg.artwork_changed = True
            except (ValueError, OSError, sqlite3.Error) as exc:
                raise ArtworkStorageError(str(exc)) from exc

    def lock_installation(self) -> None:
        """Acquire the artwork lock before entering any appearance writer."""
        if self.failure:
            raise self.failure
        if getattr(self.cfg, "dry_run", False) or getattr(self.cfg, "stats", False):
            raise ArtworkStorageError("Artwork cannot be changed during a preview.")
        root = Path(self.cfg.edopro_path).resolve()
        if self.root is None:
            self.stack.enter_context(_lock(root))
            self.root = root
        elif self.root != root:
            raise ArtworkStorageError("The installation changed during artwork sync.")

    def prepare(
        self, path: str | Path, data: bytes | bytearray | None, stage: str | None = None
    ) -> Path | None:
        root = Path(self.cfg.edopro_path).resolve()
        location = Path(path)
        selected = Path(self.cfg.edopro_path)
        name = location.relative_to(
            selected if location.is_relative_to(selected) else root
        ).as_posix()
        _target(root, name)
        self.lock_installation()
        if self.conn is None:
            db = _history(self.cfg)
            if db.resolve() != db:
                raise OSError("The artwork restore folder links to another location.")
            db.parent.mkdir(parents=True, exist_ok=True)
            self.conn = self.stack.enter_context(closing(sqlite3.connect(db)))
            self.conn.execute("PRAGMA auto_vacuum=FULL")
            columns = "(name TEXT PRIMARY KEY, original BLOB, before TEXT, after TEXT"
            self.conn.execute("CREATE TABLE IF NOT EXISTS changes" + columns + ")")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS pending" + columns + ", first INTEGER, stage TEXT)"
            )
            # A previous process may have died between the replace and its SQLite commit.
            _reconcile(self.conn, root)
        self.finish()
        dest = _target(root, name)
        old = dest.read_bytes() if dest.is_file() else None
        if old == data:
            return None
        if stage:
            temp = extras._regular_path(root, dest.with_name(f".{dest.name}.{stage}.part"))
            if temp.exists():
                raise FileExistsError(f"Staging file already exists: {temp.name}")
        with self.conn:
            self.conn.execute(
                "INSERT INTO pending VALUES (?,?,?,?,?,?)",
                (name, old, _digest(old), _digest(data), not self.cfg.artwork_changed, stage),
            )
        return dest

    def remember(self, path: str | Path, data: bytes | bytearray | None) -> None:
        # Bundled extras still announce a publication before their own atomic write.
        try:
            self.prepare(path, data)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.failure = ArtworkStorageError(str(exc))
            raise self.failure from exc

    def publish(self, path: str | Path, data: bytes | bytearray | None) -> bool:
        try:
            stage = uuid4().hex if data is not None else None
            dest = self.prepare(path, data, stage)
            if dest is None:
                return False
            if data is None:
                extras._regular_path(self.root, dest).unlink()
            else:
                temp = extras._regular_path(self.root, dest.with_name(f".{dest.name}.{stage}.part"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with temp.open("xb") as handle:
                    handle.write(data)
                mode = dest.stat().st_mode if dest.is_file() else None
                os.replace(temp, extras._regular_path(self.root, dest))
                if mode is not None:
                    dest.chmod(mode)
            self.finish()
            return True
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.failure = ArtworkStorageError(f"{Path(path).name}: {exc}")
            raise self.failure from exc


_active: ContextVar[_Journal | None] = ContextVar("artwork_journal", default=None)


def active_writer() -> _Journal | None:
    return _active.get()


@contextmanager
def remember_changes(cfg: config.Config) -> Iterator[_Journal]:
    existing = active_writer()
    if existing is not None and existing.cfg is cfg:
        yield existing
        return
    with ExitStack() as stack:
        journal = _Journal(cfg, stack)
        token = _active.set(journal)
        observer = artwork_change_sink.set(journal.remember)
        try:
            yield journal
        finally:
            try:
                journal.finish()
            finally:
                artwork_change_sink.reset(observer)
                _active.reset(token)


def restore_artwork(cfg: config.Config, *, skipped: list[str] | None = None) -> int:
    if getattr(cfg, "dry_run", False):
        return 0
    root = Path(cfg.edopro_path).resolve()
    db = _history(cfg)
    if db.resolve() != db or not db.is_file():
        raise ValueError("There is no saved artwork change for this game.")
    with _lock(root), ExitStack() as locks:
        with closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)) as conn:
            if _pending(conn):
                _reconcile(conn, root)
            if not conn.execute("SELECT 1 FROM changes LIMIT 1").fetchone():
                raise ValueError("There is no saved artwork change for this game.")
            if conn.execute(
                "SELECT 1 FROM changes WHERE name LIKE 'textures/%' OR name LIKE 'sound/%' LIMIT 1"
            ).fetchone():
                locks.enter_context(appearance_lock(root))

            # Verify every backup before changing files. An edited destination only
            # affects its own row and can be retried after a partial restore.
            for name, original, before in conn.execute(
                "SELECT name, original, before FROM changes"
            ):
                dest = _target(root, name)
                if _digest(original) != before:
                    raise ValueError(f"The restore copy of {dest.name} could not be verified.")

            restored = 0
            completed = []
            edited = []
            ids = set()
            for name, original, before, after in conn.execute(
                "SELECT name, original, before, after FROM changes ORDER BY name"
            ):
                dest = _target(root, name)
                current = extras._signature(dest)
                if current not in (before, after):
                    edited.append(name)
                    continue
                if current != before:
                    if original is None:
                        dest.unlink()
                    else:
                        extras._atomic_write(dest, original)
                    restored += 1
                completed.append(name)
                if name.startswith("pics/") and not name.startswith("pics/field/"):
                    ids.add(dest.stem)
            # A reverted era image must be eligible for the next artwork-policy refresh.
            applied = config._load_config_file(cfg.config_path).get("era_art_applied", {})
            folder = os.path.normcase(os.path.abspath(cfg.edopro_path))
            if isinstance(applied, dict) and isinstance(applied.get(folder), dict):
                policies = applied[folder]
                if ids.intersection(policies):
                    applied[folder] = {key: val for key, val in policies.items() if key not in ids}
                    if not config.save_setting(cfg.config_path, "era_art_applied", applied):
                        raise OSError(
                            "Could not finish saving artwork preferences. Retry the restore."
                        )
                cfg.era_art_applied = applied
            if edited:
                with conn:
                    conn.executemany(
                        "DELETE FROM changes WHERE name=?", ((name,) for name in completed)
                    )
        if not edited:
            db.unlink()
        elif skipped is not None:
            skipped.extend(edited)
        return restored
