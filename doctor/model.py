"""Structured findings and the fixed player-facing catalogue."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from config import APP_NAME

Severity = Literal["ok", "info", "warning", "problem"]


@dataclass(frozen=True)
class FindingText:
    severity: Severity
    title: str
    explanation: str
    action: str | None = None
    support_counts: tuple[str, ...] = ()


CATALOGUE = {
    "installation.found": FindingText(
        "ok", "EDOPro installation found", "The folder has an EDOPro executable or app bundle."
    ),
    "installation.data_only": FindingText(
        "warning",
        "Only game data was found",
        "The folder has EDOPro card data but no recognised game executable.",
        "Select the folder containing the game executable.",
    ),
    "installation.missing": FindingText(
        "problem",
        "EDOPro installation not found",
        "The folder does not contain a recognised game executable or card database.",
        "Select the folder where you open EDOPro.",
    ),
    "installation.version_unknown": FindingText(
        "info",
        "Game version unavailable",
        f"The game files do not expose a version that {APP_NAME} can read on this platform.",
    ),
    "installation.writable": FindingText(
        "ok",
        "Game folders appear writable",
        "The current account appears to have permission to add files to the checked game folders.",
    ),
    "installation.read_only": FindingText(
        "problem",
        "Game folders are not writable",
        "The current account appears unable to add files to at least one game folder.",
        "Use a copy of EDOPro in a folder you can write to.",
    ),
    "installation.write_unknown": FindingText(
        "info",
        "Write access could not be checked",
        f"{APP_NAME} could not determine write access to at least one game folder.",
    ),
    "process.running": FindingText(
        "info",
        "EDOPro appears to be open",
        "The process check found the selected client or an EDOPro process with an unavailable path.",
        "Run the check again after updates have finished or the game has closed.",
    ),
    "process.closed": FindingText(
        "ok",
        "EDOPro was not detected running",
        "The process check did not find the selected client.",
    ),
    "process.unknown": FindingText(
        "info", "Running status unavailable", f"{APP_NAME} could not complete the process check."
    ),
    "repositories.config": FindingText(
        "info",
        "Update repositories inventoried",
        "These are the sources configured for the game to read or update.",
        support_counts=("configured", "available"),
    ),
    "repositories.config_missing": FindingText(
        "warning",
        "Update configuration missing",
        "The main repository settings file is missing.",
        "Check the selected installation against the official EDOPro download.",
    ),
    "repositories.config_invalid": FindingText(
        "warning",
        "Update configuration unreadable",
        "Some repository settings could not be read or interpreted.",
        "Check the repository settings in EDOPro or ask Project Ignis support.",
    ),
    "repositories.missing": FindingText(
        "warning",
        "Configured repository is missing",
        "A configured folder is missing. The download may not have started or may have failed.",
        "Open EDOPro and check the repository update panel.",
    ),
    "repositories.empty": FindingText(
        "warning",
        "Configured repository is empty",
        "A configured folder has no checked-out files.",
        "Open EDOPro and check the repository update panel.",
    ),
    "repositories.clone_incomplete": FindingText(
        "warning",
        "Repository checkout is incomplete",
        "A managed folder has files but its Git checkout could not be confirmed.",
        "Check the repository update panel and keep any local files before seeking help.",
    ),
    "repositories.resource_missing": FindingText(
        "warning",
        "Configured repository folder is missing",
        "The repository exists but a configured card, script, core or list folder is unavailable.",
        "Check the repository update panel in EDOPro.",
    ),
    "repositories.disabled": FindingText(
        "info",
        "Repository reading is disabled",
        "EDOPro is configured to skip loading this repository's files.",
    ),
    "databases.available": FindingText(
        "ok",
        "Card databases are available",
        f"{APP_NAME} read the installed databases and counted distinct card IDs.",
        support_counts=("files", "cards", "duplicates", "artwork_index_cards"),
    ),
    "databases.missing": FindingText(
        "problem",
        "No usable card databases",
        "No installation database supplied readable card rows.",
        "Open EDOPro, allow its repositories to finish updating and run the check again.",
    ),
    "databases.placeholder": FindingText(
        "info",
        "Base database is a placeholder",
        "EDOPro can ship an empty base database and load cards from other sources.",
    ),
    "databases.empty": FindingText(
        "warning",
        "Card database is empty",
        "A card database contains no usable card rows.",
        "Let EDOPro finish downloading its game data and check again.",
    ),
    "databases.unreadable": FindingText(
        "problem",
        "Card database is unreadable",
        "A database failed its SQLite integrity or card table check.",
        "Check the repository update panel or ask Project Ignis support.",
    ),
    "databases.mappings_unreadable": FindingText(
        "warning",
        "Card ID mappings are unreadable",
        "The game may be unable to resolve some old card IDs.",
        "Check the repository update panel in EDOPro.",
    ),
    "banlists.available": FindingText(
        "ok",
        "Forbidden and Limited Lists are available",
        "Lists were found in the game or its configured repositories.",
        support_counts=("files", "lists", "whitelists"),
    ),
    "banlists.missing": FindingText(
        "warning",
        "No Forbidden and Limited Lists found",
        "No list with a readable name was found in the configured sources.",
        "Let EDOPro finish updating its Forbidden and Limited Lists repository.",
    ),
    "banlists.unreadable": FindingText(
        "warning",
        "A Forbidden and Limited List is unreadable",
        "A list file has no named list, contains invalid entries, or could not be read.",
        "Check the list file or the repository that supplied it.",
    ),
    "artwork.coverage": FindingText(
        "info",
        "Artwork coverage measured",
        "Counts use the same artwork folders, image checks and card scope as the sync screen.",
        support_counts=("cards_have", "cards_total", "fields_have", "fields_total", "disk_bytes"),
    ),
    "artwork.unavailable": FindingText(
        "info", "Artwork coverage unavailable", f"{APP_NAME} could not scan the artwork folders."
    ),
    "decks.inventory": FindingText(
        "ok",
        "Saved decks checked",
        f"{APP_NAME} read the saved deck files and compared their card IDs with installed card data.",
        support_counts=("files", "parsed"),
    ),
    "decks.unreadable": FindingText(
        "warning",
        "A saved deck has unreadable entries",
        "A deck could not be read or contains lines that are not valid card IDs.",
        "Open the deck in EDOPro or re-export it from its source.",
    ),
    "decks.unknown_cards": FindingText(
        "warning",
        "A deck uses unknown card IDs",
        "Some IDs are missing from the readable installed databases and ID mappings.",
        "Let EDOPro update, then check for any custom databases the deck needs.",
    ),
    "settings.unavailable": FindingText(
        "info", "Game settings unavailable", f"{APP_NAME} could not read the game settings file."
    ),
    "skin.default": FindingText(
        "ok", "Built-in skin selected", "The game is using its built-in appearance."
    ),
    "skin.available": FindingText(
        "ok",
        "Active skin files are present",
        "The skin definition and its named images passed the file checks.",
        support_counts=("images",),
    ),
    "skin.missing": FindingText(
        "problem",
        "Active skin is missing",
        "The selected skin folder or its skin definition could not be found.",
        "Choose a different skin in EDOPro or restore the previous skin.",
    ),
    "skin.malformed": FindingText(
        "warning",
        "Active skin definition is malformed",
        f"{APP_NAME} could not read the active skin's XML definition.",
        "Choose a different skin or ask the skin author for an updated copy.",
    ),
    "skin.images": FindingText(
        "warning",
        "Active skin images need attention",
        "Some images referenced by the active skin are missing or unreadable.",
        "Choose a different skin or restore the original skin files.",
        ("affected",),
    ),
    "skin.stock_malformed": FindingText(
        "info",
        "A stock skin shipped with malformed files",
        "These files match a known issue in an EDOPro-supplied skin.",
        "If the skin displays incorrectly, choose another skin.",
        ("affected",),
    ),
    "fonts.available": FindingText(
        "ok",
        "Configured fonts are available",
        "The checked font files are readable or use the game's bundled font.",
        support_counts=("fonts",),
    ),
    "fonts.missing": FindingText(
        "warning",
        "A configured font is missing",
        "One or more explicit font files could not be opened.",
        "Restore the previous skin and fonts or select an available font.",
        ("affected",),
    ),
    "music.disabled": FindingText(
        "info",
        "Game music is switched off",
        "EDOPro is set to play without music.",
        "Enable game music in EDOPro or in the music library.",
        ("files",),
    ),
    "music.zero_volume": FindingText(
        "info",
        "Game music volume is zero",
        "EDOPro is set to play music at zero volume.",
        "Raise the music volume in EDOPro.",
        ("files",),
    ),
    "music.missing": FindingText(
        "warning",
        "Music is on but tracks are missing",
        "At least one game scene has no supported music files.",
        f"Restore the original sound files or select a playlist in {APP_NAME}.",
        ("files", "affected"),
    ),
    "music.available": FindingText(
        "ok",
        "Game music is available",
        "Music is enabled and tracks were found for the standard scenes.",
        support_counts=("files",),
    ),
    "sound.disabled": FindingText(
        "info",
        "Game sound effects are switched off",
        "EDOPro is set to play without sound effects.",
        "Enable sound effects in EDOPro.",
        ("files",),
    ),
    "sound.zero_volume": FindingText(
        "info",
        "Sound effect volume is zero",
        "EDOPro is set to play sound effects at zero volume.",
        "Raise the sound volume in EDOPro.",
        ("files",),
    ),
    "sound.missing": FindingText(
        "warning",
        "Sound effects are on but missing",
        "No supported sound effect files were found.",
        "Restore the original sound files.",
        ("files",),
    ),
    "sound.available": FindingText(
        "ok",
        "Game sound effects are available",
        "Sound effects are enabled and files were found.",
        support_counts=("files",),
    ),
    "audio.backend_disabled": FindingText(
        "info",
        "Game audio backend is disabled",
        "EDOPro is set to use no audio backend.",
        "Select an audio backend in EDOPro.",
    ),
    "windbot.available": FindingText(
        "ok",
        "WindBot is present",
        f"{APP_NAME} found the practice bot executable and its supporting files.",
        support_counts=("decks", "catalogue_entries", "matched_entries"),
    ),
    "windbot.missing": FindingText(
        "info", "WindBot is not installed", "The optional practice bot executable was not found."
    ),
    "windbot.incomplete": FindingText(
        "warning",
        "Some WindBot files are unavailable",
        "The executable exists but its catalogue, decks or supporting library could not be found or read.",
        "Check the WindBot files supplied with EDOPro.",
    ),
    "windbot.unmatched": FindingText(
        "info",
        "Some WindBot entries have no matching filename",
        "Some catalogue entries did not match a deck filename. The bot may resolve those names itself.",
        support_counts=("affected",),
    ),
    "windbot.deck_unreadable": FindingText(
        "info",
        "Some WindBot deck entries could not be checked",
        "At least one supplied deck has unreadable entries or has IDs absent from the readable databases.",
        support_counts=("affected",),
    ),
    "windbot.list_conflicts": FindingText(
        "info",
        "Some WindBot decks conflict with installed lists",
        "Some decks exceed a list limit or contain a card excluded by a whitelist.",
        "Choose a bot suited to the list you plan to use.",
        ("decks", "lists", "conflicts"),
    ),
    "storage.enough": FindingText(
        "ok",
        "Free space covers the artwork estimate",
        "The artwork drive has space for the estimated missing downloads.",
        support_counts=("free_mib", "estimated_mib"),
    ),
    "storage.low": FindingText(
        "warning",
        "Artwork sync may run out of space",
        "The artwork drive has less free space than the estimated missing downloads.",
        "Free space or sync only the decks you play.",
        ("free_mib", "estimated_mib"),
    ),
    "storage.unknown": FindingText(
        "info",
        "Artwork disk space unavailable",
        f"{APP_NAME} could not measure free space or estimate the remaining artwork.",
    ),
    "state.clean": FindingText(
        "ok",
        f"No interrupted {APP_NAME} changes found",
        "No known interrupted journals were found for this installation.",
    ),
    "state.lock_idle": FindingText(
        "info",
        f"Inactive {APP_NAME} lock file found",
        f"This marker is reused by {APP_NAME}. The operating system released its lock.",
        "Leave the file in place.",
    ),
    "state.lock_busy": FindingText(
        "info",
        f"{APP_NAME} is using an update lock",
        "Another operation appears to hold this lock.",
        "Wait for the operation to finish before changing these files.",
    ),
    "state.lock_unknown": FindingText(
        "info",
        f"{APP_NAME} lock state could not be checked",
        "A lock file is present but its current state could not be determined.",
    ),
    "state.interrupted": FindingText(
        "warning",
        f"An interrupted {APP_NAME} change needs attention",
        "An unfinished appearance or playlist journal remains on disk.",
        f"Close EDOPro, then use the matching Restore or Undo action in {APP_NAME}. Keep the journal and backups.",
    ),
}


@dataclass(frozen=True)
class Finding:
    id: str
    severity: Severity
    title: str
    explanation: str
    evidence: dict[str, object] = field(default_factory=dict)
    action: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    installation: Path
    studio_version: str
    game_version: str | None
    findings: tuple[Finding, ...]


def finding(id: str, **evidence: object) -> Finding:
    wording = CATALOGUE[id]
    return Finding(
        id, wording.severity, wording.title, wording.explanation, evidence, wording.action
    )
