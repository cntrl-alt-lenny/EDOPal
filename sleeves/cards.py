"""Local search is independent of artwork access. Fetch only on explicit selection."""

import asyncio
import itertools
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from card_databases import installed_card_names
from config import DEFAULTS
from state_lock import locked

from .imaging import MAX_BYTES, decode, ensure_gui, read_source


@dataclass(frozen=True)
class Card:
    passcode: int
    name: str


class CardIndex:
    """Construct once per selected installation on a worker. Search uses only memory."""

    def __init__(self, installation: Path) -> None:
        cards = installed_card_names(installation)
        self._rows = sorted(
            (name.strip().casefold(), str(i), Card(i, name.strip()))
            for i, name in cards.items()
            if isinstance(i, int) and i > 0 and name.strip()
        )

    def search(self, query: str, limit: int = 50) -> list[Card]:
        """Case-insensitive name substring or numeric passcode prefix, exact code first."""
        q = query.strip().casefold()
        if limit <= 0:
            return []
        if q.isdecimal():
            q = q.lstrip("0") or "0"
            matches = [card for _, code, card in self._rows if code.startswith(q)]
            matches.sort(key=lambda card: str(card.passcode) != q)
            return matches[:limit]
        return list(itertools.islice((card for name, _, card in self._rows if q in name), limit))


class ArtMissing(LookupError):
    """The provider returned 404. This card can be checked again after 14 days."""


class ArtUnavailable(OSError):
    """Transient network, image or storage problem. The player can retry immediately."""


ART_CACHE_BYTES = 128 * 1024**2
_gate = threading.Lock()


def _valid(data: bytes) -> bool:
    try:
        decode(bytes(data))
        return True
    except ValueError:
        return False


class CardArt:
    """Pass library.root for shared disk caching. fetch() serializes requests across clients."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "card-art"

    async def fetch(self, passcode: int) -> Path:
        """Return the cached original, raise ArtMissing or ArtUnavailable. Safe to await in UI."""
        if type(passcode) is not int or not 0 < passcode <= 0xFFFFFFFF:
            raise ValueError("Choose a valid card passcode.")
        ensure_gui()
        return await asyncio.to_thread(self._fetch, passcode)

    def _trim(self, keep: Path) -> Path:
        # Only numeric provider downloads belong to this disposable cache.
        rows = []
        for file in self._dir.glob("*.jpg"):
            if (
                file.stem.isascii()
                and file.stem.isdigit()
                and file.is_file()
                and not file.is_symlink()
            ):
                info = file.stat()
                rows.append((info.st_mtime_ns, file, info.st_size))
        total = sum(size for _, _, size in rows)
        for _, file, size in sorted(rows):
            if total <= ART_CACHE_BYTES:
                break
            if file != keep:
                file.unlink(missing_ok=True)
                total -= size
        return keep

    def _fetch(self, passcode: int) -> Path:
        from main import load_failure_cache

        try:
            with _gate, locked(self._dir, ".fetch.lock"):
                path = self._dir / f"{passcode}.jpg"
                if (
                    path.is_file()
                    and path.stat().st_size <= MAX_BYTES
                    and _valid(read_source(path))
                ):
                    return self._trim(path)
                misses = load_failure_cache(str(self._dir / "missing.json"))
                if passcode in misses:
                    raise ArtMissing("No cropped art is available for this card.")
                return self._trim(asyncio.run(self._request(passcode, path, misses)))
        except (OSError, ValueError) as error:
            raise ArtUnavailable("Could not load the artwork. Please try again.") from error

    async def _request(self, passcode: int, path: Path, misses: dict[int, float]) -> Path:
        from main import FetchResult, _create_http_session, _try_download, save_failure_cache

        url = f"{DEFAULTS['sources']['field']}/{passcode}.jpg"
        async with _create_http_session(limit=1, limit_per_host=1) as session:
            result = await _try_download(
                session, url, str(path), aiohttp.ClientTimeout(total=30), 1, _valid
            )
        if result is FetchResult.MISSING:
            misses[passcode] = time.time()
            recent = dict(sorted(misses.items(), key=lambda item: item[1])[-512:])
            save_failure_cache(str(self._dir / "missing.json"), recent)
            raise ArtMissing("No cropped art is available for this card.")
        if result is not FetchResult.OK:
            raise ArtUnavailable("The artwork provider could not supply the image. Try again.")
        return path
