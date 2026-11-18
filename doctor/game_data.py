"""Inventory the installed game data independently of the artwork downloader's scope."""

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .model import Finding, finding


@dataclass(frozen=True)
class Repository:
    path: Path
    source: Path
    data: Path
    lists: Path
    language: bool


@dataclass(frozen=True)
class Card:
    name: str
    alias: int
    type: int


def local_path(root: Path, path: Path) -> Path:
    """Never follow an installation resource out of the selected installation."""
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("The installed resource points outside the selected folder.")
    return path


def files_in(root: Path, directory: Path, suffix: str) -> list[Path]:
    try:
        folder = local_path(root, directory)
        return sorted(
            path
            for path in folder.iterdir()
            if path.suffix.casefold() == suffix and not path.is_symlink() and path.is_file()
        )
    except (OSError, ValueError):
        return []


def _revision(path: Path, root: Path) -> str | None:
    git = local_path(root, path / ".git")
    head = local_path(root, git / "HEAD").read_text(encoding="ascii").strip()
    if head.startswith("ref: "):
        ref = head[5:]
        if not ref.startswith("refs/") or ".." in Path(ref).parts:
            return None
        loose = local_path(root, git / ref)
        if loose.is_file():
            head = loose.read_text(encoding="ascii").strip()
        else:
            for line in (
                local_path(root, git / "packed-refs").read_text(encoding="ascii").splitlines()
            ):
                oid, _, name = line.partition(" ")
                if name == ref:
                    head = oid
                    break
    return head if re.fullmatch(r"[0-9a-fA-F]{40}", head) else None


def _entry(
    root: Path,
    source: Path,
    entry: dict[str, object],
    findings: list[Finding],
) -> tuple[Path, Repository | None, dict[str, object]]:
    def string(key: str) -> str:
        value = entry.get(key, "")
        if not isinstance(value, str):
            raise ValueError("Invalid repository option")
        return value

    name, url, configured = string("repo_name"), string("url"), string("repo_path")
    unmanaged = entry.get("not_git_repo") is True
    disabled = entry.get("should_read") is False
    if not (disabled or unmanaged or url):
        raise ValueError("Repository URL missing")
    label = name or Path(url.rstrip("/")).name
    if not configured and not label:
        raise ValueError("Repository path missing")
    path = local_path(root, root / (configured or ("repositories/" + label)))
    details: dict[str, object] = {
        "path": path,
        "source": source,
        "name": name,
        "updates_enabled": entry.get("should_update", True),
    }
    if disabled:
        findings.append(finding("repositories.disabled", **details))
        return path, None, details
    if not path.is_dir():
        findings.append(finding("repositories.missing", **details))
        return path, None, details
    if not any(item.name != ".git" for item in path.iterdir()):
        findings.append(finding("repositories.empty", **details))
        return path, None, details
    if not unmanaged:
        try:
            revision = _revision(path, root)
        except (OSError, UnicodeError, ValueError):
            revision = None
        if revision is None:
            findings.append(finding("repositories.clone_incomplete", **details))
            return path, None, details
        details["revision"] = revision
    data = local_path(root, path / string("data_path"))
    lists = local_path(root, path / (string("lflist_path") or "lflists"))
    for key, folder in {
        "data_path": data,
        "lflist_path": lists,
        "script_path": path / string("script_path"),
        "core_path": path / string("core_path"),
    }.items():
        folder = local_path(root, folder)
        if (
            string(key) or (key == "core_path" and entry.get("has_core") is True)
        ) and not folder.is_dir():
            findings.append(
                finding("repositories.resource_missing", path=folder, repository=path, resource=key)
            )
    return path, Repository(path, source, data, lists, entry.get("is_language") is True), details


def scan_repositories(root: Path, findings: list[Finding]) -> list[Repository]:
    repositories = []
    records: list[dict[str, object]] = []
    seen: set[Path] = set()
    # User repository settings are loaded before the defaults by the client.
    for name in ("user_configs.json", "configs.json"):
        source = root / "config" / name
        try:
            value = json.loads(local_path(root, source).read_text(encoding="utf-8-sig"))
            entries = value.get("repos", []) if isinstance(value, dict) else None
            if not isinstance(entries, list):
                raise ValueError("Invalid repository array")
        except FileNotFoundError:
            if name == "configs.json":
                findings.append(finding("repositories.config_missing", path=source))
            continue
        except (OSError, ValueError):
            findings.append(finding("repositories.config_invalid", path=source))
            continue
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                findings.append(finding("repositories.config_invalid", path=source, entry=index))
                continue
            # The client keeps the first configured reader for a destination.
            issues: list[Finding] = []
            try:
                path, repo, details = _entry(root, source, entry, issues)
            except (OSError, ValueError):
                findings.append(finding("repositories.config_invalid", path=source, entry=index))
                continue
            if path in seen:
                continue
            if entry.get("should_read") is not False:
                seen.add(path)
            findings.extend(issues)
            records.append(details)
            if repo:
                repositories.append(repo)
    findings.append(
        finding(
            "repositories.config",
            configured=len(records),
            available=len(repositories),
            repositories=tuple(records),
        )
    )
    return repositories


def _card_rows(path: Path) -> dict[int, Card]:
    if path.stat().st_size == 0:
        return {}
    # Immutable SQLite reads avoid creating a rollback journal or shared-memory file.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("Failed SQLite quick check")
        columns = {row[1] for row in db.execute("PRAGMA table_info(datas)")}
        alias = "d.alias" if "alias" in columns else "0"
        kind = "d.type" if "type" in columns else "0"
        rows = db.execute(
            f"SELECT d.id, t.name, {alias}, {kind} FROM datas d JOIN texts t USING(id)"
        )
        return {
            code: Card(name, int(alt or 0), int(card_type or 0))
            for code, name, alt, card_type in rows
            if type(code) is int and 0 < code <= 0xFFFFFFFF and isinstance(name, str)
        }


def _mappings(root: Path, paths: list[Path], findings: list[Finding]) -> dict[int, int]:
    mappings: dict[int, int] = {}
    for path in paths:
        try:
            local = local_path(root, path)
            value = json.loads(local.read_text(encoding="utf-8-sig"))
            pairs = value.get("mappings") if isinstance(value, dict) else None
            if not isinstance(pairs, list) or any(
                not isinstance(pair, list)
                or len(pair) != 2
                or any(type(n) is not int or not 0 < n <= 0xFFFFFFFF for n in pair)
                for pair in pairs
            ):
                raise ValueError("Invalid ID mappings")
            mappings.update(pairs)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            findings.append(finding("databases.mappings_unreadable", path=path))
    return mappings


def scan_databases(
    root: Path,
    repos: list[Repository],
    artwork_paths: set[Path],
    findings: list[Finding],
) -> tuple[dict[int, Card], dict[int, int], dict[Path, dict[int, Card]]]:
    base = root / "cards.cdb"
    sources = [base] if base.is_file() else []
    sources.extend(files_in(root, root / "expansions", ".cdb"))
    playable_repos = [repo for repo in repos if not repo.language]
    for repo in playable_repos:
        sources.extend(files_in(root, repo.data, ".cdb"))
    game_paths = tuple(dict.fromkeys(sources))
    database_rows: dict[Path, dict[int, Card]] = {}
    for path in dict.fromkeys((*game_paths, *sorted(artwork_paths))):
        try:
            rows = _card_rows(local_path(root, path))
            if rows:
                database_rows[path] = rows
            elif path == base and path.stat().st_size == 0:
                findings.append(finding("databases.placeholder", path=path))
            else:
                findings.append(finding("databases.empty", path=path))
        except (OSError, ValueError, OverflowError, sqlite3.Error):
            findings.append(finding("databases.unreadable", path=path))
    game_rows = {path: database_rows[path] for path in game_paths if path in database_rows}
    cards: dict[int, Card] = {}
    for source_rows in game_rows.values():
        cards.update(source_rows)
    if cards:
        artwork_ids = {code for path in artwork_paths for code in database_rows.get(path, {})}
        findings.append(
            finding(
                "databases.available",
                files=len(game_rows),
                cards=len(cards),
                duplicates=sum(len(rows) for rows in game_rows.values()) - len(cards),
                sources=tuple((path, len(rows)) for path, rows in game_rows.items()),
                artwork_index_cards=len(artwork_ids),
            )
        )
    else:
        findings.append(finding("databases.missing", paths=game_paths))
    map_paths = [root / "mappings.json", root / "expansions" / "mappings.json"]
    map_paths.extend(repo.data / "mappings.json" for repo in playable_repos)
    return cards, _mappings(root, map_paths, findings), database_rows
