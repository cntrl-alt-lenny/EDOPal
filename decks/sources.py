"""Discover local, read-only inputs for the deck index."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from card_databases import get_db_files


@dataclass
class Sources:
    databases: list[Path] = field(default_factory=list)
    mappings: list[Path] = field(default_factory=list)
    strings: list[Path] = field(default_factory=list)
    lists: list[Path] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def bounded_text(path: Path, limit: int) -> str:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Expected a regular file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"File exceeds the {limit}-byte limit")
    if b"\0" in raw:
        raise ValueError("Binary data in a text file")
    return raw.decode("utf-8-sig")


def identity(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _relative(parent: Path, value: object, game: Path) -> Path:
    if not isinstance(value, str) or PureWindowsPath(value).anchor:
        raise ValueError("Expected an installation-relative repository path")
    path = (parent / value).resolve()
    if not path.is_relative_to(game):
        raise ValueError("Repository path points outside the installation")
    return path


def discover(game: Path) -> Sources:
    result = Sources()

    def add(target: list[Path], path: Path) -> None:
        if path.is_file() and path.resolve().is_relative_to(game) and path not in target:
            target.append(path)

    def folder(target: list[Path], path: Path, suffix: str) -> None:
        if path.is_dir() and path.resolve().is_relative_to(game):
            for file in sorted(path.iterdir()):
                if file.suffix.lower() == suffix:
                    add(target, file)

    repos: dict[Path, dict] = {}
    for filename in ("configs.json", "user_configs.json"):
        path = game / "config" / filename
        try:
            if not path.is_file() or not path.resolve().is_relative_to(game):
                continue
            config = json.loads(bounded_text(path, 1024 * 1024))
            if not isinstance(config, dict) or not isinstance(config.get("repos", []), list):
                raise ValueError("Expected a repository list")
            for repo in config.get("repos", []):
                if not isinstance(repo, dict):
                    continue
                try:
                    root = repo.get("repo_path")
                    if not root:
                        name = repo.get("repo_name")
                        if not name:
                            url = repo.get("url", "")
                            if not isinstance(url, str):
                                raise ValueError("Expected a repository URL")
                            name = Path(urlsplit(url).path).stem
                        if not name:
                            continue
                        root = f"repositories/{name}"
                    repos[_relative(game, root, game)] = repo
                except (ValueError, TypeError, OSError, RuntimeError) as exc:
                    result.issues.append(f"{path}: {exc}")
        except (OSError, ValueError, RecursionError) as exc:
            result.issues.append(f"{path}: {exc}")

    excluded = {
        root
        for root, repo in repos.items()
        if not repo.get("should_read", True) or repo.get("is_language")
    }
    try:
        for filename in get_db_files(str(game), unreadable=result.issues, confined=True):
            db = Path(filename)
            if not any(db.is_relative_to(root) for root in excluded):
                add(result.databases, db)
        for base in (game, game / "expansions"):
            add(result.lists, base / "lflist.conf")
            add(result.mappings, base / "mappings.json")
        add(result.mappings, game / "config/mappings.json")
        add(result.strings, game / "config/strings.conf")
        add(result.strings, game / "expansions/strings.conf")
        folder(result.lists, game / "lflists", ".conf")
    except (OSError, RuntimeError) as exc:
        result.issues.append(str(exc))

    configured_databases: list[Path] = []
    for root, repo in repos.items():
        if root in excluded:
            continue
        try:
            data = _relative(root, repo.get("data_path", ""), game)
            lists = _relative(root, repo.get("lflist_path") or "lflists", game)
            folder(configured_databases, data, ".cdb")
            add(result.strings, data / "strings.conf")
            add(result.mappings, data / "mappings.json")
            add(result.lists, data / "lflist.conf")
            folder(result.lists, lists, ".conf")
        except (ValueError, OSError, RuntimeError) as exc:
            result.issues.append(f"{root}: {exc}")
    # A delta may also be in an active data folder. Load it once at its position in
    # that repository's sorted CDBs, where it can supersede an older base printing.
    configured = set(configured_databases)
    result.databases = [p for p in result.databases if p not in configured] + configured_databases
    return result
