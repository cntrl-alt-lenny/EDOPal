"""Produce a read-only, policy-specific restore preview, including virtual preference keys."""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from . import preferences
from .archive import Manifest, inspect, opened
from .core import (
    DEFAULTS,
    Cancel,
    Installation,
    check,
    folded,
    portable,
    read_small,
    safe,
    signature,
)
from .inventory import CONF, hashes, managed, managed_puzzles, selection

Policy = Literal["keep_mine", "take_theirs", "keep_both"]


@dataclass(frozen=True)
class Item:
    source: str
    target: str
    status: str
    action: str
    before: str | None
    detail: str = ""
    mine: str | None = None
    theirs: str | None = None
    existing_path: str | None = None
    existing_sha256: str | None = None


@dataclass(frozen=True)
class Plan:
    archive: Path
    install: Installation
    policy: Policy
    categories: tuple[str, ...]
    manifest: Manifest
    items: tuple[Item, ...]


class Names:
    """Index actual and planned names by component, even on case-sensitive filesystems."""

    def __init__(self, install: Installation):
        self.install = install
        self.children: dict[str, dict[str, list[str]]] = {}
        self.claimed: set[str] = set()
        self.directories: dict[str, str] = {}

    def listing(self, parent: str) -> dict[str, list[str]]:
        if parent not in self.children:
            scope, _, rel = parent.partition("/")
            root = self.install.game if scope == "game" else self.install.data
            directory = safe(root, rel) if rel else root
            names: dict[str, list[str]] = defaultdict(list)
            if directory.exists():
                for child in directory.iterdir():
                    names[folded(child.name)].append(child.name)
            self.children[parent] = names
        return self.children[parent]

    def alternate(self, parent: str, name: str) -> str:
        names = self.listing(parent)
        p = PurePosixPath(name)
        for n in range(1, 10001):
            suffix = "" if n == 1 else f" {n}"
            candidate = f"{p.stem} (incoming{suffix}){p.suffix}"
            if folded(candidate) not in names:
                portable(parent + "/" + candidate)
                return candidate
        raise ValueError(f"Too many alternate names for {name}")

    def resolve(self, name: str) -> tuple[str, str]:
        parts = name.split("/")
        parent = parts[0]
        detail = ""
        for i, part in enumerate(parts[1:], 1):
            prefix = "/".join(parts[: i + 1])
            final = i == len(parts) - 1
            if not final and prefix in self.directories:
                parent = self.directories[prefix]
                continue
            names = self.listing(parent)
            matches = names.get(folded(part), [])
            if len(matches) > 1:
                if i == 1:
                    raise ValueError(f"Choose a single destination category folder: {matches}")
                actual = self.alternate(parent, part)
                detail = "ambiguous destination names: " + ", ".join(sorted(matches))
            else:
                actual = matches[0] if matches else part
            path = parent + "/" + actual
            if path in self.claimed:
                # Two source names cannot share a destination, including ancestor folders.
                actual = self.alternate(parent, part)
                path = parent + "/" + actual
                detail = "incoming names collide"
            elif actual != part and not detail:
                detail = "destination spelling differs"
            self.claimed.add(path)
            names[folded(actual)] = [actual]
            if not final:
                self.directories[prefix] = path
            parent = path
        if parent != name and not detail:
            detail = "directory spelling differs"
        return parent, detail

    def beside(self, name: str) -> str:
        parent, base = name.rsplit("/", 1)
        actual = self.alternate(parent, base)
        self.listing(parent)[folded(actual)] = [actual]
        result = parent + "/" + actual
        self.claimed.add(result)
        return result


def puzzle_target(install: Installation, target: str, excluded: set[str]) -> None:
    if managed(target, excluded):
        raise ValueError(f"Managed puzzle repository: {target}")
    path = install.target(target)
    for parent in path.parents:
        if parent == install.game:
            break
        if (parent / ".git").exists():
            raise ValueError(f"Managed puzzle folder: {parent.name}")


def plan_restore(
    path: Path,
    install: Installation,
    policy: Policy = "keep_mine",
    *,
    categories: Iterable[str] | None = None,
    cancel: Cancel = None,
) -> Plan:
    if policy not in {"keep_mine", "take_theirs", "keep_both"}:
        raise ValueError("Unknown conflict policy")
    items = []
    names = Names(install)
    path = Path(path).absolute()
    with opened(path) as (archive, manifest):
        selected = selection(categories if categories is not None else manifest.categories)
        excluded = managed_puzzles(install) if "puzzles" in selected else set()
        prefs: dict[str, bytes] = {}
        conf = CONF
        previous = None
        sidecar = None
        if "preferences" in selected and any(e.category == "preferences" for e in manifest.entries):
            conf, spelling = names.resolve(CONF)
            if spelling:
                raise ValueError(f"Ambiguous configuration path: {conf}")
            file = install.target(conf)
            if file.exists():
                content = read_small(file, preferences.LIMIT)
                previous = hashlib.sha256(content).hexdigest()
                prefs = preferences.values(content)
        for entry in sorted(manifest.entries, key=lambda e: e.path):
            check(cancel)
            if entry.category not in selected:
                continue
            if entry.category == "preferences":
                key = entry.path.split("/")[1]
                value = archive.read(entry.path)
                if hashlib.sha256(value).hexdigest() != entry.sha256:
                    raise ValueError(f"Preference checksum mismatch: {key}")
                preferences.validate(key, value)
                mine = prefs.get(key)
                status = "add" if mine is None else "identical" if mine == value else "conflict"
                action = "merge"
                target = conf
                if status == "identical" or (status == "conflict" and policy == "keep_mine"):
                    action = "skip"
                elif status == "conflict" and policy == "keep_both":
                    sidecar = sidecar or names.beside(CONF)
                    target = sidecar
                items.append(
                    Item(
                        entry.path,
                        target,
                        status,
                        action,
                        previous if target == conf else None,
                        "",
                        mine.decode() if mine is not None else None,
                        value.decode(),
                    )
                )
                continue
            target, detail = names.resolve(entry.path)
            if entry.category == "puzzles":
                puzzle_target(install, target, excluded)
            before = signature(install.target(target), cancel)
            existing = target if before is not None else None
            existing_sha = before
            status = "identical" if before == entry.sha256 else "conflict" if before else "add"
            if detail and status != "identical":
                status = "conflict"
            action = "write"
            if status == "identical" or (status == "conflict" and policy == "keep_mine"):
                action = "skip"
            elif status == "conflict" and before and policy == "keep_both":
                target = names.beside(target)
                before = None
            items.append(
                Item(
                    entry.path,
                    target,
                    status,
                    action,
                    before,
                    detail,
                    existing_path=existing,
                    existing_sha256=existing_sha,
                )
            )
    return Plan(path, install, policy, tuple(sorted(selected)), manifest, tuple(items))


@dataclass(frozen=True)
class Difference:
    path: str
    left: str | None
    right: str | None
    status: str


def compare(
    left: Installation,
    right: Installation | Path,
    categories: Iterable[str] | None = None,
    *,
    cancel: Cancel = None,
) -> tuple[Difference, ...]:
    manifest = None if isinstance(right, Installation) else inspect(right)
    selected = selection(
        categories if categories is not None else manifest.categories if manifest else DEFAULTS
    )
    a = hashes(left, selected, cancel)
    if isinstance(right, Installation):
        b = hashes(right, selected, cancel)
    else:
        assert manifest is not None
        b = {e.path: e.sha256 for e in manifest.entries if e.category in selected}
    names = sorted(a.keys() | b.keys())
    groups: dict[str, set[str]] = defaultdict(set)
    for name in names:
        groups[folded(name)].add(name)
    differences = []
    for name in names:
        if len(groups[folded(name)]) > 1:
            status = "case conflict"
        elif name not in a:
            status = "right only"
        elif name not in b:
            status = "left only"
        elif a[name] != b[name]:
            status = "changed"
        else:
            status = "identical"
        differences.append(Difference(name, a.get(name), b.get(name), status))
    return tuple(differences)
