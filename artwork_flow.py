"""Shared choices and display models for artwork sync, independent of the UI."""

import contextlib
import copy
import os
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import Config, folder_has_card_databases

if TYPE_CHECKING:
    from main import DownloadStats


# Context-local so concurrent CLI or deck jobs never share a Studio undo record.
ArtworkChange = Callable[[str | Path, bytes | bytearray | None], None]
artwork_change_sink: ContextVar[ArtworkChange | None] = ContextVar(
    "artwork_change_sink", default=None
)


def record_artwork_change(path: str | Path, incoming: bytes | bytearray | None) -> None:
    if observer := artwork_change_sink.get():
        observer(path, incoming)


# (section, [(key, label, hint, default), ...])
CHECKBOX_GROUPS = [
    (
        "ARTWORK",
        [
            (
                "field_art",
                "Field Spell playmat artwork",
                "Also fetch the art shown on the board during duels",
                True,
            ),
            (
                "my_decks",
                "Only cards from my decks",
                "Recommended first run — much faster, then sync the rest later",
                False,
            ),
            (
                "hd_era_art",
                "HD GOAT / Pre-Errata artwork",
                "Modern text in HD. Turn off for original wording at lower resolution.",
                True,
            ),
        ],
    ),
    (
        "EXTRAS",
        [
            (
                "theme",
                "Midnight Circuit theme",
                "Custom backgrounds and card sleeves",
                False,
            ),
            (
                "audio",
                "Synth music and cues",
                "Adds two tracks and replaces ten duel cues",
                False,
            ),
            (
                "save_report",
                "Save a sync report",
                "Writes a text file listing anything unavailable",
                False,
            ),
        ],
    ),
    (
        "MAINTENANCE",
        [
            (
                "repair",
                "Repair broken images first",
                "Finds and re-downloads corrupt files",
                False,
            ),
            (
                "force",
                "Re-download everything",
                "Full refresh instead of only missing artwork",
                False,
            ),
        ],
    ),
]

CHECKBOXES = [box for _section, boxes in CHECKBOX_GROUPS for box in boxes]


def recommended_my_decks(cfg: Config) -> bool:
    return recommend_deck_scope(cfg.edopro_path, cfg.pics_path)


def recommend_deck_scope(game: str, pics_path: str) -> bool:
    """Recommend deck-first sync on an empty install that already has decks."""
    deck_dir = os.path.join(game, "deck")
    if not os.path.isdir(deck_dir):
        return False
    image_count = 0
    if os.path.isdir(pics_path):
        with contextlib.suppress(OSError), os.scandir(pics_path) as entries:
            for entry in entries:
                if entry.name.lower().endswith(".jpg") and entry.is_file():
                    image_count += 1
                    if image_count == 500:
                        return False
    for _root, _dirs, files in os.walk(deck_dir):
        if any(name.lower().endswith(".ydk") for name in files):
            return True
    return False


def compact_path(path: str, limit: int = 36) -> str:
    """Shorten a long path to its last few folders so the row stays one line.

    Wrapping instead would widen the window and add a second line to every
    screen, since all three share one size. The tail is the part that tells
    you which folder this is, so that is what survives.
    """
    if len(path) <= limit:
        return path
    separator = "\\" if "\\" in path else "/"
    parts = [part for part in path.split(separator) if part]
    tail = parts[-1] if parts else path
    for count in range(2, len(parts) + 1):
        candidate = separator.join(parts[-count:])
        if len(candidate) + 1 > limit:
            break
        tail = candidate
    return f"…{separator}{tail}"


def format_size(nbytes: int) -> str:
    if nbytes >= 1024**3:
        return f"{nbytes / 1024**3:.1f} GB"
    if nbytes >= 1024**2:
        return f"{nbytes / 1024**2:.0f} MB"
    return f"{nbytes / 1024:.0f} KB"


class GuiProgress:
    """The subset of Rich progress used by the sync engine, delivered in order."""

    def __init__(self, events: Any) -> None:
        self._emit = events.put
        self._next_id = 0

    def add_task(self, description: str, total: int = 0) -> int:
        task_id = self._next_id
        self._next_id += 1
        self._emit(("task", task_id, description, total))
        return task_id

    def update(self, task_id: int, description: str | None = None, **_kwargs: Any) -> None:
        if description:
            self._emit(("desc", task_id, description))

    def advance(self, task_id: int, step: int = 1) -> None:
        self._emit(("adv", task_id, step))


def valid_decks(selected_path: str, game: Path, paths: tuple[Path, ...]) -> bool:
    selected = Path(selected_path).resolve()
    deck = (selected / "deck").resolve()
    return bool(
        selected == game.resolve()
        and paths
        and folder_has_card_databases(str(selected))
        and deck.is_relative_to(selected)
        and all(
            path.is_file()
            and path.suffix.lower() == ".ydk"
            and not path.is_symlink()
            and path.resolve().parent == deck
            for path in paths
        )
    )


def make_run_config(
    base: Config, picks: dict, apply_choices: Callable, paths: tuple[Path, ...] = ()
) -> Config:
    cfg = copy.copy(base)
    if paths:
        cfg.deck_paths = [str(path) for path in paths]
        cfg.decks_folder = None
        cfg.prune = False
        picks = dict(picks, my_decks=False, force=False, textures=False, theme=False, audio=False)
    apply_choices(cfg, picks)
    return cfg


@dataclass(frozen=True)
class Summary:
    title: str
    first: tuple[str, str] = ("", "")
    second: tuple[str, str] = ("", "")
    rows: tuple[tuple[str, str], ...] = ()
    note: str = ""
    warning: bool = False
    location: tuple[str, str] = ("", "")
    failures: tuple[str, ...] = ()

    @property
    def full_note(self) -> str:
        caption, path = self.location
        return " ".join(part for part in (self.note, f"{caption}: {path}" if path else "") if part)


def coverage_summary(data: dict) -> Summary:
    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.0f}%" if whole else "n/a"

    rows = [
        ("Cards indexed", f"{data['cards_total']:,}"),
        ("Card art on disk", f"{data['cards_have']:,}"),
        ("Field art on disk", f"{data['fields_have']:,} of {data['fields_total']:,}"),
        ("Disk usage", format_size(data["disk_bytes"])),
    ]
    if data.get("fields_derived"):
        rows.append(("Derived from card faces", f"{data['fields_derived']:,}"))
    optional = data["cards_total"] - data.get("cards_expected", data["cards_total"])
    if optional:
        rows.append(("Optional tokens without art", f"{optional:,}"))
    if data["known_missing"]:
        rows.append(("Known-missing (cached)", f"{data['known_missing']:,}"))
    return Summary(
        "Artwork coverage",
        (
            pct(data["cards_have"], data.get("cards_expected", data["cards_total"])),
            "Card art on disk",
        ),
        (pct(data["fields_have"], data["fields_total"]), "Field art on disk"),
        tuple(rows),
        location=("Images folder", data["pics_path"]),
    )


def sync_summary(
    cfg: Config, payload: dict, cancelled: bool, folder_cancelled: bool, coverage: bool
) -> Summary:
    error = payload.get("error")
    if not error and payload.get("exit"):
        error = "Check that the selected installation has readable card databases, then try again."
    if error:
        return Summary("Something went wrong", note=error, warning=True)
    if folder_cancelled:
        return Summary("No folder selected", note="Choose your EDOPro installation to continue.")
    stats: DownloadStats | None = payload.get("stats")
    if cancelled and stats is None:
        return Summary("Sync cancelled", note="You can start another sync whenever you are ready.")
    if coverage:
        return Summary(
            "Coverage could not be measured", note="Check the game folder and try again."
        )
    installed = cfg.installed_extras
    extra_note = (
        f"Installed {', '.join(installed)}. Reopen EDOPro to use them." if installed else ""
    )
    if stats is None:
        if idle := getattr(cfg, "idle_summary", None):
            return Summary(idle[0], ("0", "Downloaded"), note=idle[1])
        if installed:
            return Summary("Extras installed", note=extra_note)
        return Summary("Already up to date", note="All artwork is present. Enjoy your duels!")

    downloaded = stats.total_ok + stats.field_ok
    unavailable = stats.failed + stats.field_failed
    rows = (
        ("Card artwork", stats.total_ok),
        ("Field Spell artwork", stats.field_ok),
        ("Copied from installed field art", stats.field_local),
        ("Queued downloads skipped (file already present)", stats.skipped),
        ("Rush Duel (no art yet)", stats.rush_failures),
        ("Anime / custom (no art yet)", stats.unofficial_failures),
        ("Derived from card faces", stats.field_derived),
        ("Official cards unavailable", len(stats.official_failures)),
        ("Field art unavailable", stats.field_failed),
        ("Network errors (will retry)", stats.transient_failures + len(stats.field_transient_ids)),
    )
    notes = [extra_note] if installed else []
    if unavailable > stats.transient_failures + len(stats.field_transient_ids):
        notes.append("Known-missing artwork is retried after 14 days.")
    if stats.transient_failures or stats.field_transient_ids:
        notes.append("Sync again to retry interrupted downloads.")
    report = getattr(cfg, "report_path", None)
    failures = []
    for kind, entries, retry_ids in (
        ("Card artwork", stats.failed_cards, stats.transient_ids),
        ("Field art", stats.field_failed_cards, stats.field_transient_ids),
    ):
        for card_id, name in entries:
            suffix = " • Will retry" if card_id in retry_ids else ""
            failures.append(f"{name}   ·   {kind}{suffix}")
    return Summary(
        "Sync cancelled" if cancelled else "Sync complete",
        (
            f"{downloaded:,}",
            "Artwork installed" if stats.field_local or stats.field_derived else "Downloaded",
        ),
        (f"{unavailable:,}", "Unavailable"),
        tuple((label, f"{value:,}") for label, value in rows if value),
        " ".join(notes),
        bool(unavailable),
        ("Report saved", report) if report else ("", ""),
        tuple(failures),
    )
