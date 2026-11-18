# Portable backups and device transfer

The `backup` package works offline. The caller supplies the game and Studio roots. For a Qt page, run the calls on a worker thread and forward progress and results to the UI.

## Public API

| Call | Result |
|---|---|
| `Installation(game, data)` or `Installation.at(game, config=None)` | Two filesystem roots. With a config path, Studio data lives in its parent. Omitting it selects the existing per-user data directory. |
| `inventory(install, *, cancel=None)` | Category names mapped to `Total(count, bytes)`. Reads bounded recipes, sleeve catalogues, preferences, repository definitions and soundtrack lists. Counts media by file size. |
| `create(install, destination, categories=DEFAULTS, *, progress=None, cancel=None)` | Creates one archive at a new path and returns its `Manifest`. The destination parent must exist. `DEFAULTS` selects decks and replays. |
| `inspect(path)` | Validates the container, paths and manifest. Returns metadata without extracting files. |
| `plan_restore(path, install, policy="keep_mine", *, categories=None, cancel=None)` | Returns an immutable `Plan`. The default selects the categories recorded in the archive. Every selected source has an item with its destination, status, action and collision detail. File conflicts include the existing path and hash. Preference conflicts include both values. |
| `execute(plan, *, progress=None, cancel=None, require_closed=False)` | Rechecks the preview under OS locks, verifies every archive member including skipped members, stages before/after images, then publishes. Returns a `Transaction(id, status)`. |
| `transactions(install)` | Lists local recovery and undo records for this installation and Studio data directory, with last update times and file counts. |
| `preview_undo(install, id, *, cancel=None, require_closed=False)` | Checks the actual destinations and recovery images on a worker. Returns `UndoPreview(available, reason, target)`. A blocker can include a scoped file path. Undo repeats the checks when it runs. |
| `recover(install, id, "finish" or "rollback", *, progress=None, cancel=None, require_closed=False)` | Resumes a pending restore or undoes a pending/completed restore. Both operations are repeatable. The archive can be disconnected after publication starts. |
| `compare(left_install, right_install_or_archive, categories=None, *, cancel=None)` | Returns paths, left/right hashes and statuses for identical, changed, one-sided and case-colliding files. Defaults to decks and replays between installs, or the archive selection. For an archive, comparison uses the manifest hashes. |

Paths are `pathlib.Path` objects. `progress(done, total, path)` reports completed files during verification and publication. Supply `threading.Event.is_set` for `cancel`. Calls raise `Cancelled`, `ValueError` for invalid data or a stale preview, or `OSError` for filesystem, space, locking and game-process problems. After a publication error, call `transactions` and show any pending recovery. A stopped verification/staging pass can be retried directly.

`create` reads back each saved member and verifies its digest before publishing. Its progress total counts the copy and verification passes. Studio passes `require_closed=True` to check for EDOPro before publishing any category. A running game raises `GameRunning`, an `OSError` subclass.

## Selection

| Category | Portable payload |
|---|---|
| `decks` | Nested `deck/**/*.ydk`. |
| `replays` | Nested `replay/**/*.yrp` and `*.yrpX`, including rolling last replays. |
| `puzzles` | Loose personal Lua files under `puzzles/`. Excludes the Canon collection, configured repositories, hidden directories and Git checkouts. |
| `screenshots` | PNG, JPEG, BMP and WebP under `screenshots/`. Off by default. |
| `studio` | Saved theme recipes with their referenced wallpapers. Sleeve catalogues, rendered prints and editable originals, including the recipes for designed sleeves. Personal album names and a soundtrack list combining relative recording paths from all seven scenes with a previously imported list. |
| `preferences` | One UTF-8 value per supported key, listed below. Inventory counts keys and their encoded bytes. |
| `audio` | Personal album titles (`soundtracks/user<id>/game.json`) and FLAC, Ogg, MP3 and WAV recordings. The seven scene folders are menu, deck, duel, advantage, disadvantage, win and lose. Selecting audio alone also transfers album titles. |

The Save backup page offers the soundtrack list. To move recordings from the UI, open Music → Add music → Export library. Replay notes and saved sound sets stay in the source profile. Sleeve catalogues retain their existing installation-specific assignment records, which can contain paths from the source computer.

Creation verifies each referenced photo, sleeve print and sleeve original against its content-addressed filename. A missing or changed dependency stops publication. Format 1 still accepts earlier deck/duel soundtrack lists and recordings. When an older archive lacks a referenced image, restore can use a matching local copy. An unresolved reference stops the restore before any files are published.

The game executable supplies its version on Windows. A local macOS application plist can also supply it. Other installations may report an unknown game version. Game pictures, databases, card scripts, cores, binary files, active playlists, prior undo records and device configuration are outside the category allow-list.

### Portable preferences

| Key | Reason to transfer |
|---|---|
| `nickname` | The player's display name travels with their identity. |
| `hideHandsInReplays` | Replay hand visibility is a viewing preference. |
| `mute_opponent` | Opponent chat muting is the player's communication choice. |
| `mute_spectators` | Spectator chat muting is a separate communication choice. |
| `autochain` | Automatic chain handling follows the player's duel interaction preference. |

These keys are present in the measured Windows installation. The nickname is a UTF-8 string and the four toggles serialize as 0 or 1. [EDOPro 41.0.2 option definitions](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.inl) and [configuration reader and writer](https://github.com/edo9300/edopro/blob/41.0.2/gframe/game_config.cpp).

## Restore and recovery

- `keep_mine` skips conflicts. `take_theirs` replaces the previewed file and saves its original bytes for undo. `keep_both` chooses an available sibling such as `Example (incoming 2).ydk`. A retained theme receives a separate 32-digit hexadecimal library ID, which the preview and recovery journal both record. A theme already carrying the old `(incoming)` suffix in an archive also receives a valid ID on restore. Identical files retain their timestamps.
- Every path component is compared using Unicode normalization and case folding on every platform. The preview shows spelling changes. When multiple source names would map to one name, it proposes unique destinations. When existing siblings already differ only by case, it preserves them and proposes a distinct incoming name. Ambiguous top-level category folders require a local resolution first. Windows-illegal or non-portable names cause a validation error.
- Preference edits preserve other settings, spacing, BOM and line endings. If a key occurs more than once, only its last value is edited. With `keep_both`, conflicting incoming key/value pairs go to `config/system (incoming).conf`. New keys can still merge into the active file. Writing or rolling back the active config requires the game to be closed.
- Journals live in `<Studio data>/restore-journals/<installation hash>/<transaction id>/`. Undo puts back the saved bytes or removes files created by this restore. It checks each current file against the recorded before/after hashes and verifies the recovery images first. A later edit, an unreadable recovery image or a later recipe using an image that undo would remove can block the operation. Empty directories and lock files may remain. Committed transaction images remain available for undo and consume local disk space.
- Restore and recovery acquire the two restore-root locks in path order, then the affected writer locks in the order installation appearance, sleeve collection, soundtrack import. Older archives can also require the card-art fetch or wallpaper-pack media lock. New backups skip these caches. A busy writer can be retried. Keep EDOPro closed until publication finishes. External editors and engines that do not take these locks can still modify files independently.
- A process exit before journal publication leaves the game untouched. Retrying cleans up its abandoned staging directory. An exit during publication leaves a pending journal. Recovery validates each image and destination before publishing anything further. The same installation and Studio data paths locate the transaction.
- Archives contain ordinary stored ZIP members. The reader bounds the central directory before opening it, accepts at most 8,192 payload entries, 128 MiB per file, 1,800 MiB of payload and an 8 MiB manifest. Compression, ZIP64, directory entries, special files, links, encrypted members and path aliases are rejected. Large audio selections can exceed these limits. Restore checks available space for staging, publication and a 64 MiB reserve.

## Format 1 and reproducible example

`manifest.json` is UTF-8 JSON. `format_version` is an integer. `source_platform` is Python's platform name. `edopro_version` is a string or null. `app_version` is the Studio release string. `categories` records the selection, including selected empty categories. Each payload file has exactly one entry with its scoped relative path, category, integer byte length and lowercase hexadecimal SHA-256. The manifest itself is the container metadata. Unknown format versions are rejected.

New backups include optional `created_at` and per-entry `modified_at` timestamps in Unix seconds. Older backups and generated entries may have no date. These are display metadata. Restore does not use them to choose a version or set a file's modified time.

The Windows synthetic transfer produced this manifest.

```json
{
  "format_version": 1,
  "source_platform": "win32",
  "edopro_version": null,
  "app_version": "5.5.0",
  "categories": ["decks", "replays"],
  "entries": [{
    "path": "game/deck/GOAT/Example.ydk",
    "category": "decks",
    "size": 23,
    "sha256": "ff29782895a2d4d5647f9eb466ab28bc741ee635bb881ae6833fc8db44319f67"
  }]
}
```

Run from the repository root in PowerShell. The example removes its synthetic installation, archive and journal on exit.

```powershell
@'
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import backup

with TemporaryDirectory(dir=Path.cwd()) as folder:
    root = Path(folder)
    pc, deck = root / "PC", root / "Deck"
    (pc / "deck" / "GOAT").mkdir(parents=True)
    (pc / "deck" / "GOAT" / "Example.ydk").write_bytes(b"#main\n123\n#extra\n!side\n")
    deck.mkdir()
    source = backup.Installation.at(pc, root / "pc-studio" / "config.json")
    target = backup.Installation.at(deck, root / "deck-studio" / "config.json")
    path = root / "transfer.zip"
    backup.create(source, path)
    print(json.dumps(asdict(backup.inspect(path)), indent=2))
    plan = backup.plan_restore(path, target)
    print(plan.items[0].status, plan.items[0].target)
    tx = backup.execute(plan)
    print(tx.status, backup.compare(target, path)[0].status)
    print(backup.recover(target, tx.id, "rollback").status)
'@ | & 'C:\Users\leona\Dev\edopro-agents\venv\Scripts\python.exe' -
```
