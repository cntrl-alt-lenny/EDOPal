"""Cache anonymous Format Library GETs, including validators and server-wide backoff."""

from __future__ import annotations

import math
import sqlite3
import ssl
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC
from email.message import Message
from email.utils import parsedate_to_datetime
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

T = TypeVar("T")


class DeckError(ValueError):
    """A message suitable for the format browser."""


class ItemUnavailable(DeckError):
    """An individual event, deck or thumbnail has been withdrawn."""


class SourceUnavailable(DeckError):
    """Wait for Format Library or the connection to recover."""


def _retry_seconds(value: str | None, now: float, default: int) -> float:
    if value:
        try:
            if value.isascii() and value.isdecimal():
                return float(int(value))
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(0.0, date.timestamp() - now)
        except (ValueError, OverflowError, OSError, TypeError):
            pass
    return default


def _get(url: str, etag: str | None, limit: int) -> tuple[int, Message, bytes]:
    headers = {"User-Agent": "EDOPal/6 (+https://github.com/cntrl-alt-lenny/EDOPal)"}
    if etag:
        headers["If-None-Match"] = etag
    request = Request(url, headers=headers)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(certifi.where())
    try:
        with urlopen(request, timeout=8, context=ctx) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise DeckError("Format Library returned a file too large for this view.")
            length = response.headers.get("Content-Length")
            if length is not None and not response.headers.get("Transfer-Encoding"):
                if not length.isdecimal() or len(data) != int(length):
                    raise IncompleteRead(data)
            return response.status, response.headers, data
    except HTTPError as exc:
        with closing(exc):
            return exc.code, exc.headers, b""


class HttpCache:
    def __init__(self, folder: Path):
        self.path = folder / "format-library.sqlite3"
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def stale(self) -> bool:
        return getattr(self._local, "stale", False)

    def forget(self, urls: tuple[str, ...]) -> None:
        if self.path.is_file():
            with self._lock, closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                db.executemany("DELETE FROM entries WHERE url=?", [(url,) for url in urls])

    def read(self, url: str, ttl: int, limit: int, parse: Callable[[bytes], T]) -> T:
        self._local.stale = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Coalesce concurrent selections of a deck on this client. Each thread opens its own connection.
        with self._lock, closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS entries (url TEXT PRIMARY KEY, saved REAL, etag TEXT, body BLOB)")
            db.execute("CREATE TABLE IF NOT EXISTS backoff (id INTEGER PRIMARY KEY, until REAL)")
            now = time.time()
            cached = db.execute("SELECT saved, etag, body FROM entries WHERE url=?", (url,)).fetchone()
            if cached and now - cached[0] < ttl:
                return parse(cached[2])

            def fallback(error: Exception) -> T:
                if cached:
                    self._local.stale = True
                    return parse(cached[2])
                raise error

            cooldown = db.execute("SELECT until FROM backoff WHERE id=1").fetchone()
            if cooldown and cooldown[0] > now:
                seconds = math.ceil(cooldown[0] - now)
                return fallback(SourceUnavailable(f"Format Library is temporarily unavailable. Try again in {seconds} s."))
            try:
                status, headers, body = _get(url, cached[1] if cached else None, limit)
            except (URLError, OSError, HTTPException) as exc:
                db.execute("INSERT OR REPLACE INTO backoff VALUES (1,?)", (time.time() + 10,))
                message = "Couldn’t reach Format Library. Check your connection and try again."
                if isinstance(exc, IncompleteRead):
                    message = "The download from Format Library was interrupted. Try again."
                return fallback(SourceUnavailable(message))
            except DeckError as exc:
                return fallback(exc)

            if status in (403, 404, 410):
                db.execute("DELETE FROM entries WHERE url=?", (url,))
                raise ItemUnavailable("This item is no longer publicly available on Format Library.")
            if status == 304 and cached:
                db.execute(
                    "UPDATE entries SET saved=?, etag=? WHERE url=?", (time.time(), headers.get("ETag", cached[1]), url)
                )
                return parse(cached[2])
            if status != 200:
                if status == 429 or status >= 500:
                    delay = _retry_seconds(headers.get("Retry-After"), time.time(), 60 if status == 429 else 20)
                    db.execute("INSERT OR REPLACE INTO backoff VALUES (1,?)", (time.time() + delay,))
                if status == 429:
                    return fallback(
                        SourceUnavailable("Format Library is receiving too many requests. Please try again later.")
                    )
                return fallback(SourceUnavailable(f"Format Library returned HTTP {status}. Try again later."))
            try:
                result = parse(body)
            except ItemUnavailable:
                db.execute("DELETE FROM entries WHERE url=?", (url,))
                raise
            except DeckError as exc:
                return fallback(exc)
            db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?)", (url, time.time(), headers.get("ETag"), body))
            db.execute("DELETE FROM entries WHERE url IN (SELECT url FROM entries ORDER BY saved DESC LIMIT -1 OFFSET 192)")
            return result
