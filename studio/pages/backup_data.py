"""Portable backup labels and review data. All filesystem reads belong on a worker."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import backup
import config
from backup.core import Cancel, check
from backup.inventory import CONF, INDEX

CATEGORIES = {
    "decks": ("Decks", "Your saved decks and deck folders.", "deck"),
    "replays": ("Replays", "Saved duels you can watch again.", "cards"),
    "puzzles": ("Puzzles", "Your personal puzzles.", "spark"),
    "screenshots": ("Screenshots", "Pictures you took in EDOPro.", "library"),
    "studio": (
        f"{config.APP_NAME} collection",
        "Saved sleeves and themes with their editable originals, plus album names and a list of soundtrack files.",
        "skins",
    ),
    "preferences": (
        "Game preferences",
        "Your name in duels, chat, replay, and chain choices.",
        "settings",
    ),
    "audio": ("Soundtrack files", "Your imported music recordings.", "music"),
}
SAVE_CATEGORIES = tuple(key for key in CATEGORIES if key != "audio")
PREFERENCES = {
    "nickname": "Name in duels",
    "hideHandsInReplays": "Hide hands in replays",
    "mute_opponent": "Mute opponent chat",
    "mute_spectators": "Mute spectator chat",
    "autochain": "Automatic chains",
}
PREFIXES = {
    "game/deck": "Decks",
    "game/replay": "Replays",
    "game/puzzles": "Puzzles",
    "game/screenshots": "Screenshots",
    "studio/sleeves": "Sleeves",
    "studio/themes": "Themes",
    "studio/soundtracks": "Soundtracks",
}
PLATFORMS = {"win32": "Windows", "linux": "Linux or SteamOS", "darwin": "macOS"}
RESTORE_GROUPS = {"conflict": "Conflicts", "add": "New items", "identical": "Already identical"}
COMPARE_GROUPS = {
    "changed": "Changed",
    "left only": "Only here",
    "right only": "Backup only",
    "case conflict": "Name clashes",
    "identical": "Identical",
}
Mode = Literal["finish", "rollback"]


@dataclass(frozen=True)
class Row:
    title: str
    detail: str
    action: str = ""
    names: str = ""

    @property
    def tooltip(self) -> str:
        return "\n".join(filter(None, (self.title, self.detail, self.action, self.names)))


@dataclass(frozen=True)
class Review:
    groups: dict[str, list[Row]]
    writes: int
    case_clashes: int
    preference_conflicts: bool


def size_text(size: int) -> str:
    for factor, unit in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
        if size >= factor:
            return f"{size / factor:,.1f} {unit}"
    return f"{size:,} B"


def date_text(stamp: int | None, *, with_time: bool = False) -> str:
    if stamp is None:
        return "date unavailable"
    try:
        date = datetime.fromtimestamp(stamp, UTC).astimezone()
    except (OSError, OverflowError, ValueError):
        return "date unavailable"
    day = f"{date.day} {date:%b %Y}"
    return f"{day} at {date:%H:%M:%S}" if with_time else day


def totals(manifest: backup.Manifest) -> dict[str, backup.Total]:
    counts = Counter(entry.category for entry in manifest.entries)
    sizes: Counter[str] = Counter()
    for entry in manifest.entries:
        sizes[entry.category] += entry.size
    return {
        key: backup.Total(counts[key], sizes[key])
        for key in CATEGORIES
        if key in manifest.categories
    }


def amount(total: backup.Total, singular: str = "item") -> str:
    unit = singular if total.count == 1 else f"{singular}s"
    return f"{total.count:,} {unit}  ·  {size_text(total.bytes)}"


def display_name(path: str) -> str:
    if path.startswith("preferences/"):
        return f"Game preferences / {PREFERENCES[path.split('/')[1]]}"
    if path == INDEX:
        return "Soundtrack list"
    if path == CONF:
        return "Game preferences"
    prefix, _, relative = path.rpartition("/")
    root = "/".join(path.split("/")[:2])
    if root not in PREFIXES:
        return PurePosixPath(path).name
    pretty = relative if prefix == root else path[len(root) + 1 :]
    return f"{PREFIXES[root]} / {PurePosixPath(pretty).with_suffix('')}"


def check_destination(path: Path, installation: backup.Installation) -> Path:
    path = path.parent.resolve(strict=True) / path.name
    if path.is_relative_to(installation.game):
        raise ValueError(
            "Save the backup outside your EDOPro folder, such as in Documents or on a USB drive."
        )
    return path


def history(install: backup.Installation) -> tuple[backup.Transaction, ...]:
    return tuple(
        sorted(backup.transactions(install), key=lambda entry: entry.updated_at or 0, reverse=True)
    )


def is_interrupted(tx: backup.Transaction) -> bool:
    return tx.status in {"pending", "rolling_back"}


def undo_blocked_text(preview: backup.UndoPreview) -> str:
    reason = {
        "Destination changed since preview": "A file was edited after the restore.",
        "Restore journal checksum mismatch": "A recovery file is missing or damaged.",
    }.get(preview.reason, preview.reason)
    return f"Undo unavailable. {reason}"


def version_text(size: int, stamp: int | None) -> str:
    return f"{size:,} B, {date_text(stamp)}"


def collision_note(item: backup.Item) -> str:
    if not item.detail:
        return ""
    if item.detail == "incoming names collide":
        return "Two items in the backup would get the same name here"
    if item.detail.startswith("ambiguous destination names: "):
        names = item.detail.removeprefix("ambiguous destination names: ")
        return f"Several names here match the backup name ({names})"
    if item.existing_path and item.existing_path != item.source:
        name = display_name(item.existing_path).split(" / ", 1)[-1]
        return f"Name spelling differs (here: {name})"
    return "Folder or file spelling differs"


def restore_review(plan: backup.Plan, cancel: Cancel = None) -> Review:
    groups: dict[str, list[Row]] = {key: [] for key in RESTORE_GROUPS}
    entries = {entry.path: entry for entry in plan.manifest.entries}
    local: dict[str, tuple[int, int]] = {}
    writes = clashes = 0
    preference_conflicts = False
    for item in plan.items:
        check(cancel)
        entry = entries[item.source]
        case_note = collision_note(item)
        clashes += bool(case_note)
        writes += item.action != "skip"
        action = restore_action(item, entry.category)
        detail = f"Backup · {version_text(entry.size, entry.modified_at)}"
        names = f"Backup file · {item.source.partition('/')[2]}"
        if item.existing_path and item.existing_path != item.source:
            names += f"\nIn this EDOPro folder · {item.existing_path.partition('/')[2]}"
        if item.action != "skip" and item.target != item.source and entry.category != "preferences":
            names += f"\nSaves as · {item.target.partition('/')[2]}"
        if item.status == "conflict":
            if entry.category == "preferences":
                preference_conflicts = True
                if CONF not in local:
                    stat = plan.install.target(CONF).stat()
                    local[CONF] = stat.st_size, int(stat.st_mtime)
                here = version_text(len((item.mine or "").encode()), local[CONF][1])
                detail = f"In this EDOPro folder · {here}   |   {detail}"
                key = item.source.split("/")[1]
                if key != "nickname":
                    values = {"0": "Off", "1": "On"}
                    values_here, values_backup = values[item.mine], values[item.theirs]
                else:
                    values_here, values_backup = item.mine or "(blank)", item.theirs or "(blank)"
                names = (
                    f"Separate copy · {item.target.partition('/')[2]}"
                    if item.action != "skip" and item.target != CONF
                    else ""
                )
                action = (
                    f"{action}  ·  In this EDOPro folder: {values_here} / Backup: {values_backup}"
                )
            elif item.existing_path:
                stat = plan.install.target(item.existing_path).stat()
                here = version_text(stat.st_size, int(stat.st_mtime))
                detail = f"In this EDOPro folder · {here}   |   {detail}"
            else:
                detail = f"Backup name overlaps another name   |   {detail}"
        annotation = "  ·  ".join(filter(None, (case_note, action)))
        groups[item.status].append(Row(display_name(item.source), detail, annotation, names))
    return Review(groups, writes, clashes, preference_conflicts)


def restore_action(item: backup.Item, category: str) -> str:
    if item.action == "skip":
        return "Keep the current version" if item.status == "conflict" else "Leave as is"
    if category == "preferences":
        return (
            "Update game preferences"
            if item.target == CONF
            else "Save the backup choice separately"
        )
    if item.existing_path == item.target:
        return "Replace the current version"
    if item.detail or item.existing_path:
        return f"Save as {display_name(item.target).split(' / ', 1)[-1]}"
    return "Add to this installation"


def comparison_rows(differences: tuple[backup.Difference, ...], cancel: Cancel = None) -> Review:
    groups: dict[str, list[Row]] = {key: [] for key in COMPARE_GROUPS}
    descriptions = {
        "changed": "The contents differ.",
        "left only": "On this installation only.",
        "right only": "In the backup only.",
        "identical": "The contents match.",
    }
    for entry in differences:
        check(cancel)
        detail = descriptions.get(entry.status)
        if detail is None:
            side = (
                "In both copies"
                if entry.left and entry.right
                else "On this installation"
                if entry.left
                else "In the backup"
            )
            detail = f"{side} · Name differs in letter case or spelling."
        groups[entry.status].append(
            Row(display_name(entry.path), detail, names=entry.path.partition("/")[2])
        )
    return Review(groups, 0, len(groups["case conflict"]), False)


def error_text(error: Exception, fallback: str) -> tuple[str, str]:
    message = str(error)
    if isinstance(error, backup.Cancelled):
        title = (
            "Backup cancelled" if fallback == "Couldn’t save your backup" else "Operation cancelled"
        )
        return title, "You can start again when you’re ready."
    if isinstance(error, FileExistsError):
        return "That name is already in use", "Choose a new filename. Your existing backup is safe."
    if isinstance(error, FileNotFoundError):
        return fallback, "Check that the folder or backup file is still available and try again."
    if message == "Unsupported backup format version":
        return (
            "This backup needs another version",
            f"It may have been saved by a newer version of {config.APP_NAME}. "
            "Update the app and try opening the backup again.",
        )
    if "preview is stale" in message:
        return (
            "The restore preview is out of date",
            "The backup or this installation changed. Review a fresh preview before restoring.",
        )
    if "Backup changed" in message:
        return "The backup changed", "Open it again to review the current version."
    if "Destination changed" in message:
        return (
            fallback,
            "A file changed after the preview or restore. The app protects your newer edits.",
        )
    if "space" in message.lower() or getattr(error, "errno", None) == 28:
        return fallback, "Free up some space on the drive and try again."
    if message.startswith("Save the backup outside"):
        return fallback, message
    if "archive size limit" in message or "Too many personal files" in message:
        return (
            fallback,
            "There are too many files or an item is too large. Try selecting fewer categories.",
        )
    if "puzzle repository" in message.lower():
        return (
            fallback,
            "Check the puzzle folders and repository settings in EDOPro, then try again.",
        )
    if isinstance(error, OSError):
        return fallback, "Check that you can access the folder and that the drive is connected."
    return (
        fallback,
        "The app couldn’t read the files it expected. Check the backup or choose a different file.",
    )
