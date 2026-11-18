"""Plan the user's local recordings, then execute the corrected assignments in a worker."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from music.audio import FORMATS, _audio, _digest, store_track
from music.catalogue import LOCAL, SCENES, Game, catalog, identify, library_root, normalized
from music.tags import tags
from state_lock import locked


@dataclass(frozen=True)
class ImportItem:
    path: Path
    game: str | None = None
    scene: str = "duel"
    confidence: str = "low"
    duplicate: bool = False
    digest: str = ""
    error: str = ""
    duplicate_of: Path | None = None
    in_library: bool = False


@dataclass(frozen=True)
class ImportResult:
    path: Path
    status: str
    reason: str = ""
    destination: Path | None = None


def _inputs(paths: Iterable[Path]) -> Iterator[tuple[Path, tuple[str, ...], str]]:
    for source in paths:
        if not source.is_dir():
            yield source, tuple(p.name for p in source.absolute().parents if p.name), ""
            continue
        root = source.resolve()
        errors: list[OSError] = []
        for folder, dirs, files in os.walk(root, onerror=errors.append):
            base = Path(folder)
            # os.walk can follow Windows junctions even with followlinks=False.
            dirs[:] = sorted(
                name
                for name in dirs
                if not name.startswith(".") and (base / name).resolve() == base / name
            )
            context = (*reversed(base.relative_to(root).parts), root.name)
            for name in sorted(files):
                if name.startswith(".") or name == "game.json":
                    continue
                path = base / name
                yield (
                    path,
                    context,
                    ""
                    if path.resolve().is_relative_to(root)
                    else "Link outside the selected folder.",
                )
        for exc in errors:
            yield Path(exc.filename or source), (), str(exc)


_SCENE_WORDS = {
    "advantage": ("advantage", "winning duel"),
    "disadvantage": ("disadvantage", "losing duel"),
    "win": ("win", "victory", "won"),
    "lose": ("lose", "loss", "defeat"),
    "deck": ("deck", "construction", "card trunk"),
    "menu": ("menu", "title screen"),
    "duel": ("duel", "battle", "boss"),
}


def _suggest_scene(hints: Iterable[str]) -> str:
    for hint in hints:
        plain = " " + normalized(hint) + " "
        for scene, words in _SCENE_WORDS.items():
            if any(f" {word} " in plain for word in words):
                return scene
    return "duel"


def _suggest_game(hints: Iterable[str], games: dict[str, Game]) -> tuple[str | None, str]:
    for hint in hints:
        key = identify(hint, games)
        if key != LOCAL:
            return key, "high" if normalized(hint) in {
                normalized(x) for x in (games[key].title, *games[key].aliases)
            } else "medium"
    return None, "low"


def _index(
    root: Path, games: dict[str, Game], checkpoint: Callable[[], None] | None = None
) -> dict[str, Path]:
    result = {}
    for key in games:
        for scene in SCENES:
            for path in _audio(root / key / scene):
                if checkpoint:
                    checkpoint()
                try:
                    result.setdefault(_digest(path), path)
                except OSError:
                    # An inaccessible file cannot establish that a readable source is a duplicate.
                    continue
    return result


def plan_import(
    sources: Path | Iterable[Path],
    *,
    root: Path | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> list[ImportItem]:
    """Read only. Use dataclasses.replace to confirm/correct game and scene, or omit a row."""
    root = root or library_root()
    games = catalog(root=root)
    known = _index(root, games, checkpoint)
    existing = set(known)
    seen = set()
    plan = []
    for path, folders, error in _inputs([sources] if isinstance(sources, Path) else sources):
        if checkpoint:
            checkpoint()
        if path.absolute() in seen:
            continue
        seen.add(path.absolute())
        if error or path.suffix.lower() not in FORMATS:
            plan.append(ImportItem(path, error=error))
            continue
        try:
            hints = tags(path)
            key, confidence = _suggest_game([path.stem, *hints, *folders], games)
            for index, folder in enumerate(folders[:-1]):
                if folder.casefold() in SCENES and folders[index + 1].casefold() in games:
                    key, confidence = folders[index + 1].casefold(), "high"
                    break
            # An explicit scene directory takes precedence over generic cue names.
            scene = next((f.casefold() for f in folders if f.casefold() in SCENES), None)
            scene = scene or _suggest_scene([path.stem, *hints, *folders])
            digest = _digest(path)
            plan.append(
                ImportItem(
                    path,
                    key,
                    scene,
                    confidence,
                    digest in known,
                    digest,
                    duplicate_of=known.get(digest),
                    in_library=digest in existing,
                )
            )
            known.setdefault(digest, path)
        except (ValueError, OSError) as exc:
            plan.append(ImportItem(path, error=str(exc)))
    return plan


def execute_import(
    items: Iterable[ImportItem],
    *,
    root: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    dedup_library: bool = True,
    cancelled: Callable[[], bool] | None = None,
) -> list[ImportResult]:
    """Per-file outcomes. Recheck contents and duplicates at confirmation time."""
    root = root or library_root()
    plan = list(items)
    if not plan:
        return []
    # Serialize confirmation-time duplicate checks across album/scene destinations.
    with locked(root, ".import.lock"):
        known = _index(root, catalog(root=root)) if dedup_library else None
        results = []
        for item in plan:
            if cancelled and cancelled():
                break
            try:
                result = _import_one(item, root, known)
            except Exception as exc:
                result = ImportResult(item.path, "failed", str(exc) or type(exc).__name__)
            results.append(result)
            if progress:
                progress(len(results), len(plan))
        return results


def _import_one(item: ImportItem, root: Path, known: dict[str, Path] | None) -> ImportResult:
    if item.error:
        raise ValueError(item.error)
    if item.path.suffix.lower() not in FORMATS:
        return ImportResult(item.path, "unsupported", "Unsupported audio extension.")
    digest = _digest(item.path)
    if item.digest and digest != item.digest:
        raise ValueError("The file changed after planning. Scan it again.")
    existing = known.get(digest) if known is not None else None
    if existing and existing.is_file() and _digest(existing) == digest:
        return ImportResult(item.path, "duplicate", destination=existing)
    if item.game is None:
        raise ValueError("Choose a game for this recording.")
    out, added = store_track(item.path, item.game, item.scene, root=root, expected_digest=digest)
    if known is not None:
        known[digest] = out
    return ImportResult(item.path, "imported" if added else "duplicate", destination=out)
