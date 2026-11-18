"""Transaction records and shared restore locks."""

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from extras import _atomic_write
from state_lock import locked

from .archive import decode
from .core import MAX_FILES, MAX_MANIFEST, Digest, Installation, folded, portable, read_small, safe
from .inventory import CONF, category

_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_SIDECAR = re.compile(r"game/config/system \(incoming(?: [0-9]+)?\)\.conf\Z")
_STATES = {"pending", "complete", "rolling_back", "rolled_back"}


@dataclass(frozen=True)
class Change:
    source: str
    target: str
    before: str | None
    after: str


@dataclass(frozen=True)
class Transaction:
    id: str
    status: str
    updated_at: int | None = field(default=None, compare=False)
    files: int | None = field(default=None, compare=False)


def _home(install: Installation) -> Path:
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(install.game)))).hexdigest()[:32]
    return safe(install.data, "restore-journals/" + key)


def transactions(install: Installation) -> tuple[Transaction, ...]:
    home = _home(install)
    if not home.exists():
        return ()
    found = []
    for child in sorted(home.iterdir()):
        if _TOKEN.fullmatch(child.name):
            record = safe(home, child.name + "/journal.json")
            if record.exists():
                doc = decode(read_small(record, MAX_MANIFEST))
                if doc.get("status") not in _STATES:
                    raise ValueError("Invalid restore journal status")
                changes = doc.get("changes")
                found.append(
                    Transaction(
                        child.name,
                        doc["status"],
                        int(record.stat().st_mtime),
                        len(changes) if isinstance(changes, list) else None,
                    )
                )
    return tuple(found)


@contextmanager
def _locks(install: Installation) -> Iterator[None]:
    with ExitStack() as stack:
        # Serialize different config folders pointing at one game AND different games
        # sharing a Studio collection. The lock files remain as stable OS lock identities.
        for root in sorted({install.game, install.data}, key=lambda p: os.path.normcase(str(p))):
            lock = safe(root, ".edopro-restore.lock")
            stack.enter_context(locked(root, lock.name))
        yield


def _record(tx: Path, status: str, changes: list[Change]) -> None:
    data = json.dumps(
        {"version": 1, "status": status, "changes": [asdict(c) for c in changes]}
    ).encode()
    if len(data) > MAX_MANIFEST:
        raise ValueError("Restore journal is too large")
    _atomic_write(safe(tx, "journal.json"), data)


def _changes(tx: Path) -> tuple[str, list[Change]]:
    doc = decode(read_small(safe(tx, "journal.json"), MAX_MANIFEST))
    if doc.get("version") != 1 or doc.get("status") not in _STATES:
        raise ValueError("Unsupported restore journal")
    rows = doc.get("changes")
    if not isinstance(rows, list) or len(rows) > MAX_FILES:
        raise ValueError("Invalid restore journal")
    changes = []
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"source", "target", "before", "after"}:
            raise ValueError("Invalid restore journal entry")
        change = Change(**row)
        cat = category(change.source)
        portable(change.target)
        if (
            not isinstance(change.after, str)
            or not Digest.fullmatch(change.after)
            or (
                change.before is not None
                and (not isinstance(change.before, str) or not Digest.fullmatch(change.before))
            )
        ):
            raise ValueError("Invalid restore digest")
        if cat == "preferences":
            valid = change.target == CONF or _SIDECAR.fullmatch(change.target)
        else:
            src = change.source.split("/")
            dst = change.target.split("/")
            valid = (
                len(src) == len(dst)
                and src[0] == dst[0]
                and folded(src[1]) == folded(dst[1])
                and all(_same_or_alternate(s, d) for s, d in zip(src[2:], dst[2:], strict=True))
            )
        if not valid or folded(change.target) in names:
            raise ValueError("Invalid restore target")
        names.add(folded(change.target))
        changes.append(change)
    return doc["status"], changes


def _same_or_alternate(source: str, target: str) -> bool:
    if folded(source) == folded(target):
        return True
    base = Path(folded(source))
    pattern = re.escape(base.stem) + r" \(incoming(?: [0-9]+)?\)" + re.escape(base.suffix)
    return re.fullmatch(pattern, folded(target)) is not None
