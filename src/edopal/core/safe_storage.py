"""Per-install write-ahead journal for EDOPro files. See SafeStorage for the commit order."""

from __future__ import annotations

import codecs
import errno
import hashlib
import inspect
import logging
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal

from edopal.core.install import Install, RunState
from edopal.core.locks import INSTALL_LOCK_NAME, install_lock
from edopal.core.paths import Paths
from edopal.core.storage import GameRunningError, Receipt, UndoResult

_KEEP = 20
_CHUNK = 1024 * 1024
_PRIVATE = ".edopal-stage-"
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])(?:\..*)?", re.I)
_SETTING = re.compile(r"^([ \t]*([\w.-]+)[ \t]*=[ \t]*)([^\r\n]*)([\r\n]*)$")
_LOG = logging.getLogger(__name__)


class _UnsafePath(ValueError):
    pass


def _relative(name: str) -> str:
    parts = os.fspath(name).replace("\\", "/").split("/")
    for part in parts:
        if (
            not part
            or part in (".", "..")
            or part.endswith((" ", "."))
            or re.search(r'[\x00-\x1f<>:"|?*]', part)
            or _DEVICE.fullmatch(part.partition(".")[0].rstrip(" "))
            or part.casefold() == INSTALL_LOCK_NAME
            or part.casefold().startswith(_PRIVATE)
        ):
            raise _UnsafePath(f"Expected a relative game file path: {name!r}")
    return "/".join(parts)


def _sync_fd(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
            raise


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _sync_fd(fd)
        finally:
            os.close(fd)


def _mkdir(path: Path) -> None:
    if not path.is_dir():
        _mkdir(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
        _sync_dir(path.parent)


def _redirected(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass
class _Folder:
    path: Path
    fd: int | None

    def member(self, name: str) -> str | Path:
        if self.fd is not None:
            return name
        # Windows has no dir_fd API. Recheck junctions and other reparse points.
        path = self.path / name
        if self.path.resolve() != self.path or _redirected(path):
            raise _UnsafePath(f"Redirected game path: {path}")
        return path

    def read(self, name: str) -> BinaryIO:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        path = self.member(name)
        if isinstance(path, Path) and not stat.S_ISREG(path.lstat().st_mode):
            raise _UnsafePath(f"Expected a regular game file: {path}")
        try:
            fd = os.open(path, flags, dir_fd=self.fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _UnsafePath(f"Redirected game file: {self.path / name}") from exc
            raise
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise _UnsafePath(f"Expected a regular game file: {self.path / name}")
        return os.fdopen(fd, "rb")

    def digest(self, name: str) -> str | None:
        try:
            with self.read(name) as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
        except FileNotFoundError:
            return None

    def stage(self, name: str, chunks: Iterator[bytes], mode: int | None, previous: str | None = None) -> str:
        flags = os.O_WRONLY | os.O_EXCL | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(self.member(name), flags, 0o666 if mode is None else 0o600, dir_fd=self.fd)
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as stream:
            for chunk in chunks:
                stream.write(chunk)
                digest.update(chunk)
            checksum = digest.hexdigest()
            if checksum == previous:
                return checksum  # The caller discards this identical copy.
            stream.flush()
            if mode is not None:
                if self.fd is None:
                    os.chmod(self.member(name), mode)
                else:
                    os.fchmod(fd, mode)
            os.fsync(fd)
        return checksum

    def unlink(self, name: str) -> None:
        path = self.member(name)
        try:
            os.unlink(path, dir_fd=self.fd)
        except FileNotFoundError:
            pass
        except PermissionError:
            # Windows cannot delete a private stage that inherited the source's readonly bit.
            if not isinstance(path, Path) or not name.startswith(_PRIVATE) or not stat.S_ISREG(path.lstat().st_mode):
                raise
            os.chmod(self.member(name), stat.S_IWRITE)
            os.unlink(self.member(name))

    def publish(self, stage: str, name: str, expected: str | None, incoming: str | None) -> bool:
        if incoming is not None and self.digest(stage) != incoming:
            raise OSError(f"Staged file for {name} is missing or damaged.")
        if self.digest(name) != expected:
            return False
        if incoming is None:
            self.unlink(name)
        else:
            os.replace(self.member(stage), self.member(name), src_dir_fd=self.fd, dst_dir_fd=self.fd)
        return True


@contextmanager
def _folder(root: Path, parent: Path, *, create: bool = False) -> Iterator[_Folder]:
    """Walk from the canonical root without following directory links, including at publish time."""
    path = root
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = None if os.name == "nt" else os.open(root, flags)
    try:
        for part in parent.parts:
            path = path / part
            if fd is None:
                if _redirected(path):
                    raise _UnsafePath(f"Redirected game directory: {path}")
                if create:
                    path.mkdir(exist_ok=True)
                if not path.exists():
                    raise FileNotFoundError(path)
                if not path.is_dir():
                    raise _UnsafePath(f"Expected a game directory: {path}")
            else:
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, dir_fd=fd)
                    _sync_fd(fd)
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
        yield _Folder(path, fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _UnsafePath(f"Expected an ordinary game directory: {path}") from exc
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _edit_conf(data: bytes, updates: dict[str, str]) -> bytes:
    bom, encoding = b"", "utf-8"
    for marker, codec in (
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
        (codecs.BOM_UTF8, "utf-8"),
    ):
        if data.startswith(marker):
            bom, encoding = marker, codec
            break
    try:
        text = data[len(bom) :].decode(encoding)
    except UnicodeDecodeError:
        if bom:
            raise
        encoding = "cp1252"
        text = data.decode(encoding, "surrogateescape")
    lines = re.findall(r"[^\r\n]*(?:\r\n|[\r\n]|$)", text)[:-1]
    newline = next((m[0] for m in re.finditer(r"\r\n|[\r\n]", text)), "\n")
    # EDOPro uses the last declaration. Leave shadowed declarations byte-identical.
    last = {m[2]: (i, m) for i, line in enumerate(lines) if (m := _SETTING.fullmatch(line))}
    for key, value in updates.items():
        if key in last:
            index, match = last[key]
            body = match[3]
            suffix = re.search(r"[ \t]+[#;].*$|[ \t]*$", body)[0]
            if body.startswith(("#", ";")):
                suffix = " " + body
            lines[index] = match[1] + value + suffix + match[4]
        else:
            if lines and not lines[-1].endswith(("\r", "\n")):
                lines[-1] += newline
            lines.append(f"{key} = {value}{newline}")
    return bom + "".join(lines).encode(encoding, "surrogateescape")


@dataclass
class _Edit:
    # A write owns a slice of the shared spool. A copy references its source until commit.
    source: tuple[int, int] | Path | None = None
    use_original: bool = False
    settings: dict[str, str] = field(default_factory=dict)

    def chunks(self, spool: BinaryIO, original: bytes | None) -> Iterator[bytes]:
        if isinstance(self.source, Path):
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(self.source, flags), "rb") as src:
                if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                    raise ValueError(f"Expected a regular copy source: {self.source}")
                yield from iter(lambda: src.read(_CHUNK), b"")
        elif self.source is not None:
            offset, size = self.source
            spool.seek(offset)
            while size:
                block = spool.read(min(size, _CHUNK))
                if not block:
                    raise OSError("Incomplete changeset spool")
                yield block
                size -= len(block)
        elif self.use_original and original:
            yield original


class _Changes:
    def __init__(self, spool: BinaryIO):
        self.spool = spool
        self.edits: dict[str, _Edit] = {}

    def write(self, rel: str, data: bytes) -> None:
        name = _relative(rel)
        offset = self.spool.tell()
        self.spool.write(data)
        self.edits[name] = _Edit((offset, len(data)))

    def copy_in(self, src: Path, rel: str) -> None:
        self.edits[_relative(rel)] = _Edit(Path(src).absolute())

    def remove(self, rel: str) -> None:
        self.edits[_relative(rel)] = _Edit()

    def set_conf(self, rel: str, key: str, value: str) -> None:
        if not re.fullmatch(r"[\w.-]+", key) or any(c in value for c in "\r\n\0"):
            raise ValueError("Invalid configuration key or multiline value")
        self.edits.setdefault(_relative(rel), _Edit(use_original=True)).settings[key] = value

    def __len__(self) -> int:
        return len(self.edits)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, feature TEXT NOT NULL, title TEXT NOT NULL,
    created REAL NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    seq INTEGER NOT NULL REFERENCES receipts ON DELETE CASCADE, idx INTEGER NOT NULL, rel TEXT NOT NULL,
    original BLOB, mode INTEGER, before TEXT, after TEXT, PRIMARY KEY(seq,idx));
"""


def _stage_name(token: str, index: int) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", token) or not isinstance(index, int):
        raise ValueError("Invalid staging name in the undo journal")
    return f"{_PRIVATE}{token}-{index}"


def _digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


class SafeStorage:
    """Stage sibling files, commit originals and SHA-256s to SQLite, then publish.

    A durable staging intent precedes any temporary game files. Any process taking the
    install lock can discard unfinished staging, roll back partial publication, or finish
    an interrupted undo. Each journal state transition is one SQLite transaction for the
    entire batch. Only one original file at a time is materialized in memory.
    """

    def __init__(self, paths: Paths | Path, run_state: Callable[..., RunState]):
        self.data = paths.data if isinstance(paths, Paths) else Path(paths)
        # The InstallService contract accepts no arguments. The real process probe can
        # additionally inspect the job's install when the picker changes mid-download.
        try:
            inspect.signature(run_state).bind(install=None)
        except (TypeError, ValueError):
            self._probe = lambda _install: run_state()
        else:
            self._probe = lambda install: run_state(install=install)

    def _closed(self, install: Install, confirmed: bool) -> None:
        state = self._probe(install)
        if state is RunState.CLOSED or (state is RunState.UNKNOWN and confirmed):
            return
        if state is RunState.RUNNING:
            raise GameRunningError("Close EDOPro before changing its files.")
        raise GameRunningError("Confirm that EDOPro is closed before changing its files.")

    @contextmanager
    def _session(self, install: Install, *, writing: bool = False, confirmed: bool = False, recovered_undos=None):
        root = install.root.resolve(strict=True)
        key = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()
        data = self.data.resolve()
        journal = data / "journal" / key / "history.sqlite"
        if journal.resolve() != journal:
            raise _UnsafePath("Redirected undo journal")
        if not writing and not journal.exists():
            yield root, None
            return
        with install_lock(root):
            if writing:
                self._closed(install, confirmed)
            _mkdir(journal.parent)
            with closing(sqlite3.connect(journal)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA synchronous=EXTRA")
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone():
                    db.execute("PRAGMA auto_vacuum=FULL")
                    db.executescript("BEGIN;" + _SCHEMA + "COMMIT;")
                    _sync_dir(journal.parent)
                pending = db.execute("SELECT * FROM receipts WHERE state!='ready' ORDER BY seq").fetchall()
                if pending and not writing:
                    self._closed(install, confirmed)
                for record in pending:
                    result = self._restore(root, db, record, recovering=True)
                    if recovered_undos is not None and record["state"] == "undoing":
                        recovered_undos[record["feature"]] = result
                yield root, db

    @contextmanager
    def changeset(self, install: Install, feature: str, title: str, *, confirmed_closed: bool = False) -> Iterator[_Changes]:
        # One spill file for the entire run, including thousands of cs.write(jpg) calls.
        with tempfile.SpooledTemporaryFile(max_size=4 * _CHUNK, mode="w+b") as spool:
            changes = _Changes(spool)
            yield changes
            with self._session(install, writing=True, confirmed=confirmed_closed) as (root, db):
                if changes.edits:
                    self._commit(install, root, db, changes, feature, title, confirmed_closed)

    def _commit(self, install, root, db, changes, feature, title, confirmed):
        token = uuid.uuid4().hex
        with db:
            seq = db.execute(
                "INSERT INTO receipts(id,feature,title,created,state) VALUES(?,?,?,?,'staging')",
                (token, feature, title, time.time()),
            ).lastrowid
            db.executemany(
                "INSERT INTO files(seq,idx,rel) VALUES(?,?,?)",
                ((seq, index, name) for index, name in enumerate(changes.edits)),
            )
        try:
            with db:
                discarded = set()
                for index, (name, edit) in enumerate(changes.edits.items()):
                    previous = self._stage_edit(root, Path(name), _stage_name(token, index), edit, changes.spool, discarded)
                    if previous is None:
                        db.execute("DELETE FROM files WHERE seq=? AND idx=?", (seq, index))
                    else:
                        db.execute(
                            "UPDATE files SET original=?,mode=?,before=?,after=? WHERE seq=? AND idx=?",
                            (*previous, seq, index),
                        )
                self._flush(root, discarded)
                db.execute("UPDATE receipts SET state='pending' WHERE seq=?", (seq,))
            self._closed(install, confirmed)
            directories = set()
            rows = db.execute("SELECT idx,rel,before,after FROM files WHERE seq=? ORDER BY idx", (seq,))
            for row in rows:
                dest = Path(row["rel"])
                with _folder(root, dest.parent) as folder:
                    if not folder.publish(_stage_name(token, row["idx"]), dest.name, row["before"], row["after"]):
                        raise OSError(f"{dest} changed while preparing the changeset.")
                    directories.add(dest.parent)
            self._flush(root, directories)
            with db:
                if directories:
                    db.execute("UPDATE receipts SET state='ready' WHERE seq=?", (seq,))
                    db.execute(
                        "DELETE FROM receipts WHERE feature=? AND seq NOT IN "
                        "(SELECT seq FROM receipts WHERE feature=? ORDER BY seq DESC LIMIT ?)",
                        (feature, feature, _KEEP),
                    )
                else:
                    db.execute("DELETE FROM receipts WHERE seq=?", (seq,))
        except Exception:
            record = db.execute("SELECT * FROM receipts WHERE seq=?", (seq,)).fetchone()
            if record and record["state"] != "ready":
                # If EDOPro opened while staging, defer all game-folder cleanup until it closes.
                self._closed(install, confirmed)
                self._restore(root, db, record, recovering=True)
            raise

    @staticmethod
    def _stage_edit(root, dest, stage, edit, spool, discarded):
        removing = not edit.settings and edit.source is None
        try:
            with _folder(root, dest.parent, create=not removing) as folder:
                try:
                    with folder.read(dest.name) as stream:
                        mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
                        original = stream.read()
                except FileNotFoundError:
                    mode = original = None
                before = _digest(original)
                after = None
                if not removing:
                    chunks = edit.chunks(spool, original)
                    if edit.settings:
                        chunks = iter((_edit_conf(b"".join(chunks), edit.settings),))
                    after = folder.stage(stage, chunks, mode, previous=before)
                if before != after:
                    return original, mode, before, after
                if not removing:
                    folder.unlink(stage)
                    discarded.add(dest.parent)
        except FileNotFoundError:
            if not removing:
                raise
        return None

    @staticmethod
    def _flush(root, directories):
        if os.name != "nt":
            for parent in directories:
                with _folder(root, parent) as folder:
                    _sync_fd(folder.fd)

    @staticmethod
    def _restore_file(folder, dest, stage, row, recovering) -> Literal["restored", "settled", "conflict"]:
        before, after = row["before"], row["after"]
        current = folder.digest(dest.name)
        if current != after:
            # A crash may have happened after restoring this file and before retiring its row.
            return "settled" if recovering and current == before else "conflict"
        if _digest(row["original"]) != before:
            raise OSError(f"Damaged undo copy of {dest}.")
        if before is None:
            folder.unlink(dest.name)
            return "restored"
        folder.stage(stage, iter((row["original"],)), row["mode"])
        try:
            if folder.publish(stage, dest.name, after, before):
                return "restored"
            return "conflict"
        finally:
            folder.unlink(stage)

    def _restore(self, root, db, record, *, recovering: bool) -> UndoResult:
        state, seq = record["state"], record["seq"]
        if state not in ("staging", "pending", "undoing"):
            raise ValueError("Invalid transaction state in the undo journal")
        restored, conflicts, completed = [], [], []
        directories = set()
        rows = db.execute("SELECT * FROM files WHERE seq=? ORDER BY idx", (seq,))
        for row in rows:
            dest = Path(_relative(row["rel"]))
            stage = _stage_name(record["id"], row["idx"])
            try:
                recreate_parent = row["before"] is not None and row["after"] is None
                with _folder(root, dest.parent, create=recreate_parent) as folder:
                    directories.add(dest.parent)
                    folder.unlink(stage)
                    if state == "staging":
                        continue
                    outcome = self._restore_file(folder, dest, stage, row, recovering)
            except FileNotFoundError:
                outcome = "settled" if recovering and row["before"] is None else "conflict"
            except _UnsafePath:
                outcome = "conflict"
            if state == "staging":
                continue
            if outcome == "conflict":
                conflicts.append(row["rel"])
            else:
                completed.append((seq, row["idx"]))
                if outcome == "restored":
                    restored.append(row["rel"])
        self._flush(root, directories)
        with db:
            if conflicts and state == "undoing":
                db.executemany("DELETE FROM files WHERE seq=? AND idx=?", completed)
                db.execute("UPDATE receipts SET state='ready' WHERE seq=?", (seq,))
            else:
                db.execute("DELETE FROM receipts WHERE seq=?", (seq,))
        if conflicts and recovering:
            _LOG.warning("Kept external edits during recovery: %s", ", ".join(conflicts))
        return UndoResult(tuple(restored), tuple(conflicts))

    def undo_last(self, install: Install, feature: str, *, confirmed_closed: bool = False) -> UndoResult:
        recovered_undos = {}
        with self._session(install, writing=True, confirmed=confirmed_closed, recovered_undos=recovered_undos) as (root, db):
            if feature in recovered_undos:
                return recovered_undos[feature]
            record = db.execute("SELECT * FROM receipts WHERE feature=? ORDER BY seq DESC LIMIT 1", (feature,)).fetchone()
            if record is None:
                return UndoResult(())
            for row in db.execute("SELECT rel,original,before FROM files WHERE seq=?", (record["seq"],)):
                if _digest(row["original"]) != row["before"]:
                    raise OSError(f"Damaged undo copy of {row['rel']}.")
            with db:
                db.execute("UPDATE receipts SET state='undoing' WHERE seq=?", (record["seq"],))
            record = db.execute("SELECT * FROM receipts WHERE seq=?", (record["seq"],)).fetchone()
            return self._restore(root, db, record, recovering=False)

    @staticmethod
    def _receipt(db, row) -> Receipt:
        names = db.execute("SELECT rel FROM files WHERE seq=? ORDER BY idx", (row["seq"],))
        return Receipt(row["feature"], row["title"], tuple(name for (name,) in names), row["created"], row["id"])

    def last_receipt(self, install: Install, feature: str) -> Receipt | None:
        with self._session(install) as (_, db):
            if db is not None:
                row = db.execute("SELECT * FROM receipts WHERE feature=? ORDER BY seq DESC LIMIT 1", (feature,)).fetchone()
                if row:
                    return self._receipt(db, row)
            return None

    def history(self, install: Install) -> Iterator[Receipt]:
        with self._session(install) as (_, db):
            if db is None:
                return iter(())
            return iter([self._receipt(db, row) for row in db.execute("SELECT * FROM receipts ORDER BY seq DESC")])
