"""Run the offline installation doctor on a background worker."""

import math
import shutil
from pathlib import Path

from artwork_coverage import scan_artwork_coverage
from card_databases import FIELD_SPELL_TYPE, get_db_files
from config import CONFIG_FILENAME, app_version, user_data_dir
from extras import _UNDO_GROUPS, _group_dir
from music_library import SCENES, _game_running

from .decks import scan_banlists, scan_decks, scan_windbot
from .game_data import Card, local_path, scan_databases, scan_repositories
from .model import DoctorReport, Finding, finding
from .platform_checks import directory_writable, game_executable, game_version, lock_active
from .settings import scan_settings

# Rounded-up planning allowances based on the observed installation and HD image sizes.
_CARD_BYTES = 256 * 1024
_FIELD_BYTES = 128 * 1024
_MIB = 1024**2


def _artwork(
    root: Path,
    paths: set[Path],
    database_rows: dict[Path, dict[int, Card]],
    findings: list[Finding],
) -> None:
    id_to_name = {}
    fields = set()
    for path in sorted(paths):
        for code, card in database_rows.get(path, {}).items():
            id_to_name[code] = card.name
            if card.type & FIELD_SPELL_TYPE == FIELD_SPELL_TYPE:
                fields.add(code)
    try:
        pics = local_path(root, root / "pics")
        local_path(root, pics / "field")
        stats = scan_artwork_coverage(str(pics), id_to_name, fields)
        findings.append(finding("artwork.coverage", path=pics, **stats))
    except (OSError, ValueError):
        findings.extend((finding("artwork.unavailable"), finding("storage.unknown")))
        return

    if not stats["cards_total"]:
        findings.append(finding("storage.unknown"))
        return
    try:
        disk_path = pics if pics.is_dir() else root
        free = shutil.disk_usage(disk_path).free
    except OSError:
        findings.append(finding("storage.unknown", path=pics))
        return
    missing = (stats["cards_total"] - stats["cards_have"]) * _CARD_BYTES
    missing += (stats["fields_total"] - stats["fields_have"]) * _FIELD_BYTES
    full = stats["cards_total"] * _CARD_BYTES + stats["fields_total"] * _FIELD_BYTES
    findings.append(
        finding(
            "storage.low" if free < missing else "storage.enough",
            path=disk_path,
            free_bytes=free,
            estimated_bytes=missing,
            full_estimate_bytes=full,
            free_mib=free // _MIB,
            estimated_mib=math.ceil(missing / _MIB),
        )
    )


def _state(root: Path, config: Path, findings: list[Finding]) -> None:
    audio = root / "sound" / ".edopro-hd-sync"
    locks = [
        root / "textures" / ".edopro-hd-sync.lock",
        root / "sound" / ".edopro-hd-sync.lock",
        audio / ".audio-settings.lock",
        *(audio / scene / ".music.lock" for scene in SCENES),
    ]
    for marker in locks:
        try:
            path = local_path(root, marker)
            if not path.is_file():
                continue
            active = lock_active(path)
        except (OSError, ValueError):
            active = None
        status = {True: "busy", False: "idle", None: "unknown"}[active]
        findings.append(finding("state.lock_" + status, path=marker))

    journals = [(root, audio / scene / "pending.json", "music") for scene in SCENES]
    journals.extend(
        (config.parent, _group_dir(root, str(config), category) / journal, category)
        for category in sorted(_UNDO_GROUPS)
        for journal in ("installing.json", "restoring.json")
    )
    interrupted = False
    for boundary, path, category in journals:
        try:
            present = local_path(boundary, path).is_file()
        except (OSError, ValueError):
            present = False
        if present:
            interrupted = True
            findings.append(finding("state.interrupted", path=path, category=category))
    if not interrupted:
        findings.append(finding("state.clean"))


def inspect_installation(
    installation: str | Path,
    *,
    config_path: str | Path | None = None,
) -> DoctorReport:
    """Return a local report. Paths in evidence are intended for the player's own device."""
    root = Path(installation).resolve()
    studio_version = app_version()
    default = Path(user_data_dir()) / CONFIG_FILENAME
    config = Path(config_path if config_path is not None else default).resolve()
    findings: list[Finding] = []
    executable = game_executable(root)
    try:
        artwork_paths = {Path(path) for path in get_db_files(str(root))}
    except OSError:
        artwork_paths = set()
    version = None
    if executable:
        version = game_version(executable)
        findings.append(finding("installation.found", path=executable, version=version))
        if not version:
            findings.append(finding("installation.version_unknown", path=executable))
    elif artwork_paths:
        findings.append(finding("installation.data_only", path=root))
    else:
        findings.append(finding("installation.missing", path=root))
        return DoctorReport(root, studio_version, None, tuple(findings))

    access: dict[Path, bool | None] = {}
    for directory in (root, root / "pics", root / "pics" / "field", root / "deck", root / "config"):
        try:
            path = local_path(root, directory)
            if path.is_dir():
                access[directory] = directory_writable(path)
        except (OSError, ValueError):
            access[directory] = None
    if False in access.values():
        state = "read_only"
    elif not access or None in access.values():
        state = "write_unknown"
    else:
        state = "writable"
    findings.append(finding("installation." + state, folders=access))

    try:
        status = "running" if _game_running(root) else "closed"
    except (OSError, RuntimeError):
        status = "unknown"
    findings.append(finding("process." + status))

    repositories = scan_repositories(root, findings)
    cards, mappings, rows = scan_databases(root, repositories, artwork_paths, findings)
    banlists = scan_banlists(root, repositories, findings)
    _artwork(root, artwork_paths, rows, findings)
    scan_decks(root, cards, mappings, findings)
    scan_settings(root, findings)
    scan_windbot(root, cards, mappings, banlists, findings)
    _state(root, config, findings)
    return DoctorReport(root, studio_version, version, tuple(findings))
