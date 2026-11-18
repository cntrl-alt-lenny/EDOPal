# Installation doctor and replay shelf

The functions in these two packages are synchronous. Run calls that read or write files on a background worker. Both command-line tools are offline. The doctor prints the same privacy-filtered report that the Health check page offers to copy.

## Installation doctor

| API | Result |
|---|---|
| `doctor.inspect_installation(installation, *, config_path=None)` | A `DoctorReport` with the selected folder, EDOPal and game versions, and an ordered tuple of findings. Pass the active config path so the doctor can find that profile's installation-specific undo journals. The default reads the normal per-user EDOPal data folder. |
| `doctor.support_summary(report)` | Paste-ready plaintext. The formatter uses fixed catalogue text and allowlisted integer counts. It omits filenames, paths, deck and skin names, repository URLs, settings, and error messages. |
| `doctor.storage_breakdown(installation, *, data_dir=None, cancelled=None)` | Logical bytes and file counts by category, a total, and paths the scan could not read. Pass the active EDOPal data directory to include its soundtracks and backups. Pass a callback such as `event.is_set`. Cancellation raises `ScanCancelled`. Junctions and symlinks are skipped. |

A `Finding` exposes `id`, `severity`, `title`, `explanation`, `evidence`, and `action`. The Health check page shows problems and warnings first, then collapses notes and passed checks into separate sections. The route table maps applicable findings to the relevant page by ID. Other findings can show instructions for work outside EDOPal. Local evidence may contain private paths and other player data. The report can contain several findings with the same ID, each with different evidence. [Finding catalogue](../doctor/model.py) · [Page routes](../studio/pages/health_report.py)

- `repositories.config` includes configured paths, sources, update flags and available Git revisions. The check uses local files and Git refs. It cannot confirm update freshness or test remote availability.
- `databases.available` includes per-file card counts, distinct IDs and duplicate occurrences across files. Its artwork index count uses the downloader's discovery scope. Deck checks also consider non-delta CDBs in available repositories and their ID mappings. SQLite opens immutable, so a live write-ahead log is outside the scan.
- `banlists.available` includes the source path, name and whitelist flag of each installed list. `windbot.list_conflicts` contains `ListConflict` rows with the deck path, list path and name, card ID and name, copies and limit. This check covers list restrictions. Deck size, room card pools, bot executors and runtime dependencies need separate checks.
- `storage.enough` and `storage.low` include free bytes, the estimated bytes for missing artwork and field art, and the estimated full-library footprint. The estimate budgets 256 KiB per card image and 128 KiB per field image.
- Windows uses PE file-version metadata, a directory access-rights query and the Win32 lock-violation result. macOS bundle version metadata is supported. Standalone Linux binaries may produce an unknown version. The process probe distinguishes running, closed and unknown on Windows, Linux and macOS. On Linux it can also check Wine or Proton. An unknown finding means the available process information did not settle the question.
- The active skin check covers XML, its declared texture references, PNG chunk integrity and JPEG markers. Audio checks count supported nonempty files. Known unchanged malformed stock skins produce an informational finding. Both checks are static.

## Replay shelf

| API | Result |
|---|---|
| `replays.parse_header(data)`, `parse_replay(data)`, `read_replay(path)` | Read a `ReplayHeader` or `ReplayDetails`. A `ReplayFormatError` signals unsupported or damaged data. The header exposes raw client version and flags, UTC timestamp and compression status. Details add player slots by side, duel rule flags and streamed turn count when available. |
| `replays.index_replays(installation, *, annotations=None)` | A tuple of `ReplayEntry` rows. Unreadable or unsupported files keep an error row and any header that could be read. The rolling files have `is_last` set. |
| `replays.search_replays(entries, query="", *, sort="date", descending=None)` | Filter by filename, player names, label, notes or tags. Sort by `date`, `name` or `turns`. Entries missing the chosen sort value appear last. |
| `replays.AnnotationStore(data_dir=None)` | Stores `ReplayNotes(label, notes, tags)` in the EDOPal data folder. Use `get(replay_id)` and `set(replay_id, notes)`. The ID is the SHA-256 of the replay bytes. Renames retain notes and byte-identical copies share them. |
| `replays.find_last_replays(installation)` | Find rolling `.yrp` and `.yrpX` files in the installation's replay folder. |
| `replays.preserve_last_replay(installation, *, source=None, settle_seconds=1.0, expected_id=None)` | Preserve the most recently modified rolling file or the explicitly selected rolling file. Pass the previewed replay’s ID to require that the bytes still match. Returns the archived path, or `None` if the file is absent, unstable, unsupported or no longer matches. Legacy recordings with uncompressed payloads wait until the game closes. |
| `replays.export_replay(entry, destination)` | Create a new ZIP with the original replay bytes and `notes.json`. The archive uses fixed member names. The replay itself can still contain player names and duel information. |

Preservation samples the rolling file twice across the settling interval and uses a staged file for publication. Windows publication uses exclusive rename. Unix publication requires a filesystem that supports hard links. The caller controls when to check and preserve rolling files. A duel that overwrites the source between checks cannot be recovered by the shelf.

The parser follows the [EDOPro 41.0.2 header](https://github.com/edo9300/edopro/blob/41.0.2/gframe/replay.h), [replay reader and writer](https://github.com/edo9300/edopro/blob/41.0.2/gframe/replay.cpp) and [message constants](https://github.com/edo9300/edopro/blob/41.0.2/gframe/common.h). It bounds input size, LZMA output and dictionary size, player counts, and packet traversal. Streamed turn counting walks packet lengths and counts turn markers. Legacy turn counts are unavailable. Player names come from the recorded name slots.

## From source

```text
python -m doctor <installation>
python -m replays --list <installation>
```

Both commands accept `--config <path>`. The replay listing reads annotations only when a config path is supplied. Neither command creates a cache.
