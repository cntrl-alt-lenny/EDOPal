"""Stage verified before/after images, then publish under an on-disk transaction."""

import hashlib
import io
import json
import os
import shutil
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal

from music_library import _game_running

from . import preferences
from .archive import opened
from .core import (
    MAX_FILE,
    MAX_FILES,
    MAX_TOTAL,
    Cancel,
    GameRunning,
    Installation,
    Progress,
    check,
    folded,
    publish_new,
    read_small,
    safe,
    signature,
    transfer,
)
from .inventory import CONF, INDEX, category, managed_puzzles
from .journal import _TOKEN, Change, Transaction, _changes, _home, _locks, _record, transactions
from .plan import Plan, plan_restore, puzzle_target


def _space(path: Path, size: int) -> None:
    while not path.exists():
        path = path.parent
    if shutil.disk_usage(path).free < size + (64 << 20):
        raise OSError(f"Insufficient free space on {path}")


def _budget(plan: Plan) -> None:
    sizes = {e.path: e.size for e in plan.manifest.entries}
    staging = 0
    seen = set()
    volumes: dict[int, int] = defaultdict(int)
    anchors: dict[int, Path] = {}
    for item in plan.items:
        if item.action == "skip" or item.target in seen:
            continue
        seen.add(item.target)
        path = plan.install.target(item.target)
        after = preferences.LIMIT + 2048 if item.action == "merge" else sizes[item.source]
        before = path.stat().st_size if item.before else 0
        staging += before + after + 8192
        # Include directory allocation and one temporary publication per target.
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        device = parent.stat().st_dev
        anchors[device] = parent
        volumes[device] += after + 4096 * (1 + item.target.count("/"))
    if staging > MAX_TOTAL * 2 + MAX_FILES * 8192:
        raise ValueError("Restore staging would exceed its disk budget")
    state = _home(plan.install)
    device = state.stat().st_dev
    anchors[device] = state
    volumes[device] += staging
    for device, size in volumes.items():
        _space(anchors[device], size)


def _blob(tx: Path, stream: BinaryIO, size: int, digest: str, cancel: Cancel) -> None:
    path = safe(tx, digest)
    if path.exists():
        actual = transfer(stream, None, size, cancel)
    else:
        _space(tx, size)
        with path.open("xb") as output:
            actual = transfer(stream, output, size, cancel)
    if actual != (size, digest):
        raise ValueError("File changed or checksum mismatch during staging")


def _save_original(install: Installation, tx: Path, change: Change, cancel: Cancel) -> None:
    target = install.target(change.target)
    if change.before is None:
        if target.exists():
            raise ValueError(f"Destination appeared after preview: {change.target}")
    else:
        with target.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > MAX_FILE:
                raise ValueError("Destination grew after preview")
            _blob(tx, stream, size, change.before, cancel)


def _stage(plan: Plan, tx: Path, progress: Progress, cancel: Cancel) -> list[Change]:
    changes = []
    by_name = {i.source: i for i in plan.items}
    merges: dict[str, dict[str, bytes]] = {}
    publications = len({item.target for item in plan.items if item.action != "skip"})
    with opened(plan.archive) as (archive, manifest):
        if manifest != plan.manifest:
            raise ValueError("Backup changed after preview")
        for i, entry in enumerate(manifest.entries, 1):
            item = by_name.get(entry.path)
            with archive.open(entry.path) as stream:
                if entry.category == "preferences" or entry.path == INDEX:
                    data = stream.read()
                    if hashlib.sha256(data).hexdigest() != entry.sha256:
                        raise ValueError("Backup checksum mismatch")
                    if entry.category == "preferences":
                        key = entry.path.split("/")[1]
                        preferences.validate(key, data)
                        if item and item.action == "merge":
                            merges.setdefault(item.target, {})[key] = data
                    else:
                        tracks = json.loads(data)
                        if (
                            not isinstance(tracks, list)
                            or len(tracks) > MAX_FILES
                            or any(
                                not isinstance(t, str)
                                or category("studio/soundtracks/" + t) != "audio"
                                for t in tracks
                            )
                        ):
                            raise ValueError("Invalid portable music index")
                        if item and item.action == "write":
                            _blob(tx, io.BytesIO(data), entry.size, entry.sha256, cancel)
                elif item and item.action == "write":
                    _blob(tx, stream, entry.size, entry.sha256, cancel)
                elif transfer(stream, None, entry.size, cancel) != (entry.size, entry.sha256):
                    raise ValueError("Backup checksum mismatch")
            if item and item.action == "write":
                changes.append(Change(item.source, item.target, item.before, entry.sha256))
            if progress:
                progress(i, len(manifest.entries) + publications, entry.path)
            check(cancel)
    for target, keys in merges.items():
        item = next(i for i in plan.items if i.target == target and i.action == "merge")
        original = (
            read_small(plan.install.target(target), preferences.LIMIT) if item.before else b""
        )
        if item.before and hashlib.sha256(original).hexdigest() != item.before:
            raise ValueError("Preferences changed after preview")
        data = preferences.merge(original, keys)
        if len(data) > preferences.LIMIT:
            raise ValueError("Merged system.conf exceeds size limit")
        digest = hashlib.sha256(data).hexdigest()
        _blob(tx, io.BytesIO(data), len(data), digest, cancel)
        changes.append(Change(item.source, target, item.before, digest))
    for change in changes:
        _save_original(plan.install, tx, change, cancel)
    return changes


def _current(install: Installation, change: Change, cancel: Cancel) -> tuple[Path, str | None]:
    target = install.target(change.target)
    if category(change.source) == "puzzles":
        puzzle_target(install, change.target, managed_puzzles(install))
    # A new sibling with different casing can invalidate the preview even on Linux.
    root = install.game if change.target.startswith("game/") else install.data
    parent = root
    for part in target.relative_to(root).parts:
        if parent.is_dir() and any(
            c.name != part and folded(c.name) == folded(part) for c in parent.iterdir()
        ):
            raise ValueError(f"Case collision since preview: {target}")
        parent /= part
    return target, signature(target, cancel)


def _apply(
    install: Installation,
    tx: Path,
    changes: list[Change],
    reverse: bool,
    progress: Progress,
    cancel: Cancel,
    *,
    fresh: bool = False,
    begin: Callable[[], None] | None = None,
    require_closed: bool = False,
) -> None:
    desired = [
        (c, c.after if reverse else c.before, c.before if reverse else c.after)
        for c in (reversed(changes) if reverse else changes)
    ]
    # Preflight every file before changing any, also on retries and rollback.
    for change, old, new in desired:
        target, current = _current(install, change, cancel)
        if current != old and (fresh or current != new):
            raise ValueError(f"Destination changed since preview: {target}")
        for digest in {old, new} - {None}:
            if signature(safe(tx, digest), cancel) != digest:
                raise ValueError(f"Restore journal checksum mismatch: {target}")
        if current != new and change.target == CONF and _game_running(install.game):
            raise GameRunning("Close EDOPro before restoring preferences")
    if require_closed and _game_running(install.game):
        raise GameRunning("Close EDOPro before changing files")
    next_game_check = monotonic() + 1

    def check_game(target: str) -> None:
        nonlocal next_game_check
        # Starting the Windows process probe for each deck would stall large transfers.
        if target == CONF or (require_closed and monotonic() >= next_game_check):
            if _game_running(install.game):
                raise GameRunning("Close EDOPro before changing files")
            next_game_check = monotonic() + 1

    if begin:
        begin()
    for i, (change, old, new) in enumerate(desired, 1):
        check(cancel)
        target, current = _current(install, change, cancel)
        temp = safe(target.parent, f".edopro-restore-{tx.name}.tmp")
        if not fresh:
            # A killed process can leave this transaction's unpublished sibling behind.
            temp.unlink(missing_ok=True)
        if current != new:
            if current != old:
                raise ValueError(f"Destination changed during restore: {target}")
            check_game(change.target)
            if new is None:
                # Only undo a file this transaction added, whose bytes still match.
                # Its complete content is retained in the transaction.
                target.unlink()
            else:
                blob = safe(tx, new)
                _space(target.parent, blob.stat().st_size)
                target.parent.mkdir(exist_ok=True, parents=True)
                out = temp.open("xb")
                try:
                    with out, blob.open("rb") as stream:
                        if transfer(stream, out, MAX_FILE, cancel)[1] != new:
                            raise ValueError("Restore image changed during copy")
                    check_game(change.target)
                    if _current(install, change, cancel)[1] != old:
                        raise ValueError(f"Destination changed during restore: {target}")
                    if old is None:
                        publish_new(temp, target)
                    else:
                        temp.chmod(target.stat().st_mode)
                        temp.replace(target)
                finally:
                    temp.unlink(missing_ok=True)
        if progress:
            progress(i, len(desired), change.target)


def execute(
    plan: Plan, *, progress: Progress = None, cancel: Cancel = None, require_closed: bool = False
) -> Transaction:
    """Apply exactly the preview. A durable pending journal precedes the first game write."""
    with _locks(plan.install):
        for tx in transactions(plan.install):
            if tx.status in {"pending", "rolling_back"}:
                raise ValueError(f"Recover interrupted restore {tx.id} first")
        if plan != plan_restore(
            plan.archive, plan.install, plan.policy, categories=plan.categories, cancel=cancel
        ):
            raise ValueError("Restore preview is stale. Preview again")
        home = _home(plan.install)
        home.mkdir(parents=True, exist_ok=True)
        # Only an atomically promoted staging folder can have published targets.
        # The OS locks exclude a second process still preparing these images.
        for abandoned in home.glob(".staging-*"):
            if _TOKEN.fullmatch(abandoned.name.removeprefix(".staging-")):
                shutil.rmtree(safe(home, abandoned.name))
        _budget(plan)
        token = uuid.uuid4().hex
        txdir = safe(home, token)
        staging = safe(home, ".staging-" + token)
        staging.mkdir(mode=0o700)
        try:
            changes = _stage(plan, staging, progress, cancel)
            check(cancel)
            if require_closed and _game_running(plan.install.game):
                raise GameRunning("Close EDOPro before changing files")
            if any(c.target == CONF for c in changes) and _game_running(plan.install.game):
                raise GameRunning("Close EDOPro before restoring preferences")
            _record(staging, "pending", changes)
            staging.rename(txdir)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

        def report(i: int, total: int, name: str) -> None:
            if progress:
                progress(len(plan.manifest.entries) + i, len(plan.manifest.entries) + total, name)

        _apply(
            plan.install,
            txdir,
            changes,
            False,
            report,
            cancel,
            fresh=True,
            require_closed=require_closed,
        )
        _record(txdir, "complete", changes)
        return Transaction(token, "complete")


def recover(
    install: Installation,
    token: str,
    mode: Literal["finish", "rollback"],
    *,
    progress: Progress = None,
    cancel: Cancel = None,
    require_closed: bool = False,
) -> Transaction:
    """Finish/undo using only verified journal images; the USB archive can be disconnected."""
    if not _TOKEN.fullmatch(token) or mode not in {"finish", "rollback"}:
        raise ValueError("Invalid recovery request")
    with _locks(install):
        txdir = safe(_home(install), token)
        state, changes = _changes(txdir)
        reverse = mode == "rollback"
        expected = "rolled_back" if reverse else "complete"
        if state == expected:
            return Transaction(token, state)
        if not reverse and state != "pending":
            raise ValueError("A rollback can only be continued")
        for other in transactions(install):
            if other.id != token and other.status in {"pending", "rolling_back"}:
                raise ValueError(f"Recover interrupted restore {other.id} first")

        def begin() -> None:
            if reverse and state != "rolling_back":
                _record(txdir, "rolling_back", changes)

        _apply(
            install,
            txdir,
            changes,
            reverse,
            progress,
            cancel,
            begin=begin,
            require_closed=require_closed,
        )
        _record(txdir, expected, changes)
        return Transaction(token, expected)
