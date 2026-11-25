"""Daily, conditional checks of the latest stable EDOPal release. Call from a job."""

from __future__ import annotations

import json
import re
import sqlite3
import ssl
import time
from contextlib import closing
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi

from edopal import REPO, __version__

RELEASES = f"https://github.com/{REPO}/releases"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_DAY = 24 * 60 * 60
_NUMBER = r"(0|[1-9][0-9]*)"
_IDENTIFIERS = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_VERSION = re.compile(rf"v?{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-({_IDENTIFIERS}))?(?:\+{_IDENTIFIERS})?")


def _version_key(version: str) -> tuple:
    match = _VERSION.fullmatch(version)
    if not match:
        raise ValueError(f"Invalid release version {version!r}")
    major, minor, patch, prerelease = match.groups()
    identifiers = []
    for identifier in prerelease.split(".") if prerelease else ():
        if identifier.isdigit():
            if len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError(f"Invalid numeric prerelease identifier {identifier!r}")
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier))
    return int(major), int(minor), int(patch), prerelease is None, tuple(identifiers)


def _installed_key(version: str) -> tuple:
    # Python's wheel metadata uses e.g. 6.0.0.dev0 and 6.0.0rc1.
    normalized = re.sub(r"^(\d+\.\d+\.\d+)\.dev(\d+)$", r"\1-0.dev.\2", version)
    aliases = {"a": "alpha", "b": "beta", "rc": "rc"}
    normalized = re.sub(
        r"^(\d+\.\d+\.\d+)(a|b|rc)(\d+)$",
        lambda m: f"{m[1]}-{aliases[m[2]]}.{m[3]}",
        normalized,
    )
    return _version_key(normalized)


@dataclass(frozen=True)
class UpdateCheck:
    latest: str | None
    available: bool
    failed: bool

    @property
    def url(self) -> str:
        return f"{RELEASES}/tag/{quote(self.latest, safe='')}" if self.latest else RELEASES


def _fetch_release(tag: str | None, etag: str | None) -> tuple[str, str | None]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"EDOPal/{__version__}"}
    if tag and etag:
        headers["If-None-Match"] = etag
    request = Request(_API, headers=headers)
    try:
        response = urlopen(request, timeout=5, context=ssl.create_default_context(cafile=certifi.where()))
    except HTTPError as exc:
        with exc:
            if exc.code == 304 and tag and etag:
                return tag, exc.headers.get("ETag", etag)
            raise
    with response:
        limit = 512 * 1024
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("Release response exceeded the size limit")
        release = json.loads(body)
        if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
            raise ValueError("Missing release tag")
        tag = release["tag_name"]
        _version_key(tag)
        return tag, response.headers.get("ETag")


def check_for_updates(cache: Path, current: str = __version__) -> UpdateCheck:
    """Even simultaneous application instances share one GitHub request per rolling day."""
    installed = _installed_key(current)
    cache.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cache / "releases.sqlite3", timeout=12)) as db, db:
        # Serialize the read, refresh and write. An interrupted refresh rolls back cleanly.
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS latest (id INTEGER PRIMARY KEY, checked REAL, tag TEXT, etag TEXT, failed INT)"
        )
        row = db.execute("SELECT checked, tag, etag, failed FROM latest WHERE id = 1").fetchone()
        now = time.time()
        checked, tag, etag, failed = row or (None, None, None, False)
        if checked is None or not 0 <= now - checked < _DAY:
            try:
                tag, etag = _fetch_release(tag, etag)
            except (OSError, HTTPException, ValueError):
                failed = True
            else:
                failed = False
            db.execute("INSERT OR REPLACE INTO latest VALUES (1, ?, ?, ?, ?)", (now, tag, etag, failed))
    return UpdateCheck(tag, bool(tag and _version_key(tag) > installed), bool(failed))
