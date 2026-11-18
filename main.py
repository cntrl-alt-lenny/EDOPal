"""
EDOPal - Automatically download missing HD card artwork for EDOPro.

Improvements over the original:
  - Rich progress bars and color-coded console output
  - Automatic retries with exponential backoff on failed downloads
  - Configurable via config.json and CLI arguments
  - Detailed summary report at the end
"""

import asyncio
import contextlib
import json
import ntpath
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Coroutine, Iterable
from datetime import datetime
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import aiohttp

import config as config_module
import extras
from artwork_coverage import JPEG_END as JPEG_END
from artwork_coverage import JPEG_MAGIC as JPEG_MAGIC
from artwork_coverage import MIN_IMAGE_BYTES as MIN_IMAGE_BYTES
from artwork_coverage import PNG_MAGIC as PNG_MAGIC
from artwork_coverage import (
    crop_field_face,
    derived_field,
    field_source,
    mark_field_source,
    scan_artwork_coverage,
)
from artwork_coverage import field_art_dir as _field_art_dir
from artwork_coverage import image_sample as _image_sample
from artwork_coverage import looks_like_image as _looks_like_image
from artwork_coverage import looks_like_jpeg as _looks_like_jpeg
from artwork_flow import STORAGE_MESSAGE, ArtworkStorageError
from artwork_flow import format_size as _format_size
from card_databases import FIELD_SPELL_TYPE as FIELD_SPELL_TYPE
from card_databases import SMALL_DECK_INDEX_LIMIT, ArtworkIndex, get_db_files, read_artwork_index
from config import BUILTIN_MANUAL_MAP, DEFAULTS, Config, app_version, find_edopro_folder
from network_tls import create_http_session as _create_http_session
from state_lock import appearance_lock
from studio.pages.cards_undo import (
    active_writer,
    recover_publication,
    remember_changes,
    restore_artwork,
)

# Rich console setup
# We import rich here so the rest of the file can use `console` everywhere.

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
    )

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def _output_stream():
    """Return a usable output stream, including for windowed builds."""
    return sys.stdout or getattr(sys, "__stdout__", None)


def _stdout_supports_unicode() -> bool:
    """Return True when stdout can encode Rich output safely."""
    stream = _output_stream()
    if stream is None:
        return False
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "\u2500".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


_MARKUP_RE = re.compile(r"\[/?[a-zA-Z0-9_#][a-zA-Z0-9 _#]*\]")


def _strip_markup(value: str) -> str:
    """Remove Rich-style [tag]...[/tag] markup so plain consoles stay readable."""
    return _MARKUP_RE.sub("", value)


class _WindowedOutput:
    """Keep an optional Windows console from interrupting a windowed executable."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, value):
        try:
            return self.stream.write(value)
        except (OSError, ValueError):
            # A launcher may close its inherited console before this process exits.
            return len(value)

    def flush(self):
        with contextlib.suppress(OSError, ValueError):
            self.stream.flush()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return self.stream.encoding


def _console_stream():
    stream = _output_stream()
    if stream is not None and sys.platform == "win32" and getattr(sys, "frozen", False):
        return _WindowedOutput(stream)
    return stream


if RICH_AVAILABLE and _stdout_supports_unicode():
    stream = _console_stream()
    # Rich's legacy Win32 renderer bypasses file.write and assumes a live console.
    console = (
        Console(file=stream, force_terminal=False, legacy_windows=False)
        if isinstance(stream, _WindowedOutput)
        else Console()
    )
else:
    RICH_AVAILABLE = False

    class _FallbackConsole:
        @staticmethod
        def print(*args, **kwargs):
            stream = _console_stream()
            if stream is None:
                return
            kwargs.pop("style", None)
            kwargs.pop("highlight", None)
            stripped = tuple(_strip_markup(arg) if isinstance(arg, str) else arg for arg in args)
            print(*stripped, file=stream, **kwargs)

        @staticmethod
        def rule(title=""):
            stream = _console_stream()
            if stream is not None:
                print(f"\n{'-' * 20} {_strip_markup(title)} {'-' * 20}\n", file=stream)

    console = _FallbackConsole()


VERSION = app_version()


def format_duration(seconds: float) -> str:
    """Render elapsed time in a compact human-friendly format."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def format_rate(count: int, seconds: float) -> str:
    """Render a compact images-per-second value."""
    if count <= 0 or seconds <= 0:
        return "n/a"
    return f"{count / seconds:.1f}/s"


def _truncate(text: str, max_len: int) -> str:
    """Clip long card names so the progress bar stays on one line."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


LATEST_RELEASE_API = "https://api.github.com/repos/cntrl-alt-lenny/edopro-hd-cards/releases/latest"
TEXTURES_RELEASES_API = "https://api.github.com/repos/cntrl-alt-lenny/edopro-hd-cards/releases"
TEXTURES_RELEASE_API = f"{TEXTURES_RELEASES_API}/tags/textures"


def _parse_version(text: str) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple. Unknowns become (0,)."""
    cleaned = text.strip().lstrip("v").split("-", 1)[0]
    parts = cleaned.split(".") if cleaned else []
    numbers: list[int] = []
    for part in parts:
        try:
            numbers.append(int(part))
        except ValueError:
            return (0,)
    return tuple(numbers) if numbers else (0,)


def _update_message(latest: str) -> str:
    return (
        f"[yellow]A newer version ({latest}) is available — "
        "https://github.com/cntrl-alt-lenny/edopro-hd-cards/releases/latest\n"
        "Download it and replace this app with the new one.[/yellow]"
    )


async def notify_if_update_available(
    cfg: Config | None = None, session: aiohttp.ClientSession | None = None
) -> None:
    """Report a newer release if the check succeeds."""
    if getattr(cfg, "update_checked_by_shell", False):
        return
    try:
        if session is None:
            async with _create_http_session() as client:
                latest = await check_for_update(client, VERSION)
        else:
            latest = await check_for_update(session, VERSION)
    except Exception:
        return
    if latest:
        console.print(_update_message(latest))
        if cfg is not None and cfg.notice_sink is not None:
            cfg.notice_sink(f"A newer version ({latest}) is available")


async def check_for_update(
    session: aiohttp.ClientSession, current: str, *, strict: bool = False
) -> str | None:
    """Return the latest release tag if it's newer than `current`, else None.

    Artwork sync ignores a failed check. The Settings screen requests strict
    errors so it can distinguish an unavailable service from a current release.
    """
    if current == "dev":
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with session.get(LATEST_RELEASE_API, timeout=timeout) as resp:
            if resp.status != 200:
                raise OSError(f"GitHub returned HTTP {resp.status}")
            data = await resp.json()
        latest = data.get("tag_name")
        if not isinstance(latest, str) or _parse_version(latest) == (0,):
            raise ValueError("Unrecognized release version")
    except Exception:
        if strict:
            raise
        return None
    if _parse_version(latest) > _parse_version(current):
        return latest
    return None


# Database scanning


def normalize_edopro_path(path: str) -> str:
    """Trim quotes and expand user input into an absolute folder path."""
    cleaned = path.strip().strip('"')
    if sys.platform == "win32" and (
        cleaned.startswith("\\\\") or (len(cleaned) >= 2 and cleaned[1] == ":") or "\\" in cleaned
    ):
        return ntpath.abspath(ntpath.expanduser(cleaned))
    return os.path.abspath(os.path.expanduser(cleaned))


def browse_for_edopro_path_with_powershell(initial_dir: str) -> tuple[str | None, bool]:
    """Open a Windows folder picker via PowerShell when Tk is unavailable."""
    if sys.platform != "win32":
        return None, False

    start_dir = initial_dir if os.path.isdir(initial_dir) else os.path.expanduser("~")
    script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select your EDOPro folder'
$dialog.UseDescriptionForTitle = $true
$dialog.SelectedPath = $env:EDOPRO_INITIAL_DIR
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
""".strip()

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "EDOPRO_INITIAL_DIR": start_dir},
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, False

    if result.returncode != 0:
        return None, False

    selected = result.stdout.strip()
    if not selected:
        return None, True
    return normalize_edopro_path(selected), True


def browse_for_edopro_path(initial_dir: str) -> tuple[str | None, bool]:
    """Open a Windows folder picker. Returns (path, used_dialog)."""
    selected, used_dialog = browse_for_edopro_path_with_powershell(initial_dir)
    if used_dialog:
        return selected, True

    if sys.platform != "win32":
        return None, False
    from desktop_dialogs import console_folder_dialog

    selected, available = console_folder_dialog(initial_dir)
    return normalize_edopro_path(selected) if selected else None, available


def read_edopro_path_from_console() -> str | None:
    """Read an EDOPro folder path from stdin."""
    try:
        entered = input("EDOPro folder path: ").strip()
    except EOFError:
        entered = ""
    if not entered:
        return None
    return normalize_edopro_path(entered)


def detect_edopro_folder(cfg: Config) -> None:
    """Find the EDOPro folder up front so it can be confirmed before any work.

    Skipped when --edopro-path names a folder explicitly: the user has already
    said where it is, so there is nothing to look for and nothing to confirm.
    """
    if cfg.cli.edopro_path is not None:
        return
    found = find_edopro_folder([cfg.discovery_guess()])
    if found:
        cfg.set_edopro_path(found)
        cfg.folder_detected = True


def confirm_edopro_folder(cfg: Config) -> bool:
    """Show the folder that was found and let the user accept or change it.

    Returns False only when the user backs out entirely.
    """
    console.print(f"[green]Found your EDOPro folder:[/green] [bold]{cfg.edopro_path}[/bold]")
    if _prompt_yes_no("Use this folder?", default=True):
        cfg.set_edopro_path(cfg.edopro_path, save=True)
        return True
    return prompt_for_edopro_path(cfg) is not None


def prompt_for_edopro_path(cfg: Config, *, unreadable: list[str] | None = None) -> list[str] | None:
    """Prompt until the user enters a valid EDOPro folder or cancels."""
    checked_path = os.path.abspath(cfg.edopro_path)
    console.print(f"[yellow]No .cdb files found in:[/yellow] [bold]{checked_path}[/bold]")

    if sys.platform == "win32":
        console.print("Select your EDOPro folder in the window that opens. Cancel to quit.")
    else:
        console.print("Enter your EDOPro folder path. Leave it blank to quit.")

    warned_about_fallback = False
    while True:
        candidate: str | None
        if cfg.folder_picker is not None:
            # The options window is driving — ask it to show a folder dialog.
            picked = cfg.folder_picker(cfg.edopro_path)
            if not picked:
                console.print("[yellow]No folder selected. Exiting.[/yellow]")
                return None
            candidate = normalize_edopro_path(picked)
        elif sys.platform == "win32":
            candidate, used_dialog = browse_for_edopro_path(cfg.edopro_path)
            if used_dialog:
                if not candidate:
                    console.print("[yellow]No folder selected. Exiting.[/yellow]")
                    return None
            else:
                if not warned_about_fallback:
                    console.print(
                        "[yellow]Windows folder picker unavailable. "
                        "Enter the path manually instead.[/yellow]"
                    )
                    warned_about_fallback = True
                candidate = read_edopro_path_from_console()
                if not candidate:
                    console.print("[yellow]No folder entered. Exiting.[/yellow]")
                    return None
        else:
            candidate = read_edopro_path_from_console()
            if not candidate:
                console.print("[yellow]No folder entered. Exiting.[/yellow]")
                return None

        if unreadable is not None:
            unreadable.clear()
        dbs = get_db_files(candidate, unreadable=unreadable)
        if dbs:
            saved = cfg.set_edopro_path(candidate, save=True)
            console.print(f"[green]Using EDOPro folder:[/green] [bold]{cfg.edopro_path}[/bold]")
            if not saved:
                console.print(
                    f"[yellow]Could not save settings to "
                    f"{os.path.abspath(cfg.config_path)}.[/yellow]"
                )
            return dbs

        console.print(
            "[yellow]That folder does not look like EDOPro. It needs cards.cdb, "
            "expansions/*.cdb, or repositories/**/*.delta.cdb.[/yellow]"
        )


def scan_databases(
    db_files: list[str], *, unreadable: list[str] | None = None, only_ids: set[int] | None = None
) -> tuple[ArtworkIndex, dict[str, list[int]], set[int], set[int]]:
    return read_artwork_index(
        db_files,
        unreadable=unreadable,
        only_ids=only_ids,
        on_error=lambda message: console.print(f"[yellow]{message}[/yellow]"),
    )


# Name matching


def find_official_match(
    name: str,
    name_to_official: dict[str, list[int]],
    suffixes: list[str],
    quiet: bool = False,
) -> tuple[list[int], bool, bool]:
    """Try to resolve a card name to one or more official Konami IDs.

    Returns (official_ids, is_pre_errata_miss, is_suffix_match).
    is_suffix_match is True when matches came from stripping a GOAT/Pre-Errata
    suffix rather than a direct name lookup.
    """
    pre_errata_miss = False

    for suffix in suffixes:
        if name.endswith(suffix):
            clean = name[: -len(suffix)]
            if clean in name_to_official:
                return name_to_official[clean], False, True
            if "Pre-Errata" in suffix:
                pre_errata_miss = True
                if not quiet:
                    console.print(f'[dim]Pre-Errata lookup miss: "{clean}" (from "{name}")[/dim]')

    return name_to_official.get(name, []), pre_errata_miss, False


def load_manual_map(path: str) -> dict[str, str]:
    """Load built-in overrides, merged with the optional manual_map.json file.

    Keys beginning with "_" are treated as inline documentation and skipped, so
    users can keep comment fields in the JSON without polluting the override map.
    """
    result = dict(BUILTIN_MANUAL_MAP)
    if not os.path.exists(path):
        return result
    try:
        with open(path, encoding="utf-8") as file_obj:
            raw = json.load(file_obj)
    except json.JSONDecodeError as exc:
        console.print(
            f"[yellow]Could not read manual_map.json: {exc}. Using built-in overrides only.[/yellow]"
        )
        return result
    except OSError:
        return result
    if isinstance(raw, dict):
        result.update(
            {k: v for k, v in raw.items() if isinstance(k, str) and not k.startswith("_")}
        )
    return result


# Deck file (.ydk) parsing


def parse_ydk(path: str) -> set[int]:
    """Return the set of card IDs referenced by a .ydk deck file.

    .ydk files are plain text: section headers like `#main`, `#extra`, `!side`
    and then one integer card ID per line. We ignore everything that isn't a
    positive integer so comments and blank lines don't throw things off.
    """
    ids: set[int] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                token = raw.strip()
                if not token or token[0] in "#!":
                    continue
                try:
                    cid = int(token)
                except ValueError:
                    continue
                if cid > 0:
                    ids.add(cid)
    except OSError as exc:
        console.print(f"[yellow]Could not read {path}: {exc}[/yellow]")
    return ids


def prune_orphan_images(pics_path: str, known_ids: set[int], *, cfg: Config | None = None) -> int:
    """Delete pics/<id>.jpg for IDs no longer present in any scanned DB. Returns the count."""
    if not os.path.isdir(pics_path):
        return 0
    if cfg is None:
        cfg = Config(["--edopro-path", str(Path(pics_path).absolute().parent)])
    with remember_changes(cfg) as writer:
        pruned = 0
        try:
            entries = os.listdir(pics_path)
        except OSError as exc:
            raise ArtworkStorageError(str(exc)) from exc
        for entry in entries:
            if not entry.endswith(".jpg"):
                continue
            stem = entry[:-4]
            try:
                cid = int(stem)
            except ValueError:
                continue
            if cid in known_ids:
                continue
            target = os.path.join(pics_path, entry)
            if writer.publish(target, None):
                pruned += 1
        return pruned


def collect_deck_filter_ids(cfg: Config) -> set[int] | None:
    """Gather card IDs from every deck path/folder on `cfg`, or None if unused."""
    folder = cfg.decks_folder
    if not folder and not cfg.deck_paths and getattr(cfg, "my_decks", False):
        folder = os.path.join(cfg.edopro_path, "deck")
    if not cfg.deck_paths and not folder:
        return None
    wanted: set[int] = set()
    paths: list[str] = list(cfg.deck_paths)
    if folder:
        if os.path.isdir(folder):
            # EDOPro nests decks in subfolders, so walk the whole tree.
            for current_root, dirnames, filenames in os.walk(folder):
                dirnames.sort()
                for entry in sorted(filenames):
                    if entry.lower().endswith(".ydk"):
                        paths.append(os.path.join(current_root, entry))
        elif cfg.decks_folder:
            console.print(
                f"[yellow]--decks-folder path is not a directory: {cfg.decks_folder}[/yellow]"
            )
    for path in paths:
        wanted |= parse_ydk(path)
    return wanted


# Persistent failure cache

FAILURE_CACHE_FILENAME = "failed_cards.json"
FIELD_FAILURE_CACHE_FILENAME = "failed_fields.json"
FAILURE_CACHE_TTL_DAYS = 14


def _failure_cache_path(cfg: Config) -> str:
    return os.path.join(os.path.dirname(cfg.config_path), FAILURE_CACHE_FILENAME)


def _field_failure_cache_path(cfg: Config) -> str:
    return os.path.join(os.path.dirname(cfg.config_path), FIELD_FAILURE_CACHE_FILENAME)


def load_failure_cache(
    path: str,
    ttl_days: int = FAILURE_CACHE_TTL_DAYS,
    *,
    contexts: dict[int, str | int] | None = None,
) -> dict[int, float]:
    """Return {card_id: last_checked_epoch} for entries still within the TTL window."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = datetime.now().timestamp() - (ttl_days * 86400)
    fresh: dict[int, float] = {}
    for key, value in raw.items():
        try:
            cid = int(key)
            if isinstance(value, list) and len(value) == 2:
                value, context = value
                if contexts is not None and isinstance(context, (str, int)):
                    contexts[cid] = context
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            fresh[cid] = ts
    return fresh


def save_failure_cache(
    path: str, cache: dict[int, float], *, contexts: dict[int, str | int] | None = None
) -> None:
    """Write the cache atomically; swallow errors so a bad disk never fails a sync."""
    tmp = f"{path}.{uuid4().hex}.part"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    str(cid): [ts, contexts[cid]] if contexts and cid in contexts else ts
                    for cid, ts in cache.items()
                },
                f,
            )
        os.replace(tmp, path)
    except OSError:
        _remove_quietly(tmp)


# Download logic with retries


class DownloadStats:
    """Counters for the current sync run."""

    def __init__(self, rush_ids: set[int] | None = None, token_ids: set[int] | None = None) -> None:
        self.storage_error = ""
        self.success_ids: set[int] = set()
        self.ok_hd = 0
        self.ok_mapped = 0
        self.ok_fallback = 0
        self.official_backup = 0
        self.official_ok = 0
        self.skipped = 0
        self.planned = 0
        self.planned_fields = 0
        self.failed = 0
        self.failed_cards: list[tuple[int, str]] = []
        self.transient_ids: set[int] = set()
        self.field_ok = 0
        self.field_derived = 0
        self.field_local = 0
        self.published_misses: set[int] = set()
        self.token_ids = token_ids or set()
        self.optional_tokens: set[int] = set()
        self.field_failed_cards: list[tuple[int, str]] = []
        self.field_transient_ids: set[int] = set()
        self.unexpected_errors: list[tuple[int, str, str]] = []
        self._rush_ids: set[int] = rush_ids or set()

    @property
    def total_ok(self) -> int:
        return self.ok_hd + self.ok_mapped + self.ok_fallback

    @property
    def transient_failures(self) -> int:
        """Cards that failed because of network trouble, not because art is missing."""
        return len(self.transient_ids)

    @property
    def rush_failures(self) -> int:
        return sum(
            1
            for cid, _ in self.failed_cards
            if cid in self._rush_ids and cid not in self.transient_ids
        )

    @property
    def unofficial_failures(self) -> int:
        return sum(
            1
            for cid, _ in self.failed_cards
            if cid >= 100_000_000 and cid not in self._rush_ids and cid not in self.transient_ids
        )

    @property
    def official_failures(self) -> list[tuple[int, str]]:
        """Official non-Rush, non-token cards that failed — these are actual problems."""
        return [
            (cid, name)
            for cid, name in self.failed_cards
            if cid < 100_000_000 and cid not in self._rush_ids and cid not in self.transient_ids
        ]

    def record_success(self, card_id: int, counter_name: str) -> None:
        self.success_ids.add(card_id)
        setattr(self, counter_name, getattr(self, counter_name) + 1)
        if card_id < 100_000_000:
            self.official_ok += 1
            if counter_name == "ok_fallback":
                self.official_backup += 1

    def record_failure(self, card_id: int, name: str, transient: bool = False) -> None:
        if card_id in self.token_ids and not transient:
            self.optional_tokens.add(card_id)
            return
        self.failed += 1
        self.failed_cards.append((card_id, name))
        if transient:
            self.transient_ids.add(card_id)

    @property
    def field_failed(self) -> int:
        return len(self.field_failed_cards)

    def record_field_failure(self, card_id: int, name: str, transient: bool = False) -> None:
        self.field_failed_cards.append((card_id, name))
        if transient:
            self.field_transient_ids.add(card_id)

    def record_unexpected_error(self, card_id: int, name: str, exc: BaseException) -> None:
        self.unexpected_errors.append((card_id, name, f"{type(exc).__name__}: {exc}"))


MAX_IMAGE_BYTES = 25 * 1024 * 1024


def field_art_dir(cfg: Config) -> str:
    return _field_art_dir(cfg.pics_path)


def has_field_art(cfg: Config, card_id: int) -> bool:
    """True when playmat art exists for this card (EDOPro reads .png or .jpg)."""
    base = field_art_dir(cfg)
    return os.path.exists(os.path.join(base, f"{card_id}.jpg")) or os.path.exists(
        os.path.join(base, f"{card_id}.png")
    )


def find_broken_field_images(field_dir: str, known_ids: set[int]) -> list[int]:
    """Field art IDs whose pics/field image exists but isn't a valid JPEG/PNG."""
    broken: list[int] = []
    seen: set[int] = set()
    if not os.path.isdir(field_dir):
        return broken
    for entry in os.listdir(field_dir):
        stem, _, ext = entry.rpartition(".")
        if ext not in ("jpg", "png"):
            continue
        try:
            cid = int(stem)
        except ValueError:
            continue
        if cid not in known_ids:
            continue
        path = os.path.join(field_dir, entry)
        try:
            if not _looks_like_image(_image_sample(path)) and cid not in seen:
                broken.append(cid)
                seen.add(cid)
        except OSError:
            continue
    return broken


def _present_art_filenames(folder: str, extensions: tuple[str, ...]) -> set[str]:
    """Index a full art folder in one pass, preserving case and symlink semantics."""
    names = set()
    try:
        entries = os.scandir(folder)
    except FileNotFoundError:
        return names
    with entries:
        for entry in entries:
            name = entry.name
            if name.endswith(extensions):
                if entry.is_file(follow_symlinks=False):
                    names.add(name)
                    continue
                canonical = name
            else:
                stem, ext = os.path.splitext(name)
                ext = ext.lower()
                if ext not in extensions:
                    continue
                canonical = stem + ext
            if os.path.exists(os.path.join(folder, canonical)):
                names.add(canonical)
    return names


def find_broken_images(pics_path: str, known_ids: set[int]) -> list[int]:
    """Return card IDs whose pics/<id>.jpg exists but isn't a valid JPEG.

    Used by --repair to catch images that are truncated, empty, or were an error
    page saved as a .jpg before download validation existed.
    """
    broken: list[int] = []
    if not os.path.isdir(pics_path):
        return broken
    for entry in os.listdir(pics_path):
        if not entry.endswith(".jpg"):
            continue
        try:
            cid = int(entry[:-4])
        except ValueError:
            continue
        if cid not in known_ids:
            continue
        path = os.path.join(pics_path, entry)
        try:
            if not _looks_like_jpeg(_image_sample(path)):
                broken.append(cid)
        except OSError:
            continue
    return broken


# When a server replies 429 (rate limited) it may send a Retry-After header
# telling us how many seconds to wait. We honor it, capped so one throttled
# request can't stall the whole sync. Only the seconds form is parsed; the
# rarer HTTP-date form falls back to the normal exponential backoff.
RETRY_AFTER_CAP_SECONDS = 30


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (seconds form) into a capped delay, or None."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, RETRY_AFTER_CAP_SECONDS)


class FetchResult(Enum):
    """Outcome of one download attempt chain.

    MISSING means the server definitively said the image does not exist (404),
    so it's safe to remember the card as unavailable. ERROR covers transient
    trouble — timeouts, connection drops, rate limiting, 5xx —
    which must NOT be cached, or one bad Wi-Fi day would mark hundreds of
    cards "unavailable" for two weeks.
    """

    OK = "ok"
    MISSING = "missing"
    ERROR = "error"


async def _fetch_image(
    session: aiohttp.ClientSession,
    url: str,
    timeout: aiohttp.ClientTimeout,
    max_retries: int,
    is_valid: Callable[[bytes], bool] = _looks_like_jpeg,
) -> bytes | FetchResult:
    """Fetch and validate a bounded response before opening any game file."""
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        retry_after: float | None = None
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    content_length = resp.headers.get("Content-Length")
                    try:
                        if content_length is not None and int(content_length) > MAX_IMAGE_BYTES:
                            return FetchResult.ERROR
                    except (TypeError, ValueError):
                        pass
                    content = bytearray()
                    async for chunk in resp.content.iter_chunked(128 * 1024):
                        if len(content) + len(chunk) > MAX_IMAGE_BYTES:
                            return FetchResult.ERROR
                        content.extend(chunk)
                    if is_valid(content):
                        return bytes(content)
                    if attempt >= total_attempts:
                        # A 200 with a non-image body is usually an error or
                        # anti-bot page, so treat it as transient.
                        return FetchResult.ERROR
                elif resp.status == 404:
                    return FetchResult.MISSING
                elif resp.status == 429:
                    # Rate limited — wait the server's requested cooldown (capped)
                    # before retrying instead of the usual short backoff.
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        except (OSError, aiohttp.ClientError):
            pass

        if attempt < total_attempts:
            delay = retry_after if retry_after is not None else 2 ** (attempt - 1)
            await asyncio.sleep(delay)

    return FetchResult.ERROR


async def _try_download(
    session: aiohttp.ClientSession,
    url: str,
    filepath: str,
    timeout: aiohttp.ClientTimeout,
    max_retries: int,
    is_valid: Callable[[bytes], bool] = _looks_like_jpeg,
    *,
    source: str = "",
) -> FetchResult:
    result = await _fetch_image(session, url, timeout, max_retries, is_valid)
    if isinstance(result, FetchResult):
        return result
    content = mark_field_source(result, source) if source else result
    if active_writer() is not None:
        _write_image(filepath, content)
        return FetchResult.OK
    # The one non-installation caller is the sleeve library's private card-art cache.
    path = Path(filepath)
    if (
        path.parent.name != "card-art"
        or not path.stem.isdecimal()
        or path.suffix != ".jpg"
        or path.resolve() != path.absolute()
    ):
        raise ArtworkStorageError("Saving game artwork requires an installation and restore store.")
    tmp_path = f"{filepath}.{uuid4().hex}.part"
    try:
        with open(tmp_path, "xb") as target:
            target.write(content)
        os.replace(tmp_path, filepath)
    except OSError as exc:
        raise ArtworkStorageError(str(exc)) from exc
    finally:
        _remove_quietly(tmp_path)
    return FetchResult.OK


def _write_image(filepath: str, content: bytes | bytearray) -> None:
    writer = active_writer()
    if writer is None:
        raise ArtworkStorageError("Saving game artwork requires an installation and restore store.")
    writer.publish(filepath, content)


def _remove_quietly(path: str) -> None:
    """Delete a file if present, swallowing any errors."""
    with contextlib.suppress(OSError):
        os.remove(path)


# How a winning source is counted in the summary.
SUCCESS_KIND = {"manual-map": "ok_mapped", "backup": "ok_fallback"}


def build_download_candidates(
    card_id: int,
    official_matches: list[int],
    manual_match: str | None,
    is_pre_errata_miss: bool,
    is_suffix_match: bool,
    sources: dict,
    original_text: bool = False,
) -> list[tuple[str, str]]:
    """Return (reason, url) pairs to try in order; the first success wins.

    Two orderings, and the difference only ever applies to GOAT/Pre-Errata
    entries:

    Normally the base card's HD artwork wins over an era entry's own image,
    which may exist on YGOProDeck at a lower resolution.

    With original_text on, the card's own artwork comes first instead. The
    whole point of a Pre-Errata entry is the wording it was printed with, and
    the base card carries the errata'd text, so borrowing its art makes the
    card state rules it does not have. The substitutes still follow, so a card
    with no artwork of its own anywhere ends up no worse off than before.
    """
    official_source = sources["official"]

    # The card's own artwork, sharpest source first.
    own: list[tuple[str, str]] = [("direct-id", f"{official_source}/{card_id}.jpg")]
    if "backup" in sources:
        own.append(("backup", f"{sources['backup']}/{card_id}.jpg"))

    # Another card's artwork, standing in for this one.
    substitutes: list[tuple[str, str]] = []
    if manual_match:
        substitutes.append(("manual-map", f"{official_source}/{manual_match}.jpg"))
    if is_suffix_match:
        for official_id in official_matches:
            if official_id != card_id and str(official_id) != manual_match:
                substitutes.append(("name-match", f"{official_source}/{official_id}.jpg"))
    if is_pre_errata_miss and card_id >= 10:
        offset_id = card_id - 10
        if not any(url.endswith(f"/{offset_id}.jpg") for _, url in substitutes):
            substitutes.append(("pre-errata-offset", f"{official_source}/{offset_id}.jpg"))

    if original_text and (is_suffix_match or is_pre_errata_miss):
        if card_id == config_module.HISTORICAL_IGNIS_PRINT:
            own.reverse()
        return own + substitutes

    # Default: pinned/base HD art first, then the era ID on the big CDN,
    # and the small community server last of all.
    manual = [c for c in substitutes if c[0] == "manual-map"]
    rest = [c for c in substitutes if c[0] != "manual-map"]
    return manual + rest + own


def is_era_entry(name: str, suffixes: list[str] | tuple[str, ...]) -> bool:
    """True for GOAT / Pre-Errata entries - the cards original_text affects."""
    return name.endswith(tuple(suffixes))


def era_policy(cid: int, original_text: bool) -> str:
    if original_text:
        return "original-v2" if cid == config_module.HISTORICAL_IGNIS_PRINT else "original-v1"
    return "hd-v3" if str(cid) in config_module.CORRECTED_HD_PRINTS else "hd-v2"


async def download_card(
    session: aiohttp.ClientSession,
    card_id: int,
    name: str,
    official_matches: list[int],
    manual_match: str | None,
    is_pre_errata_miss: bool,
    is_suffix_match: bool,
    cfg: Config,
    stats: DownloadStats,
    progress=None,
    task_id=None,
    *,
    replace_existing: bool = False,
) -> None:
    """Download a single card image using a simple waterfall."""
    filepath = os.path.join(cfg.pics_path, f"{card_id}.jpg")

    if (
        not cfg.force
        and not replace_existing
        and card_id not in getattr(cfg, "refresh_era_ids", set())
        and os.path.exists(filepath)
    ):
        stats.skipped += 1
        if progress and task_id is not None:
            progress.advance(task_id)
        return

    timeout = aiohttp.ClientTimeout(total=cfg.timeout)
    candidates = build_download_candidates(
        card_id,
        official_matches,
        manual_match,
        is_pre_errata_miss,
        is_suffix_match,
        cfg.sources,
        cfg.original_text,
    )

    if cfg.dry_run:
        stats.planned += 1
        tag = candidates[0][0] if candidates else "fallback"
        console.print(f"  [dim]Would download:[/dim] {name} ({card_id}) via {tag}")
        if progress and task_id is not None:
            progress.advance(task_id)
        return

    # Only cache the card as unavailable when every source said MISSING; any
    # transient error means it may well exist and should be retried next run.
    saw_transient = False

    with remember_changes(cfg):
        for reason, url in candidates:
            result = await _try_download(session, url, filepath, timeout, cfg.max_retries)
            if result is FetchResult.OK:
                stats.record_success(card_id, SUCCESS_KIND.get(reason, "ok_hd"))
                if progress and task_id is not None:
                    progress.advance(task_id)
                return
            if result is FetchResult.ERROR:
                saw_transient = True

    stats.record_failure(card_id, name, transient=saw_transient)
    if progress and task_id is not None:
        progress.advance(task_id)


def _local_donor(folder: Path, donor: int) -> Path | None:
    for ext in ("jpg", "png"):
        path = folder / f"{donor}.{ext}"
        try:
            sample = _image_sample(str(path))
        except FileNotFoundError:
            continue
        if _looks_like_image(sample) and field_source(sample) != "derived":
            return path
    return None


async def download_field_art(
    session: aiohttp.ClientSession,
    card_id: int,
    name: str,
    cfg: Config,
    stats: DownloadStats,
    progress=None,
    task_id=None,
    *,
    donor: int | None = None,
    layout: str = "card",
) -> None:
    if getattr(cfg, "dry_run", False):
        return
    with remember_changes(cfg) as writer:
        folder = Path(field_art_dir(cfg))
        jpeg = folder / f"{card_id}.jpg"
        timeout = aiohttp.ClientTimeout(total=cfg.timeout)
        transient = False
        installed = None
        for candidate in (card_id, donor):
            if candidate is None:
                continue
            if candidate != card_id and (local := _local_donor(folder, candidate)):
                with local.open("rb") as handle:
                    content = handle.read(MAX_IMAGE_BYTES + 1)
                if len(content) <= MAX_IMAGE_BYTES and _looks_like_image(content):
                    installed = jpeg.with_suffix(local.suffix)
                    _write_image(str(installed), mark_field_source(content, f"local:{candidate}"))
                    stats.field_local += 1
                    break
            for host, ext, validate in (
                ("field", "jpg", _looks_like_jpeg),
                ("field_backup", "png", _looks_like_image),
            ):
                if host not in cfg.sources:
                    continue
                path = jpeg.with_suffix("." + ext)
                result = await _try_download(
                    session,
                    f"{cfg.sources[host]}/{candidate}.{ext}",
                    str(path),
                    timeout,
                    cfg.max_retries,
                    validate,
                    source=f"{host}:{candidate}",
                )
                if result is FetchResult.OK:
                    installed = path
                    break
                transient |= result is FetchResult.ERROR
            if installed:
                break

        if not installed:
            # Leave a published image intact on force/repair, and avoid rewriting
            # a valid crop when the scheduled check finds no higher-quality copy.
            if _local_donor(folder, card_id):
                stats.skipped += 1
            elif derived_field(jpeg) and _looks_like_image(_image_sample(str(jpeg))):
                stats.skipped += 1
                if not transient:
                    stats.published_misses.add(card_id)
            else:
                face = folder.parent / f"{card_id}.jpg"
                if face.is_file():
                    from sleeves.imaging import ensure_gui

                    ensure_gui()
                    crop = await asyncio.to_thread(crop_field_face, face, layout)
                    _write_image(str(jpeg), mark_field_source(crop, "derived"))
                    stats.field_derived += 1
                    installed = jpeg
                    if not transient:
                        stats.published_misses.add(card_id)
                else:
                    stats.record_field_failure(card_id, name, transient)

        if installed:
            stats.field_ok += 1
            sibling = jpeg.with_suffix(".png") if installed == jpeg else jpeg
            # A stale derived JPG can shadow the newly published PNG in the client.
            if sibling.is_file() and (
                derived_field(sibling)
                or (
                    getattr(cfg, "repair", False)
                    and not _looks_like_image(_image_sample(str(sibling)))
                )
            ):
                writer.publish(sibling, None)
        if progress and task_id is not None:
            progress.advance(task_id)


# Curated textures


def _textures_release_api(pack: str | None) -> str:
    """GitHub API URL for a texture pack's release (default pack -> the 'textures' tag)."""
    tag = "textures" if not pack or pack == "default" else f"textures-{pack}"
    return f"{TEXTURES_RELEASES_API}/tags/{tag}"


def _texture_release_is_attributed(release: object) -> bool:
    """Require visible attribution and licensing before downloading textures."""
    if not isinstance(release, dict):
        return False
    body = release.get("body")
    if not isinstance(body, str):
        return False
    normalized = body.casefold()
    return "attribution:" in normalized and "license:" in normalized


async def download_curated_textures(
    session: aiohttp.ClientSession, cfg: Config, pack: str | None = None
) -> tuple[int, int, int]:
    """Download a curated texture pack into <edopro>/textures/.

    Textures aren't on a card database, so they're published as assets on a
    dedicated GitHub release ("textures", or "textures-<pack>" for extra packs).
    Files already on disk are kept (unless --force), so repeat runs are quick.
    Returns (downloaded, skipped, total_available).
    """
    if getattr(cfg, "dry_run", False):
        return (0, 0, 0)
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.get(_textures_release_api(pack), timeout=timeout) as resp:
            if resp.status == 404:
                console.print("[yellow]No curated textures have been published yet.[/yellow]")
                return (0, 0, 0)
            if resp.status != 200:
                console.print(
                    f"[yellow]Could not fetch the curated texture list (HTTP {resp.status}).[/yellow]"
                )
                return (0, 0, 0)
            data = await resp.json()
    except Exception as exc:
        console.print(f"[yellow]Could not fetch the curated texture list: {exc}[/yellow]")
        return (0, 0, 0)

    if not _texture_release_is_attributed(data):
        console.print(
            "[yellow]Skipping curated textures: the pack release has no explicit "
            "Attribution: and License: information.[/yellow]"
        )
        return (0, 0, 0)

    assets = [
        a
        for a in (data or {}).get("assets", [])
        if isinstance(a, dict)
        and isinstance(a.get("name"), str)
        and isinstance(a.get("browser_download_url"), str)
        and a["name"].lower().endswith((".png", ".jpg", ".jpeg"))
    ]
    if not assets:
        console.print("[yellow]No curated textures have been published yet.[/yellow]")
        return (0, 0, 0)

    game = Path(cfg.edopro_path).resolve()
    textures_dir = game / "textures"
    # Textures can be large (multi-MB backgrounds), so allow extra time.
    timeout = aiohttp.ClientTimeout(total=max(cfg.timeout, 120))
    downloaded = 0
    skipped = 0
    force = getattr(cfg, "force", False)
    cancel_event = getattr(cfg, "cancel_event", None)
    with remember_changes(cfg) as writer:
        for asset in assets:
            if cancel_event is not None and cancel_event.is_set():
                break
            name = os.path.basename(asset["name"])  # guard against odd asset names
            try:
                dest = extras._regular_path(game, textures_dir / name)
            except OSError as exc:
                raise ArtworkStorageError(str(exc)) from exc
            if not force and dest.exists():
                skipped += 1
                continue
            result = await _fetch_image(
                session, asset["browser_download_url"], timeout, cfg.max_retries, _looks_like_image
            )
            if isinstance(result, FetchResult):
                console.print(f"  [yellow]could not download {name}[/yellow]")
                continue
            try:
                with appearance_lock(game):
                    dest = extras._regular_path(game, textures_dir / name)
                    if dest.exists() and not force:
                        skipped += 1
                        continue
                    writer.publish(dest, result)
            except OSError as exc:
                raise ArtworkStorageError(str(exc)) from exc
            downloaded += 1
            console.print(f"  [dim]downloaded {name}[/dim]")
    return (downloaded, skipped, len(assets))


async def list_texture_packs(session: aiohttp.ClientSession) -> list[tuple[str, str]]:
    """List texture packs from the 'textures' and 'textures-*' releases.

    Returns [(pack_name, label)]; 'default' is the standard 'textures' release.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.get(f"{TEXTURES_RELEASES_API}?per_page=100", timeout=timeout) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception:
        return []
    packs: list[tuple[str, str]] = []
    for rel in data or []:
        if not isinstance(rel, dict):
            continue
        tag = rel.get("tag_name", "")
        if not isinstance(tag, str):
            continue
        label = rel.get("name") or tag
        if tag == "textures":
            if not _texture_release_is_attributed(rel):
                continue
            packs.append(("default", label))
        elif tag.startswith("textures-"):
            if not _texture_release_is_attributed(rel):
                continue
            packs.append((tag[len("textures-") :], label))
    return packs


async def _resolve_texture_pack(session: aiohttp.ClientSession, cfg: Config) -> str:
    """Pick which texture pack to download: an explicit flag, the only pack, or a prompt."""
    if cfg.textures_pack:
        return cfg.textures_pack
    if cfg.quiet or cfg.dry_run or not getattr(cfg, "interactive_prompts", True):
        return "default"
    packs = await list_texture_packs(session)
    if len(packs) <= 1:
        return "default"
    console.print("\n[bold]Available texture packs:[/bold]")
    for i, (name, label) in enumerate(packs, 1):
        console.print(f"  {i}. {name} [dim]({label})[/dim]")
    while True:
        try:
            raw = input(f"Choose a pack [1-{len(packs)}] (default 1): ").strip()
        except EOFError:
            return packs[0][0]
        if not raw:
            return packs[0][0]
        try:
            idx = int(raw)
        except ValueError:
            idx = 0
        if 1 <= idx <= len(packs):
            return packs[idx - 1][0]
        console.print("[yellow]Please enter a number from the list.[/yellow]")


def _resolve_want_textures(cfg: Config, has_cards: bool) -> bool:
    """Decide whether to download curated textures: explicit flag, or a prompt."""
    if cfg.textures is not None:
        return cfg.textures
    if cfg.dry_run or cfg.quiet:
        return False
    question = (
        "Also download curated textures (custom backgrounds & card sleeves)?"
        if has_cards
        else "Download curated textures (custom backgrounds & card sleeves)?"
    )
    return _prompt_yes_no(question)


# Summary


def _build_summary_rows(
    stats: DownloadStats,
    cfg: Config,
    runtime_seconds: float,
) -> list[tuple[str, str, str | None]]:
    """Build the rows shown in the terminal summary."""
    rows: list[tuple[str, str, str | None]] = []
    if cfg.dry_run:
        rows.append(("Would download", f"{stats.planned:,}", None))
        if stats.planned_fields:
            rows.append(("Field art to fetch", f"{stats.planned_fields:,}", None))
        if stats.skipped:
            rows.append(("Already on disk", f"{stats.skipped:,}", "dim"))
    else:
        rows.append(("Downloaded", f"{stats.total_ok:,}", "green"))
        if stats.official_backup:
            rows.append(("Backup art", f"{stats.official_backup:,}", "cyan"))
        if stats.field_ok:
            rows.append(("Field art", f"{stats.field_ok:,}", "green"))
        if stats.field_derived:
            rows.append(("Derived from card faces", f"{stats.field_derived:,}", "dim"))
        if stats.field_failed:
            rows.append(("Field art unavailable", f"{stats.field_failed:,}", "yellow"))
        if stats.skipped:
            rows.append(("Already existed", f"{stats.skipped:,}", "dim"))
        if stats.failed:
            rows.append(("Unavailable", f"{stats.failed:,}", "yellow"))
            rush = stats.rush_failures
            unofficial = stats.unofficial_failures
            official = stats.official_failures
            if rush:
                rows.append(("  Rush Duel", f"{rush:,}", "dim"))
            if unofficial:
                rows.append(("  Anime / fan-made", f"{unofficial:,}", "dim"))
            if official:
                rows.append(("  Official", f"{len(official):,}", "dim"))
            if stats.transient_failures:
                rows.append(("  Network errors", f"{stats.transient_failures:,}", "dim"))
        rows.append(("Time", format_duration(runtime_seconds), None))
        rows.append(("Speed", format_rate(stats.total_ok, runtime_seconds), None))
    return rows


def _write_report(stats: DownloadStats, cfg: Config, runtime_seconds: float) -> None:
    """Write a sync report file with grouped failure categories."""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    report_path = os.path.join(
        os.path.dirname(cfg.config_path),
        f"sync-report-{timestamp}.txt",
    )
    try:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"{config_module.APP_NAME} Report\n")
            f.write("=" * 40 + "\n")
            f.write(f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Runtime:   {format_duration(runtime_seconds)}\n")
            f.write(f"Speed:     {format_rate(stats.total_ok, runtime_seconds)}\n")
            f.write(f"\nDownloaded:      {stats.total_ok:,}\n")
            if stats.official_backup:
                f.write(f"Backup art:      {stats.official_backup:,}\n")
            if stats.field_ok:
                f.write(f"Field art:       {stats.field_ok:,}\n")
            if stats.field_derived:
                f.write(f"Derived fields:  {stats.field_derived:,}\n")
            if stats.field_failed:
                f.write(f"Field art n/a:   {stats.field_failed:,}\n")
            if stats.skipped:
                f.write(f"Already existed: {stats.skipped:,}\n")
            f.write(f"Unavailable:     {stats.failed:,}\n")
            f.write(f"Images folder:   {os.path.abspath(cfg.pics_path)}\n")

            if stats.storage_error:
                f.write(f"\n{STORAGE_MESSAGE}\n{stats.storage_error}\n")
            if stats.failed_cards:
                f.write(f"\n{'=' * 40}\n")
                f.write("Unavailable Breakdown\n")
                f.write(f"{'=' * 40}\n")
                rush = stats.rush_failures
                unofficial = stats.unofficial_failures
                official = stats.official_failures
                if rush:
                    f.write(f"  Rush Duel cards:       {rush:,}\n")
                if unofficial:
                    f.write(f"  Anime / fan-made:      {unofficial:,}\n")
                if stats.transient_failures:
                    f.write(f"  Network errors:        {stats.transient_failures:,}")
                    f.write("  (will be retried automatically next run)\n")
                if official:
                    f.write(f"  Official cards:        {len(official):,}\n")
                    f.write("\nOfficial cards that could not be found:\n")
                    f.write("-" * 60 + "\n")
                    for card_id, card_name in official:
                        f.write(f"  {card_id}\t{card_name}\n")
            elif not stats.optional_tokens and not stats.storage_error:
                f.write("\nAll cards downloaded successfully.\n")

            if stats.unexpected_errors:
                f.write(f"\n{'=' * 40}\n")
                f.write("Unexpected Errors\n")
                f.write(f"{'=' * 40}\n")
                for card_id, card_name, detail in stats.unexpected_errors:
                    f.write(f"  {card_id}\t{card_name}\n    {detail}\n")
        cfg.report_path = report_path
        console.print(f"[dim]Report saved to: {report_path}[/dim]")
    except OSError as exc:
        console.print(f"[yellow]Could not write sync report: {exc}[/yellow]")


def print_summary(
    stats: DownloadStats,
    cfg: Config,
    runtime_seconds: float,
    save_report: bool = False,
):
    """Print a clean terminal summary and optionally save a report."""
    rows = _build_summary_rows(stats, cfg, runtime_seconds)
    label_width = max(len(label) for label, _, _ in rows)

    console.print()
    if stats.storage_error:
        console.rule("[bold]Sync stopped[/bold]")
        console.print(STORAGE_MESSAGE)
        console.print(stats.storage_error)
    else:
        console.rule("[bold]Sync Complete[/bold]")
    for label, value, style in rows:
        formatted = f"{value:>12}"
        if style:
            console.print(f"  {label:<{label_width}} [{style}]{formatted}[/{style}]")
        else:
            console.print(f"  {label:<{label_width}} {formatted}")
    console.rule()

    if stats.transient_failures and not cfg.dry_run:
        console.print(
            f"[yellow]{stats.transient_failures:,} download(s) failed due to network problems — "
            "they will be retried automatically on the next run.[/yellow]"
        )

    if stats.unexpected_errors:
        console.print(
            f"[bold red]{len(stats.unexpected_errors)} unexpected error(s) occurred:[/bold red]"
        )
        for _cid, card_name, detail in stats.unexpected_errors[:3]:
            console.print(f"  [red]{card_name}: {detail}[/red]")
        if len(stats.unexpected_errors) > 3:
            console.print("  [dim]...more in the sync report (--save-report)[/dim]")

    if cfg.dry_run:
        return

    if save_report:
        _write_report(stats, cfg, runtime_seconds)


# Main


def _prompt_yes_no(question: str, default: bool = False) -> bool:
    """Prompt for a yes/no answer, returning the default on blank input."""
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {suffix}: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    if answer in {"y", "yes"}:
        return True
    if answer in {"n", "no"}:
        return False
    return default


def run_health_check(cfg: Config) -> bool:
    """Check artwork matching and the assets needed by the packaged windows."""
    suffixes = DEFAULTS["suffixes_to_strip"]
    name_to_official = {
        "Dark Magician": [36996508, 46986414],
        "Blue-Eyes White Dragon": [89631136, 89631139],
    }
    checks: list[tuple[bool, str]] = []

    # Check that suffix stripping resolves Pre-Errata cards to their base
    official_ids, is_pre_errata_miss, is_suffix_match = find_official_match(
        "Dark Magician (Pre-Errata)",
        name_to_official,
        suffixes,
        quiet=True,
    )
    checks.append(
        (
            46986414 in official_ids and not is_pre_errata_miss and is_suffix_match,
            "Dark Magician (Pre-Errata): suffix stripping resolves to base art",
        )
    )

    # Check that GOAT suffix stripping works
    official_ids, is_pre_errata_miss, is_suffix_match = find_official_match(
        "Blue-Eyes White Dragon GOAT",
        name_to_official,
        suffixes,
        quiet=True,
    )
    checks.append(
        (
            89631136 in official_ids and not is_pre_errata_miss and is_suffix_match,
            "Blue-Eyes White Dragon GOAT: suffix stripping resolves to base art",
        )
    )

    # Check that Pre-Errata miss is flagged when base name is absent
    official_ids, is_pre_errata_miss, is_suffix_match = find_official_match(
        "Summoned Skull (Pre-Errata)",
        name_to_official,
        suffixes,
        quiet=True,
    )
    checks.append(
        (
            is_pre_errata_miss and not is_suffix_match,
            "Summoned Skull (Pre-Errata): flags pre-errata miss when base is absent",
        )
    )

    from content.catalogue import bundled_manifest
    from music.catalogue import ART, SCENES, official_games
    from music_library import catalog
    from sleeves import PALETTES, PATTERNS, Recipe
    from themes import PRESETS, Theme

    try:
        games = [
            game.id
            for game in catalog(root=Path(cfg.config_path).parent / "soundtracks").values()
            if game.cover_page
        ]
        music_present = bool(games) and all(
            (ART / size / f"{game}.png").is_file()
            for game in games
            for size in ("", "thumb", "grid", "detail")
        )
    except (OSError, KeyError, ValueError):
        music_present = False
    checks.append((music_present, "Soundtrack catalogue and game covers"))
    try:
        bundled_manifest()
    except ValueError:
        content_present = False
    else:
        content_present = True
    checks.append((content_present, "Bundled content catalogue"))
    checks.append(
        (
            music_present and len(SCENES) == 7 and bool(official_games()),
            "Music scenes and catalogue IDs",
        )
    )
    checks.append(
        (
            bool(PRESETS) and Theme.from_json(PRESETS[0].to_json()) == PRESETS[0],
            "Editable theme preset",
        )
    )
    checks.append(
        (
            bool(PATTERNS)
            and Recipe.from_dict({"pattern": "sun"}).pattern in PATTERNS
            and all(pattern.palette in PALETTES for pattern in PATTERNS.values()),
            "Sleeve pattern catalogue and recipe",
        )
    )
    gallery = extras.PACKS.parent / "window" / "gallery"
    if getattr(sys, "frozen", False):
        try:
            import miniaudio
        except (ImportError, OSError):
            playable = False
        else:
            try:
                playable = miniaudio.get_file_info(str(gallery / "thonk-draw.wav")).num_frames > 0
            except (OSError, RuntimeError, miniaudio.MiniaudioError):
                playable = False
        checks.append((playable, "Built-in soundtrack decoder"))
    checks.append(
        (
            all(
                (gallery / size / f"{skin.lower()}-{view}.png").is_file()
                for skin in extras.SKINS
                for view in ("menu", "deck", "duel", "turn")
                for size in ("", "large")
            ),
            "In-game appearance previews",
        )
    )

    console.print()
    console.rule("[bold]Health Check[/bold]")

    for passed, label in checks:
        prefix = "[green]OK[/green]" if passed else "[red]FAIL[/red]"
        console.print(f"  {prefix} {label}")

    passed = all(ok for ok, _ in checks)
    if passed:
        console.print("\n[bold green]All checks passed.[/bold green]")
    else:
        console.print(
            "\n[bold red]Health check failed — please review the messages above.[/bold red]"
        )
    return passed


def _should_show_gui(cfg: Config) -> bool:
    """Show the tick-box window for plain runs of the packaged app.

    --gui forces it (handy from source), --no-gui suppresses it, and any
    explicit power flag means the user already said what they want.
    """
    if cfg.gui is False:
        return False
    if cfg.gui is True:
        return True
    if not getattr(sys, "frozen", False):
        return False
    if cfg.quiet or cfg.dry_run or cfg.stats or cfg.health_check or cfg.repair or cfg.force:
        return False
    if cfg.deck_paths or cfg.decks_folder or cfg.my_decks or cfg.prune or cfg.recheck_missing:
        return False
    return cfg.textures is None and not cfg.textures_pack


def _run_was_cancelled(cfg: Config) -> bool:
    """True when the window's Cancel button stopped this run part-way."""
    return cfg.cancel_event is not None and cfg.cancel_event.is_set()


def _apply_gui_choices(cfg: Config, choices: dict) -> None:
    """Copy the window's tick-boxes onto the config and mute console questions."""
    cfg.field_art = bool(choices.get("field_art", cfg.field_art))
    cfg.original_text = (
        not bool(choices["hd_era_art"])
        if "hd_era_art" in choices
        else bool(choices.get("original_text", cfg.original_text))
    )
    cfg.my_decks = bool(choices.get("my_decks", False))
    cfg.textures = bool(choices.get("textures", False))
    cfg.theme = bool(choices.get("theme", False))
    cfg.audio = bool(choices.get("audio", False))
    cfg.installed_extras = []
    cfg.repair = bool(choices.get("repair", False))
    cfg.force = bool(choices.get("force", False))
    cfg.save_report = bool(choices.get("save_report", False))
    cfg.stats = bool(choices.get("stats", False))
    # The window already answered everything a console prompt would ask.
    cfg.interactive_prompts = False


def _count_card_images(pics_path: str) -> int:
    """Count downloaded card images; cheap fresh-install detector."""
    if not os.path.isdir(pics_path):
        return 0
    count = 0
    with os.scandir(pics_path) as it:
        for entry in it:
            if entry.name.endswith(".jpg") and entry.is_file():
                count += 1
    return count


def _count_deck_files(folder: str) -> int:
    """Count .ydk files under a deck folder (EDOPro nests them in subfolders)."""
    if not os.path.isdir(folder):
        return 0
    count = 0
    for _root, _dirs, files in os.walk(folder):
        count += sum(1 for f in files if f.lower().endswith(".ydk"))
    return count


def _field_miss_applies(
    pics: Path,
    cid: int,
    donor: int | None,
    checked_donor: str | int | None,
    derived: bool,
    pending_faces: set[int],
) -> bool:
    # Legacy timestamps covered own-ID hosts only. A new donor or a face
    # arriving since the 404 gives this field a source it hasn't exhausted.
    if donor:
        try:
            if donor != checked_donor or _local_donor(pics / "field", donor):
                return False
        except OSError:
            # An unreadable donor cannot substantiate a negative cache hit.
            return False
    return derived or (cid not in pending_faces and not (pics / f"{cid}.jpg").is_file())


def collect_coverage_stats(cfg: Config, id_to_name: dict[int, str], field_ids: set[int]) -> dict:
    """Gather how much artwork is on disk versus what the databases know about."""
    tokens = getattr(id_to_name, "tokens", set())
    cached = load_failure_cache(_failure_cache_path(cfg)).keys() & id_to_name.keys()
    stats = scan_artwork_coverage(
        cfg.pics_path, id_to_name, field_ids, optional_missing=cached & tokens
    )
    donors = getattr(id_to_name, "field_donors", {})
    checked: dict[int, str | int] = {}
    fields = load_failure_cache(_field_failure_cache_path(cfg), contexts=checked)
    pics = Path(cfg.pics_path)
    missing_fields = sum(
        not has_field_art(cfg, cid)
        and _field_miss_applies(pics, cid, donors.get(cid), checked.get(cid), False, set())
        for cid in fields.keys() & field_ids
    )
    return {
        **stats,
        "known_missing": sum(not (pics / f"{cid}.jpg").is_file() for cid in cached - tokens)
        + missing_fields,
        "pics_path": os.path.abspath(cfg.pics_path),
    }


def print_coverage_stats(cfg: Config, id_to_name: dict[int, str], field_ids: set[int]) -> None:
    """Print (or hand to the options window) the artwork coverage summary."""
    data = collect_coverage_stats(cfg, id_to_name, field_ids)
    if cfg.coverage_sink is not None:
        cfg.coverage_sink(data)
        return

    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.1f}%" if whole else "n/a"

    console.print()
    console.rule("[bold]Artwork Coverage[/bold]")
    console.print(f"  Cards indexed   {data['cards_total']:>10,}")
    optional = data["cards_total"] - data["cards_expected"]
    if optional:
        console.print(f"  Optional tokens without art  {optional:,}")
    console.print(
        f"  Card art        {data['cards_have']:>10,}  "
        f"[dim]({pct(data['cards_have'], data['cards_expected'])})[/dim]"
    )
    console.print(
        f"  Field art       {data['fields_have']:>10,}  [dim]of {data['fields_total']:,} "
        f"Field Spells ({pct(data['fields_have'], data['fields_total'])})[/dim]"
    )
    if data["fields_derived"]:
        console.print(f"  Derived fields  {data['fields_derived']:>10,}")
    console.print(f"  Disk usage      {_format_size(data['disk_bytes']):>10}")
    if data["known_missing"]:
        console.print(
            f"  Known missing   {data['known_missing']:>10,}  "
            f"[dim](re-check with --recheck-missing)[/dim]"
        )
    console.rule()
    console.print(f"[dim]Images folder: {data['pics_path']}[/dim]")


class SyncInputError(SystemExit):
    """An actionable stop message for the same CLI and desktop entry points."""

    def __init__(self, message: str):
        self.user_message = message
        console.print(f"[bold red]{message}[/bold red]")
        super().__init__(1)


async def _run_workers(workers: Iterable[Coroutine[Any, Any, None]]) -> None:
    # Drain in-flight workers before releasing the journal on a failed publication.
    async with asyncio.TaskGroup() as group:
        for worker in workers:
            group.create_task(worker)


async def run(cfg: Config) -> DownloadStats | None:
    stats = DownloadStats()
    started_at = perf_counter()
    try:
        with remember_changes(cfg):
            return await _run_sync(cfg, stats)
    except* ArtworkStorageError as errors:
        error = errors
        while isinstance(error, BaseExceptionGroup):
            error = error.exceptions[0]
        stats.storage_error = str(error)
        print_summary(stats, cfg, perf_counter() - started_at, cfg.save_report)
    return stats


async def _run_sync(cfg: Config, stats: DownloadStats) -> DownloadStats | None:
    started_at = perf_counter()
    cfg.report_path = None
    cfg.idle_summary = None

    console.print(f"[bold cyan]{config_module.APP_NAME}[/bold cyan] [dim]v{VERSION}[/dim]")

    if cfg.health_check:
        if not run_health_check(cfg):
            raise SystemExit(1)
        return None

    # The window already shows the folder (Start is the confirmation), so this
    # only runs for console launches.
    if (
        cfg.folder_detected
        and cfg.interactive_prompts
        and not cfg.quiet
        and not confirm_edopro_folder(cfg)
    ):
        return None

    def requested_scope() -> set[int] | None:
        if cfg.stats:
            return None
        ids = collect_deck_filter_ids(cfg)
        if cfg.my_decks and not ids:
            has_decks = cfg.deck_paths or _count_deck_files(
                cfg.decks_folder or os.path.join(cfg.edopro_path, "deck")
            )
            cfg.idle_summary = (
                "No cards found in your decks — nothing to sync"
                if has_decks
                else "No decks found — nothing to sync",
                "No card IDs were found in your saved decks. Save a deck in EDOPro, then try again, or choose Full library.",
            )
            console.print(f"{cfg.idle_summary[0]}. {cfg.idle_summary[1]}")
            if cfg.notice_sink is not None:
                cfg.notice_sink(cfg.idle_summary[0])
        return ids

    deck_filter = requested_scope()
    if cfg.idle_summary:
        return None
    unreadable: list[str] = []
    dbs = get_db_files(cfg.edopro_path, unreadable=unreadable)
    if not dbs:
        dbs = prompt_for_edopro_path(cfg, unreadable=unreadable)
        if not dbs:
            return None
        # The picker can select another installation with a different deck folder.
        if cfg.my_decks:
            deck_filter = requested_scope()
            if cfg.idle_summary:
                return None

    if cfg.dry_run:
        # SQLite's read-only WAL connection may still create -wal and -shm files.
        for database in dbs:
            try:
                with open(database, "rb") as handle:
                    header = handle.read(20)
            except OSError as exc:
                raise SyncInputError(
                    f"Cannot read {Path(database).name} for this preview."
                ) from exc
            if header.startswith(b"SQLite format 3\x00") and 2 in header[18:20]:
                raise SyncInputError(
                    f"Cannot preview {Path(database).name} without writing SQLite journal files."
                )
    elif not cfg.stats:
        recover_publication(cfg)

    era_suffixes = tuple(cfg.suffixes)
    # A receipt retry keeps its original deck scope and only revisits interrupted assets.
    retry_artwork = getattr(cfg, "retry_artwork", None)
    if retry_artwork:
        retry_ids = retry_artwork[0] | retry_artwork[1]
        deck_filter = retry_ids if deck_filter is None else deck_filter & retry_ids
    scoped_index = bool(deck_filter) and len(deck_filter) <= SMALL_DECK_INDEX_LIMIT
    if scoped_index:
        index = scan_databases(dbs, unreadable=unreadable, only_ids=deck_filter)
        # A suffix match may need a base card outside this deck. The full scan
        # also distinguishes missing deck IDs from an empty or corrupt installation.
        scoped_index = (
            bool(index[0])
            and not index[3]
            and not any(is_era_entry(name, era_suffixes) for name in index[0].values())
        )
    if not scoped_index:
        index = scan_databases(dbs, unreadable=unreadable)
    full_id_to_name, name_to_official, rush_ids, field_ids = index
    metadata = full_id_to_name if isinstance(full_id_to_name, ArtworkIndex) else ArtworkIndex()
    manual_map = load_manual_map(cfg.manual_map_file)
    scope = "selected " if scoped_index else ""
    console.print(f"[dim]Indexed {len(full_id_to_name):,} {scope}cards[/dim]")

    has_non_empty_db = any(os.path.isfile(db) and os.path.getsize(db) > 0 for db in dbs)
    if has_non_empty_db and not full_id_to_name and not metadata.excluded:
        raise SyncInputError(
            "The selected databases contain no readable cards. Open EDOPro to update "
            "its card databases, or choose another installation."
        )

    if cfg.stats:
        print_coverage_stats(cfg, full_id_to_name, field_ids)
        return None

    if not cfg.dry_run:
        for selected, group, label in (
            (cfg.theme, "textures", "Midnight Circuit theme"),
            (cfg.audio, "sound", "Synth music and cues"),
        ):
            if selected and not _run_was_cancelled(cfg):
                if cfg.notice_sink is not None:
                    cfg.notice_sink(f"Installing {label}…")
                try:
                    with remember_changes(cfg) as writer:
                        extras.install(cfg.edopro_path, cfg.config_path, group)
                        writer.finish()
                except OSError as exc:
                    raise ArtworkStorageError(str(exc)) from exc
                cfg.installed_extras.append(label)

    # Fresh install with decks? Offer the fast path before a ~13,000-card sync.
    if (
        cfg.interactive_prompts
        and not cfg.quiet
        and not cfg.dry_run
        and not cfg.force
        and not cfg.my_decks
        and not cfg.deck_paths
        and not cfg.decks_folder
        and len(full_id_to_name) > 2000
        and _count_card_images(cfg.pics_path) < 500
    ):
        deck_dir = os.path.join(cfg.edopro_path, "deck")
        deck_count = _count_deck_files(deck_dir)
        if deck_count and _prompt_yes_no(
            f"Quick start: only sync the cards in your {deck_count} deck(s) for now? "
            "(run again later for the full set)"
        ):
            cfg.decks_folder = deck_dir
            cfg.my_decks = True
            deck_filter = requested_scope()
            if cfg.idle_summary:
                return None

    if deck_filter is not None and not deck_filter:
        raise SyncInputError(
            "The selected deck files contain no card IDs. Choose a saved .ydk deck "
            "or check the deck folder."
        )
    missing_from_dbs = set()
    if deck_filter is not None:
        id_to_name = {cid: name for cid, name in full_id_to_name.items() if cid in deck_filter}
        missing_from_dbs = deck_filter - full_id_to_name.keys() - metadata.excluded
        console.print(
            f"[dim]Deck filter: {len(id_to_name):,} of {len(deck_filter):,} "
            "deck cards found in the scanned databases.[/dim]"
        )
        if missing_from_dbs:
            console.print(
                f"[yellow]{len(missing_from_dbs):,} deck IDs aren't in any .cdb — "
                "they'll be skipped.[/yellow]"
            )
    else:
        id_to_name = full_id_to_name

    failure_cache_path = _failure_cache_path(cfg)
    checked_policies: dict[int, str | int] = {}
    failure_cache = load_failure_cache(failure_cache_path, contexts=checked_policies)
    excluded = metadata.excluded if deck_filter is None else metadata.excluded & deck_filter
    for cid in excluded:
        failure_cache.pop(cid, None)
    if cfg.recheck_missing or cfg.force:
        # Forget only the cards in scope for THIS run. A deck-filtered recheck
        # must not wipe cache entries for cards it never re-attempts.
        for cid in id_to_name:
            failure_cache.pop(cid, None)

    broken: set[int] = set()
    if cfg.repair:
        broken = set(find_broken_images(cfg.pics_path, set(id_to_name)))
        for cid in broken:
            failure_cache.pop(cid, None)
        if broken:
            console.print(
                f"[yellow]Repair: re-downloading {len(broken):,} broken image(s).[/yellow]"
            )
        else:
            console.print("[dim]Repair: no broken images found.[/dim]")

    # Track the applied policy per folder and card, so partial/deck runs and
    # failed downloads do not mark untouched artwork as upgraded.
    era_folder = os.path.normcase(os.path.abspath(cfg.edopro_path))
    applied = cfg.era_art_applied.get(era_folder, {})
    if not isinstance(applied, dict):
        applied = {}
    cfg.refresh_era_ids = {
        cid
        for cid, name in id_to_name.items()
        if is_era_entry(name, era_suffixes)
        and applied.get(str(cid)) != era_policy(cid, cfg.original_text)
    }
    if cfg.refresh_era_ids:
        for cid in cfg.refresh_era_ids & failure_cache.keys():
            policy = era_policy(cid, cfg.original_text)
            previous = checked_policies.get(cid)
            if previous != policy and (
                previous or policy in ("hd-v3", "original-v2") or str(cid) in applied
            ):
                failure_cache.pop(cid)
        console.print(
            f"[yellow]Refreshing {len(cfg.refresh_era_ids):,} GOAT/Pre-Errata "
            f"card(s) with {'original wording' if cfg.original_text else 'HD artwork'}.[/yellow]"
        )
        if cfg.notice_sink is not None:
            cfg.notice_sink(f"Refreshing {len(cfg.refresh_era_ids):,} GOAT/Pre-Errata cards")

    skipped_by_cache = 0
    skipped_tokens = 0
    if cfg.force:
        missing_ids = list(id_to_name.keys())
    else:
        missing_ids = []
        existing = None
        if deck_filter is None:
            # Small deck syncs only need to check their own IDs.
            with contextlib.suppress(OSError):
                existing = _present_art_filenames(cfg.pics_path, (".jpg",))
        for card_id in id_to_name:
            if card_id not in broken and card_id not in cfg.refresh_era_ids:
                filename = f"{card_id}.jpg"
                present = (
                    filename in existing
                    if existing is not None
                    else os.path.exists(os.path.join(cfg.pics_path, filename))
                )
                if present:
                    continue
            if card_id in failure_cache:
                if card_id in metadata.tokens:
                    skipped_tokens += 1
                else:
                    skipped_by_cache += 1
                continue
            missing_ids.append(card_id)
        if skipped_by_cache:
            console.print(
                f"[dim]Skipped {skipped_by_cache:,} cards cached as unavailable "
                f"(re-check with --recheck-missing)[/dim]"
            )

    if retry_artwork:
        missing_ids = [cid for cid in missing_ids if cid in retry_artwork[0]]

    # Field Spell playmat art (pics/field/) — same incremental + cache logic.
    field_cache_path = _field_failure_cache_path(cfg)
    checked_donors: dict[int, str | int] = {}
    field_cache = load_failure_cache(field_cache_path, contexts=checked_donors)
    field_donors = metadata.field_donors
    if cfg.recheck_missing or cfg.force:
        for cid in id_to_name:
            field_cache.pop(cid, None)
    missing_fields: list[int] = []
    if cfg.field_art:
        candidate_fields = sorted(cid for cid in field_ids if cid in id_to_name)
        if retry_artwork:
            candidate_fields = [cid for cid in candidate_fields if cid in retry_artwork[1]]
        broken_fields: set[int] = set()
        if cfg.repair:
            broken_fields = set(find_broken_field_images(field_art_dir(cfg), set(candidate_fields)))
            for cid in broken_fields:
                field_cache.pop(cid, None)
            if broken_fields:
                console.print(
                    f"[yellow]Repair: re-downloading {len(broken_fields):,} "
                    "broken field image(s).[/yellow]"
                )
        existing_fields = None
        if deck_filter is None and candidate_fields and not cfg.force:
            with contextlib.suppress(OSError):
                existing_fields = _present_art_filenames(field_art_dir(cfg), (".jpg", ".png"))
        pics = Path(cfg.pics_path)
        pending_faces = set(missing_ids)
        for cid in candidate_fields:
            present = (
                f"{cid}.jpg" in existing_fields or f"{cid}.png" in existing_fields
                if existing_fields is not None
                else has_field_art(cfg, cid)
            )
            derived = present and derived_field(pics / "field" / f"{cid}.jpg")
            if not cfg.force and cid not in broken_fields and present and not derived:
                continue
            if cid in field_cache:
                if _field_miss_applies(
                    pics,
                    cid,
                    field_donors.get(cid),
                    checked_donors.get(cid),
                    derived,
                    pending_faces,
                ):
                    if not present:
                        skipped_by_cache += 1
                    continue
                del field_cache[cid]
            missing_fields.append(cid)

    # Nothing missing? Offer the easy full refresh (re-download everything).
    if (
        not missing_ids
        and not missing_fields
        and not cfg.force
        and not cfg.dry_run
        and not cfg.quiet
        and id_to_name
        and not skipped_by_cache
        and not skipped_tokens
        and not missing_from_dbs
        and cfg.interactive_prompts
        and _prompt_yes_no("All artwork is already downloaded. Re-download everything anyway?")
    ):
        cfg.force = True
        missing_ids = list(id_to_name.keys())
        if cfg.field_art:
            missing_fields = sorted(cid for cid in field_ids if cid in id_to_name)

    save_report_after = cfg.save_report
    if missing_ids or missing_fields:
        parts = []
        if missing_ids:
            plural = "s" if len(missing_ids) != 1 else ""
            parts.append(f"{len(missing_ids):,} card image{plural}")
        if missing_fields:
            plural = "s" if len(missing_fields) != 1 else ""
            parts.append(f"{len(missing_fields):,} Field Spell artwork{plural}")
        console.print(f"\n[bold yellow]This will download {' and '.join(parts)}.[/bold yellow]")
        # Ask about saving a report before starting (unless --save-report or --quiet).
        if not cfg.dry_run and not cfg.save_report and not cfg.quiet and cfg.interactive_prompts:
            save_report_after = _prompt_yes_no("Save a sync report when finished?")
        want_textures = _resolve_want_textures(cfg, has_cards=True)
    else:
        want_textures = _resolve_want_textures(cfg, has_cards=False)
        if not want_textures and not cfg.prune:
            if not full_id_to_name:
                cfg.idle_summary = (
                    "Waiting for card data",
                    "Open EDOPro and let it download its card databases, then sync again.",
                )
            elif skipped_by_cache or skipped_tokens or missing_from_dbs:
                notes = []
                if skipped_by_cache:
                    notes.append(
                        f"Previously unavailable images ({skipped_by_cache:,}) are retried after 14 days."
                    )
                if skipped_tokens:
                    notes.append(
                        f"Optional token art ({skipped_tokens:,}) is retried after 14 days."
                    )
                if missing_from_dbs:
                    notes.append(
                        f"Update EDOPro’s card databases to recognize {len(missing_from_dbs):,} missing deck IDs."
                    )
                cfg.idle_summary = ("No new artwork available", " ".join(notes))
            console.print(
                cfg.idle_summary[1]
                if cfg.idle_summary
                else "\n[bold green]All synced — nothing to download![/bold green]"
            )
            if not cfg.dry_run:
                await notify_if_update_available(cfg)
            return None

    if cfg.dry_run and missing_fields:
        console.print(
            f"[dim]Would download {len(missing_fields):,} Field Spell artworks "
            "into pics/field/[/dim]"
        )

        for cid in missing_fields:
            donor = field_donors.get(cid)
            stages = ["own published art"]
            if donor:
                if local := _local_donor(pics / "field", donor):
                    stages.append(f"installed donor {local.name}")
                stages.append(f"published donor {donor}")
            stages.append(f"crop from {cid}.jpg if present after the card sync")
            console.print(f"  [dim]Field {cid} ({id_to_name[cid]}):[/dim] " + " -> ".join(stages))

    # Pre-compute match info for every card to avoid redundant lookups in workers.
    card_match_info: dict[int, tuple[list[int], str | None, bool, bool]] = {}
    for card_id in missing_ids:
        name = id_to_name[card_id]
        official, is_pre_errata_miss, is_suffix_match = find_official_match(
            name,
            name_to_official,
            cfg.suffixes,
            quiet=True,
        )
        manual = manual_map.get(str(card_id))
        card_match_info[card_id] = (official, manual, is_pre_errata_miss, is_suffix_match)

    stats._rush_ids = rush_ids
    stats.token_ids = metadata.tokens
    if cfg.dry_run:
        stats.planned_fields = len(missing_fields)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for card_id in missing_ids:
        queue.put_nowait(card_id)

    async with (
        _create_http_session(
            limit=cfg.concurrency,
            limit_per_host=min(cfg.concurrency, 25),
        ) as session,
        asyncio.TaskGroup() as background,
    ):
        if not cfg.dry_run:
            background.create_task(notify_if_update_available(cfg, session))

        def make_worker(progress=None, task_id=None):
            async def worker():
                while True:
                    if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                        return
                    try:
                        card_id = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    name = id_to_name[card_id]
                    official, manual, is_pre_errata_miss, is_suffix_match = card_match_info[card_id]
                    if progress and task_id is not None:
                        progress.update(task_id, description=f"Syncing {_truncate(name, 40)}")
                    try:
                        await download_card(
                            session,
                            card_id,
                            name,
                            official,
                            manual,
                            is_pre_errata_miss,
                            is_suffix_match,
                            cfg,
                            stats,
                            progress,
                            task_id,
                            replace_existing=card_id in broken,
                        )
                    except (asyncio.CancelledError, ArtworkStorageError):
                        raise
                    except Exception as exc:
                        # A crash proves nothing about availability — don't cache it.
                        stats.record_failure(card_id, name, transient=True)
                        stats.record_unexpected_error(card_id, name, exc)
                        if progress and task_id is not None:
                            progress.advance(task_id)

            return worker

        if missing_ids:
            if cfg.gui_progress is not None:
                progress = cfg.gui_progress
                task_id = progress.add_task("Syncing card artwork", total=len(missing_ids))
                workers = [make_worker(progress, task_id)() for _ in range(cfg.concurrency)]
                await _run_workers(workers)
            elif RICH_AVAILABLE and not cfg.quiet:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(bar_width=40),
                    MofNCompleteColumn(),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task_id = progress.add_task("Syncing...", total=len(missing_ids))
                    workers = [make_worker(progress, task_id)() for _ in range(cfg.concurrency)]
                    await _run_workers(workers)
            else:
                workers = [make_worker()() for _ in range(cfg.concurrency)]
                await _run_workers(workers)

        if missing_fields and not cfg.dry_run:
            field_queue: asyncio.Queue[int] = asyncio.Queue()
            for cid in missing_fields:
                field_queue.put_nowait(cid)

            def make_field_worker(progress=None, task_id=None):
                async def worker():
                    while True:
                        if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                            return
                        try:
                            cid = field_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        fname = id_to_name.get(cid, str(cid))
                        if progress and task_id is not None:
                            progress.update(
                                task_id, description=f"Field art {_truncate(fname, 40)}"
                            )
                        try:
                            await download_field_art(
                                session,
                                cid,
                                fname,
                                cfg,
                                stats,
                                progress,
                                task_id,
                                donor=field_donors.get(cid),
                                layout=metadata.field_layout(cid, rush_ids),
                            )
                        except (asyncio.CancelledError, ArtworkStorageError):
                            raise
                        except Exception as exc:
                            stats.record_field_failure(cid, fname, transient=True)
                            stats.record_unexpected_error(cid, fname, exc)
                            if progress and task_id is not None:
                                progress.advance(task_id)

                return worker

            field_workers = min(cfg.concurrency, 20)
            if cfg.gui_progress is not None:
                progress = cfg.gui_progress
                task_id = progress.add_task("Field Spell artwork", total=len(missing_fields))
                await _run_workers(
                    [make_field_worker(progress, task_id)() for _ in range(field_workers)]
                )
            elif RICH_AVAILABLE and not cfg.quiet:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(bar_width=40),
                    MofNCompleteColumn(),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task_id = progress.add_task("Field art...", total=len(missing_fields))
                    await _run_workers(
                        [make_field_worker(progress, task_id)() for _ in range(field_workers)]
                    )
            else:
                await _run_workers([make_field_worker()() for _ in range(field_workers)])

        if want_textures and not cfg.dry_run:
            pack = await _resolve_texture_pack(session, cfg)
            if cfg.notice_sink is not None:
                cfg.notice_sink("Downloading curated textures…")
            console.print(f"\n[bold cyan]Downloading curated textures ({pack})...[/bold cyan]")
            got, already, total = await download_curated_textures(session, cfg, pack)
            if total:
                skipped_note = f", {already} already on disk" if already else ""
                console.print(
                    f"[green]Curated textures: {got}/{total} downloaded{skipped_note}[/green] "
                    f"[dim]-> {os.path.join(cfg.edopro_path, 'textures')}[/dim]"
                )
        elif want_textures and cfg.dry_run:
            console.print("\n[dim]Would download curated textures into textures/[/dim]")

    if missing_ids and not cfg.dry_run:
        now_ts = datetime.now().timestamp()
        for cid in stats.optional_tokens:
            failure_cache[cid] = now_ts
        for cid, _ in stats.failed_cards:
            # Only remember definitive misses; network hiccups retry next run.
            if cid not in stats.transient_ids:
                failure_cache[cid] = now_ts
                if cid in cfg.refresh_era_ids:
                    checked_policies[cid] = era_policy(cid, cfg.original_text)
        # Forget cached entries whose image is on disk now (e.g. after a recheck).
        for cid in list(failure_cache.keys()):
            if (cid not in cfg.refresh_era_ids or cid in stats.success_ids) and os.path.exists(
                os.path.join(cfg.pics_path, f"{cid}.jpg")
            ):
                del failure_cache[cid]
        save_failure_cache(failure_cache_path, failure_cache, contexts=checked_policies)

    if not cfg.dry_run:
        for cid in cfg.refresh_era_ids & stats.success_ids:
            applied[str(cid)] = era_policy(cid, cfg.original_text)
        if applied:
            cfg.era_art_applied[era_folder] = applied
            config_module.save_setting(cfg.config_path, "era_art_applied", cfg.era_art_applied)

    # Only once the refresh has actually run: recording it after a cancelled
    # run would strand the cards it never got to with no image at all.
    if (
        not cfg.dry_run
        and cfg.original_text != cfg.saved_original_text
        and not _run_was_cancelled(cfg)
    ):
        config_module.save_setting(cfg.config_path, "original_text", cfg.original_text)
        cfg.saved_original_text = cfg.original_text

    if missing_fields and not cfg.dry_run:
        now_ts = datetime.now().timestamp()
        misses = stats.published_misses | {
            cid for cid, _ in stats.field_failed_cards if cid not in stats.field_transient_ids
        }
        for cid in misses:
            field_cache[cid] = now_ts
            checked_donors.pop(cid, None)
            if cid in field_donors:
                checked_donors[cid] = field_donors[cid]
        for cid in field_cache.keys() - misses:
            if has_field_art(cfg, cid) and not derived_field(
                Path(field_art_dir(cfg), f"{cid}.jpg")
            ):
                del field_cache[cid]
        save_failure_cache(field_cache_path, field_cache, contexts=checked_donors)

    if cfg.prune and not cfg.dry_run:
        if deck_filter is not None:
            console.print(
                "[yellow]Skipping --prune: can't safely prune while --deck "
                "is active (the DB scan is authoritative, not the deck).[/yellow]"
            )
        elif unreadable or not full_id_to_name or _run_was_cancelled(cfg):
            console.print("[yellow]Skipping --prune: no complete card index is available.[/yellow]")
        else:
            pruned = prune_orphan_images(
                cfg.pics_path, set(full_id_to_name) | metadata.excluded, cfg=cfg
            )
            if pruned:
                console.print(f"[dim]Pruned {pruned:,} orphaned images[/dim]")

    if missing_ids or missing_fields:
        print_summary(stats, cfg, perf_counter() - started_at, save_report_after)
    return stats


def should_pause_before_exit(cfg: Config | None, gui_ran: bool = False) -> bool:
    """Keep a Windows console run open so the output stays readable.

    Only ever when someone is actually there to press Enter. The Windows build
    is a windowed app, so a double-click has no console at all: pausing then
    would leave the app waiting forever on input nobody can see. A real
    terminal has an interactive stdin; a double-click and a piped run do not.
    """
    if gui_ran:
        return False
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    if cfg and cfg.no_pause:
        return False
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def pause_before_exit(cfg: Config | None, gui_ran: bool = False) -> None:
    """Wait for Enter before closing when the packaged Windows app finishes."""
    if not should_pause_before_exit(cfg, gui_ran):
        return
    with contextlib.suppress(EOFError):
        input("\nPress Enter to close this window...")


def _extract_exit_code(code) -> int:
    """Convert a SystemExit payload into a shell-friendly integer."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _launch_config() -> Config:
    parser = config_module._build_parser()
    cli = parser.parse_args()
    if not cli.dry_run:
        return Config(defer_discovery=True)
    if cli.generate_config or cli.gui_smoke_test:
        parser.error("--dry-run cannot be combined with a command that writes output")
    if cli.config or not getattr(sys, "frozen", False):
        return Config(defer_discovery=True)

    # An explicit settings source prevents the packaged constructor migrating a legacy file.
    state = Path(config_module.default_config_path())
    legacy = Path(config_module._runtime_dir()) / config_module.CONFIG_FILENAME
    source = (
        state if config_module._load_config_file(str(state)) or not legacy.is_file() else legacy
    )
    cfg = Config([*sys.argv[1:], "--config", str(source)], defer_discovery=True)
    cfg.config_path = str(state)
    manual_map = state.with_name("manual_map.json")
    legacy_map = legacy.with_name(manual_map.name)
    cfg.manual_map_file = str(
        legacy_map if not manual_map.is_file() and legacy_map.is_file() else manual_map
    )
    return cfg


def main() -> int:
    cfg: Config | None = None
    exit_code = 0
    launched_gui = False

    try:
        cfg = _launch_config()
        if cfg.cli.undo_last_sync:
            detect_edopro_folder(cfg)
            if cfg.dry_run:
                console.print(f"Would undo the last artwork sync in {cfg.edopro_path}.")
                return 0
            try:
                restored = restore_artwork(cfg)
            except (ValueError, OSError, sqlite3.DatabaseError) as exc:
                console.print(f"Could not undo the last artwork sync: {exc}")
                return 1
            console.print(f"Undid the last artwork sync. Restored {restored} file(s).")
            return 0
        if cfg.cli.gui_smoke_test:
            launched_gui = True
            from tools.gui_smoke import smoke_test

            return smoke_test(cfg, VERSION, run, _apply_gui_choices)
        if cfg.cli.legacy_gui and not cfg.dry_run and getattr(sys, "frozen", False):
            from desktop_dialogs import show_startup_error

            show_startup_error(
                "The Tk window is available from source. Open the app without --legacy-gui for Qt."
            )
            return 1
        studio_launch = not cfg.dry_run and _should_show_gui(cfg) and not cfg.cli.legacy_gui
        if not cfg.health_check and not studio_launch:
            detect_edopro_folder(cfg)

        if sys.platform == "win32":
            # Deprecated as of Python 3.14 — revisit when bumping past the
            # pinned 3.11 build (aiohttp needs the selector loop on Windows).
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        if studio_launch:
            launched_gui = True
            try:
                from studio.app import run_app
            except ImportError as exc:
                from desktop_dialogs import show_startup_error

                show_startup_error(f"{config_module.APP_NAME} needs PySide6-Essentials. {exc}")
                return 1
            exit_code = run_app(cfg, VERSION, run, _apply_gui_choices)
        elif cfg.cli.legacy_gui and not cfg.dry_run:
            import gui

            # Tk runs sync in a worker. Qt's image codecs need their application on the main thread.
            from sleeves.imaging import ensure_gui

            ensure_gui()
            launched_gui = True
            try:
                exit_code = gui.run_app(cfg, VERSION, run, _apply_gui_choices)
            except gui.GuiUnavailable as exc:
                from desktop_dialogs import show_startup_error

                show_startup_error(
                    f"The legacy EDOPro window requires a working Tk installation. {exc}"
                )
                return 1
        if not launched_gui:
            stats = asyncio.run(run(cfg))
            if stats is not None and stats.storage_error:
                exit_code = 1
    except KeyboardInterrupt:
        exit_code = 130
        console.print("\n[yellow]Interrupted - partial progress is saved.[/yellow]")
    except SystemExit as exc:
        exit_code = _extract_exit_code(exc.code)
    except Exception as exc:
        exit_code = 1
        if getattr(sys, "frozen", False):
            console.print(f"\n[bold red]Unexpected error:[/bold red] {exc}")
        else:
            raise
    finally:
        pause_before_exit(cfg, launched_gui)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
