"""Simple implementations for tests, previews and early development.

* :class:`StaticInstalls` — a fixed game folder (e.g. tests/fixtures/game).
* :class:`DirectStorage` — same ChangeSet API, writes immediately, keeps a naive undo copy.
* :class:`LocalPacks` — reads ``catalog.json`` + pack folders/ZIPs from a local directory
  (the ``packs/`` checkout of edopal-packs), so features work before anything is published.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from edopal.core.install import Install, LaunchTarget, RunState
from edopal.core.packs import PackInfo
from edopal.core.storage import Receipt, UndoResult


class StaticInstalls:
    def __init__(self, root: Path | None, version: str = "41.0.2"):
        self._install = Install(Path(root), "native", version, LaunchTarget(), "Test game") if root else None
        self._subs: list[Callable] = []
        self.launched = 0

    def current(self):
        return self._install

    def discover(self):
        return [self._install] if self._install else []

    def select(self, root):
        self._install = Install(Path(root), "native", None, LaunchTarget(), Path(root).name)
        for cb in self._subs:
            cb(self._install)
        return self._install

    def launch(self):
        self.launched += 1
        return "Launched (test)"

    def run_state(self):
        return RunState.CLOSED

    def subscribe(self, callback):
        self._subs.append(callback)


class _DirectChangeSet:
    def __init__(self, root: Path, backup: Path):
        self.root, self.backup, self.changed = root, backup, []

    def _target(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"path escapes game folder: {rel}")
        if p.exists() and not (self.backup / rel).exists():
            (self.backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, self.backup / rel)
        self.changed.append(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, rel, data):
        self._target(rel).write_bytes(data)

    def copy_in(self, src, rel):
        shutil.copyfile(src, self._target(rel))

    def remove(self, rel):
        p = self._target(rel)
        if p.exists():
            p.unlink()

    def set_conf(self, rel, key, value):
        p = self._target(rel)
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        out, done = [], False
        for line in lines:
            if line.split("=", 1)[0].strip() == key:
                out.append(f"{key} = {value}")
                done = True
            else:
                out.append(line)
        if not done:
            out.append(f"{key} = {value}")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")

    def __len__(self):
        return len(self.changed)


class DirectStorage:
    def __init__(self, scratch: Path):
        self.scratch = scratch
        self._receipts: dict[tuple[str, str], Receipt] = {}

    @contextmanager
    def changeset(self, install, feature, title, *, confirmed_closed=False):
        rid = uuid.uuid4().hex[:8]
        cs = _DirectChangeSet(install.root, self.scratch / "undo" / rid)
        yield cs
        self._receipts[(str(install.root), feature)] = Receipt(feature, title, tuple(cs.changed), time.time(), rid)

    def last_receipt(self, install, feature):
        return self._receipts.get((str(install.root), feature))

    def undo_last(self, install, feature, *, confirmed_closed=False):
        r = self._receipts.pop((str(install.root), feature), None)
        if not r:
            return UndoResult(())
        restored = []
        for rel in r.changed:
            b = self.scratch / "undo" / r.id / rel
            t = install.root / rel
            if b.exists():
                shutil.copy2(b, t)
            elif t.exists():
                t.unlink()
            restored.append(rel)
        return UndoResult(tuple(restored))

    def history(self, install):
        return iter([r for (root, _), r in self._receipts.items() if root == str(install.root)])


class LocalPacks:
    """``root/catalog.json`` = {"packs": [PackInfo fields...]}; ``url`` may be a relative path to a
    folder or ZIP under ``root``."""

    def __init__(self, root: Path, cache: Path):
        self.root, self.cache = Path(root), Path(cache)

    def catalog(self, kind=None, *, refresh=False):
        try:
            rows = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))["packs"]
        except (OSError, ValueError, KeyError):
            return []
        packs = [PackInfo(**row) for row in rows]
        return [p for p in packs if kind is None or p.kind == kind]

    def cached(self, pack_id):
        p = self.cache / "packs" / pack_id
        return p if (p / "manifest.json").exists() else None

    def ensure(self, pack, progress=None, cancel=None):
        if hit := self.cached(pack.id):
            return hit
        src = self.root / pack.url
        dest = self.cache / "packs" / pack.id
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            with zipfile.ZipFile(src) as z:
                z.extractall(dest)
        if progress:
            progress(pack.size, pack.size)
        return dest

    def thumbnail(self, pack):
        p = self.root / pack.thumbnail if pack.thumbnail else None
        return p if p and p.exists() else None

    def remove(self, pack_id):
        shutil.rmtree(self.cache / "packs" / pack_id, ignore_errors=True)
