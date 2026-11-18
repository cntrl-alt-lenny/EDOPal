"""A lightweight installation snapshot. Full image verification stays on demand."""

from copy import copy
from dataclasses import dataclass
from os import scandir
from pathlib import Path
from threading import Event

import main
from artwork_flow import format_size
from config import Config
from studio.pages.cards_undo import has_restore


class NoDecksFound(Exception):
    """The engine reached an empty saved-deck scope before downloading images."""


def keep_deck_scope(cfg: Config) -> None:
    if not cfg.my_decks or cfg.deck_paths or cfg.decks_folder:
        return
    forward = cfg.notice_sink

    def notice(message: str) -> None:
        if message in {
            "No decks found — syncing everything instead",
            "Your decks are empty — syncing everything instead",
        }:
            raise NoDecksFound
        # Extras are installed before the engine resolves the deck filter. Check the
        # actual folder here, including a replacement chosen during database recovery.
        if message in {"Installing Midnight Circuit theme…", "Installing Synth music and cues…"}:
            check = copy(cfg)
            check.decks_folder = str(Path(cfg.edopro_path) / "deck")
            if not main.collect_deck_filter_ids(check):
                raise NoDecksFound
        forward(message)

    cfg.notice_sink = notice


@dataclass(frozen=True)
class LibrarySnapshot:
    artwork: str
    caption: str
    decks: int
    storage: str
    can_restore: bool


def inspect_library(cfg: Config, cancel: Event) -> LibrarySnapshot | None:
    count = size = 0
    for folder in (Path(cfg.pics_path), Path(cfg.pics_path) / "field"):
        if not folder.is_dir():
            continue
        with scandir(folder) as entries:
            for entry in entries:
                if cancel.is_set():
                    return None
                if entry.name.lower().endswith((".jpg", ".png")) and entry.is_file(
                    follow_symlinks=False
                ):
                    size += entry.stat().st_size
                    count += 1
    decks = main._count_deck_files(str(Path(cfg.edopro_path) / "deck"))
    artwork, caption = f"{count:,}", "Artwork files"
    # Opening thousands of individual files is expensive even on a worker, especially on SD cards.
    if count <= 500 and not cancel.is_set():
        dbs = main.get_db_files(cfg.edopro_path)
        if dbs and sum(Path(db).stat().st_size for db in dbs) <= 8 * 1024**2:
            cards, _, _, fields = main.scan_databases(dbs)
            if cards and not cancel.is_set():
                stats = main.collect_coverage_stats(cfg, cards, fields)
                artwork = f"{stats['cards_have'] / len(cards):.0%}"
                caption = f"of {len(cards):,} cards have art"
    return LibrarySnapshot(artwork, caption, decks, format_size(size), has_restore(cfg))
