"""Extend the appearance effects journal with deletions and an original snapshot."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import extras
from state_lock import locked

from .audio import decode_clip
from .catalogue import cue_filename, event_for, event_key_for_path
from .sets import SoundSet, chant_candidates

Signature = str | None
Changes = dict[str, list[Signature]]


@dataclass
class _History:
    original: dict[str, Signature] = field(default_factory=dict)
    current: dict[str, Signature] = field(default_factory=dict)
    undo: Changes = field(default_factory=dict)


def _signature(data: bytes | None) -> Signature:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _target(root: Path, name: str) -> Path:
    return extras._group_target(root, "effects", name)


def _current(root: Path, name: str) -> Signature:
    return extras._signature(_target(root, name))


def _paths(key: str) -> Iterable[str]:
    return ("sound/" + cue_filename(key, format) for format in event_for(key).formats)


def _valid_digest(value: object) -> bool:
    return value is None or (isinstance(value, str) and extras._SHA256.fullmatch(value) is not None)


def _validate_paths(mapping: object) -> None:
    if not isinstance(mapping, dict):
        raise ValueError("The saved effects record is damaged.")
    for name in mapping:
        event_key_for_path(name)


def _read_record(path: Path) -> tuple[_History, Changes, str]:
    record = json.loads(path.read_bytes())
    if not isinstance(record, dict) or record.get("version") != 1:
        raise ValueError("The saved effects record has an unknown version.")
    original, current, undo, changes, action = (
        record.get(key) for key in ("original", "current", "undo", "changes", "action")
    )
    for mapping in (original, current, undo, changes):
        _validate_paths(mapping)
    if (
        original.keys() != current.keys()
        or not all(_valid_digest(value) for value in (*original.values(), *current.values()))
        or action not in ("apply", "undo", "restore", "migrate")
    ):
        raise ValueError("The saved effects record is damaged.")
    for mapping in (undo, changes):
        for name, pair in mapping.items():
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(map(_valid_digest, pair))
                or pair[0] == pair[1]
                or name not in current
                or current[name] != pair[1]
            ):
                raise ValueError("The saved effects record is damaged.")
    return _History(original, current, undo), changes, action


def _write_record(path: Path, history: _History, changes: Changes, action: str) -> None:
    extras._atomic_write(
        path,
        json.dumps(
            {
                "version": 1,
                "original": history.original,
                "current": history.current,
                "undo": history.undo,
                "changes": changes,
                "action": action,
            },
            sort_keys=True,
        ).encode("utf-8"),
    )


def _check_current(root: Path, expected: dict[str, Signature]) -> None:
    for name, digest in expected.items():
        if _current(root, name) != digest:
            raise ValueError(
                f"{name} changed after installation. Its current contents have been kept."
            )


def _check_chants(root: Path, names: Iterable[str]) -> None:
    chant_keys = {event_key_for_path(name) for name in names if name.count("/") == 2}
    if not chant_keys:
        return
    for key, path in chant_candidates(root / "sound"):
        if key in chant_keys and path.name != Path(cue_filename(key, path.suffix[1:].lower())).name:
            raise ValueError(
                f"{path.relative_to(root).as_posix()} is a nonstandard chant name. "
                "Rename it to its card passcode and a lowercase audio extension before applying."
            )


def _backup_path(backups: Path, name: str, digest: str) -> Path:
    return extras._regular_path(backups, Path(digest[:16]) / name)


def _save_backup(root: Path, backups: Path, name: str, digest: str) -> None:
    backup = _backup_path(backups, name, digest)
    if not backup.exists():
        previous = _target(root, name).read_bytes()
        if _signature(previous) != digest:
            raise OSError(f"{name} changed while backing up. Please retry.")
        extras._atomic_write(backup, previous)
    if extras._signature(backup) != digest:
        raise OSError(f"Could not validate the backup for {name}.")


def _read_backup(backups: Path, name: str, digest: Signature) -> bytes | None:
    if digest is None:
        return None
    backup = _backup_path(backups, name, digest)
    content = backup.read_bytes() if backup.is_file() else None
    if _signature(content) != digest:
        raise OSError(f"The original backup for {name} is missing or has changed.")
    return content


def _has_record(state: Path, name: str) -> bool:
    return extras._regular_path(state, name).is_file()


def _migrate(root: Path, backups: Path, state: Path, history_path: Path) -> tuple[_History, bool]:
    """Recover an old journal and retain the earliest state its records can prove."""
    rolled_back_apply = False
    last = extras._regular_path(state, "last.json")
    restoring = extras._regular_path(state, "restoring.json")
    installing = extras._regular_path(state, "installing.json")
    if not any(path.exists() for path in (last, restoring, installing)):
        return _History(), False
    allowed = extras._group_files(root, "effects")
    if installing.exists():
        changes = extras._read_changes(installing, allowed)
        extras._reverse_group(root, backups, "effects", changes)
        installing.unlink()
        rolled_back_apply = True
    journal = restoring if restoring.exists() else last
    if not journal.exists():
        return _History(), rolled_back_apply
    changes = {
        name: pair
        for name, pair in extras._read_changes(journal, allowed).items()
        if pair[0] != pair[1]
    }
    original = {name: pair[0] for name, pair in changes.items()}
    if journal == restoring:
        extras._reverse_group(root, backups, "effects", changes)
        history = _History(original, original.copy())
        rolled_back_apply = True
    else:
        current = {name: pair[1] for name, pair in changes.items()}
        _check_current(root, current)
        history = _History(original, current, changes)
    for key in {event_key_for_path(name) for name in changes}:
        for name in _paths(key):
            if name not in history.current:
                history.current[name] = history.original[name] = _current(root, name)
    _write_record(history_path, history, {}, "migrate")
    journal.unlink()
    return history, rolled_back_apply


def _recover(root: Path, backups: Path, pending: Path, history: _History) -> str:
    if not pending.exists():
        return ""
    updated, changes, action = _read_record(pending)
    if any(
        name in history.current and history.current[name] != before
        for name, (before, _) in changes.items()
    ):
        raise ValueError("The pending effects change does not match its history.")
    _check_chants(root, updated.current)
    _check_current(
        root, {name: digest for name, digest in updated.current.items() if name not in changes}
    )
    extras._reverse_group(root, backups, "effects", changes)
    pending.unlink()
    return action


def _commit(
    root: Path,
    backups: Path,
    history_path: Path,
    pending: Path,
    history: _History,
    desired: dict[str, bytes | None],
    action: str,
) -> None:
    before = history.current.copy()
    changes = {}
    for name, content in desired.items():
        previous = before[name]
        after = _signature(content)
        if previous != after:
            if previous is not None:
                _save_backup(root, backups, name, previous)
            changes[name] = [previous, after]
    if not changes:
        return
    current = {**before, **{name: pair[1] for name, pair in changes.items()}}
    updated = _History(history.original, current, changes if action != "undo" else {})
    _check_current(root, before)
    _write_record(pending, updated, changes, action)
    try:
        for name, (previous, _) in changes.items():
            target = _target(root, name)
            if extras._signature(target) != previous:
                raise OSError(f"{name} changed during installation. Please retry.")
            if desired[name] is None:
                target.unlink()
            else:
                extras._atomic_write(target, desired[name])
        _check_chants(root, current)
        _check_current(root, current)
        pending.replace(history_path)
    except Exception:
        # Keep the journal if rollback itself cannot safely finish.
        extras._reverse_group(root, backups, "effects", changes)
        pending.unlink()
        raise


def _operate(
    game: Path,
    config_path: str | Path,
    action: str,
    files: dict[str, bytes | None] | None = None,
) -> None:
    root = game.resolve(strict=True)
    if not root.is_dir():
        raise OSError("Choose an EDOPro installation folder.")
    backups = extras._backup_dir(root, config_path)
    state = extras._group_dir(root, config_path, "effects")
    if backups.resolve() != backups or state.resolve() != state:
        raise OSError("The effects backup directory points outside the expected location.")
    lock = extras._regular_path(root, "sound/.edopro-hd-sync.lock")
    with locked(lock.parent, lock.name):
        history_path = extras._regular_path(state, "history.json")
        pending = extras._regular_path(state, "pending.json")
        if history_path.exists():
            history, _, _ = _read_record(history_path)
            recovered_legacy = False
        else:
            history, recovered_legacy = _migrate(root, backups, state, history_path)
        recovered = _recover(root, backups, pending, history)
        if action == "undo" and (recovered == "apply" or recovered_legacy):
            return
        _check_chants(root, [*history.current, *(files or {})])
        _check_current(root, history.current)
        if action == "apply":
            assert files is not None
            changed_keys = set()
            for name, content in files.items():
                if _current(root, name) != _signature(content):
                    changed_keys.add(event_key_for_path(name))
            for key in sorted(changed_keys):
                for name in _paths(key):
                    if name not in history.current:
                        history.current[name] = history.original[name] = _current(root, name)
            desired = {
                name: data
                for name, data in files.items()
                if event_key_for_path(name) in changed_keys
            }
        else:
            originals = (
                history.original
                if action == "restore"
                else {name: pair[0] for name, pair in history.undo.items()}
            )
            desired = {
                name: _read_backup(backups, name, digest)
                for name, digest in originals.items()
                if history.current[name] != digest
            }
            if not desired and action == "undo":
                raise ValueError("There is no previous change to undo for this category.")
        _commit(root, backups, history_path, pending, history, desired, action)


def apply_set(
    game: Path, config_path: str | Path, sound_set: SoundSet, *, events: Iterable[str] | None = None
) -> None:
    """Apply assigned events (or a subset); remove alternate formats for each choice."""
    keys = tuple(sound_set.assignments if events is None else dict.fromkeys(events))
    files: dict[str, bytes | None] = {}
    for key in keys:
        event_for(key)
        if key not in sound_set.assignments:
            continue
        files.update(dict.fromkeys(_paths(key)))
        clip = sound_set.assignments[key]
        if clip is not None:
            decode_clip(clip.data, clip.format)
            files["sound/" + cue_filename(key, clip.format)] = clip.data
    if files:
        _operate(game, config_path, "apply", files)


def change_legacy_effects(
    game: Path, config_path: str | Path, files: dict[str, bytes] | None
) -> None:
    """The Tk gallery historically replaces only the bundled WAV files."""
    if files is None:
        undo(game, config_path)
        return
    if not files or files.keys() - extras._group_files(game, "effects"):
        raise ValueError("The appearance pack contains no valid selection.")
    _operate(game, config_path, "apply", dict(files))


def undo(game: Path, config_path: str | Path) -> None:
    _operate(game, config_path, "undo")


def restore(game: Path, config_path: str | Path) -> None:
    _operate(game, config_path, "restore")


def _available(game: Path, config_path: str | Path, original: bool) -> bool:
    state = extras._group_dir(game.resolve(), config_path, "effects")
    if _has_record(state, "pending.json"):
        return True
    history_path = extras._regular_path(state, "history.json")
    if history_path.is_file():
        try:
            history, _, _ = _read_record(history_path)
        except (OSError, ValueError):
            # Preserve the legacy gallery's ability to surface a damaged record
            # through the action's normal error handler.
            return True
        return history.original != history.current if original else bool(history.undo)
    return any(
        _has_record(state, name) for name in ("last.json", "restoring.json", "installing.json")
    )


def can_undo(game: Path, config_path: str | Path) -> bool:
    return _available(game, config_path, original=False)


def can_restore(game: Path, config_path: str | Path) -> bool:
    return _available(game, config_path, original=True)
