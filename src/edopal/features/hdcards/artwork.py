"""Artwork printing selection, bounded image decoding, and paced HTTP fetching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import ssl
import struct
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aiohttp
import certifi
from PIL import Image

from .reviewed import MEASURED_RUSH
from .sources import DIRECT, MAPPED, RAW_HOST, is_hd, rush_index

if TYPE_CHECKING:
    from edopal.core.install import Install

    from .catalog import Card, Catalog

CDN = "https://images.ygoprodeck.com/images"
IGNIS = "https://pics.projectignis.org:2096/pics"
MAX_BYTES = 25 * 1024**2
MAX_PIXELS = 20_000_000
MISS_TTL = 14 * 86400
# The image CDN publishes no numeric limit. Space starts evenly without bursts.
CDN_RATE = 15
CONNECTIONS_PER_HOST = 12
IGNIS_HOST = "pics.projectignis.org"
PINS = {
    **MAPPED,
    504700040: 23401840,
    504700142: 83011278,
    511000824: 83555667,
    511000825: 83555666,
    511002992: 14878871,
    511003023: 80604092,
    511000822: 57728570,
    511000823: 57728571,
    504700111: 68540058,
    511006005: 68540059,
    504700179: 14878871,
}
FIELD_PRINTS = {
    300101002: 23424603,
    300101005: 47355498,
    300102002: 22702055,
    300201001: 59197169,
    300202001: 50913601,
    300203001: 10080320,
    300304003: 90011152,
    300304020: 12644061,
    300305012: 44139064,
    101403062: 19832229,
}
ERA = re.compile(r" (?:\((?:GOAT|Pre-Errata)\)|GOAT|Pre-Errata)$", re.IGNORECASE)


def is_historical(name: str) -> bool:
    return bool(ERA.search(name))


def load_pins(path: Path) -> dict[int, int]:
    pins = PINS.copy()
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return pins
    if isinstance(overrides, dict):
        for cid, donor in overrides.items():
            if re.fullmatch(r"[0-9]{1,10}", cid) and re.fullmatch(r"[0-9]{1,10}", str(donor)):
                if 0 < int(cid) <= 0xFFFFFFFF and 0 < int(donor) <= 0xFFFFFFFF:
                    pins[int(cid)] = int(donor)
    return pins


def candidates(card: Card, catalog: Catalog, original: bool = False, pins=None, rush=False) -> list[tuple[int, str]]:
    own = [(card.id, f"{CDN}/cards/{card.id}.jpg"), (card.id, f"{IGNIS}/{card.id}.jpg")]
    if rush and card.rush and (entry := rush_index().get(card.id)):
        own.insert(0, (card.id, entry[0]))
    match = ERA.search(card.name)
    prints = []
    if pin := (PINS if pins is None else pins).get(card.id):
        prints.append(pin)
    if match:
        base = catalog.official.get(card.name[: match.start()], [])
        prints.extend(base)
        # Only borrow an offset when the historical record explicitly identifies its printing.
        if not base and "pre-errata" in match.group().casefold() and card.alias and card.alias == card.id - 10:
            prints.append(card.alias)
    substitutes = [(p, f"{CDN}/cards/{p}.jpg") for p in dict.fromkeys(prints) if p != card.id]
    if original and match:
        return (own[::-1] if card.id == 88264988 else own) + substitutes
    return substitutes + own


def field_donors(catalog: Catalog) -> dict[int, int]:
    donors, reverse = {}, defaultdict(list)
    for cid in catalog.fields:
        card = catalog.cards[cid]
        alias = catalog.cards.get(card.alias)
        base = card.name.removesuffix(" (Anime)")
        matching_anime = alias and base != card.name and base == alias.name
        if matching_anime and alias.id >= 100_000_000 and alias.id in catalog.fields:
            reverse[alias.id].append(cid)
        donor = FIELD_PRINTS.get(cid)
        if not donor and (
            cid in (160203015, 160320032) or (matching_anime and cid not in (511030054, 511310036, 511756002))
        ):
            donor = card.alias
        if not donor:
            for suffix in (" (Action Field)", " (GOAT)", " (Skill Card)"):
                if card.name.casefold().endswith(suffix.casefold()):
                    ids = catalog.official.get(card.name[: -len(suffix)], [])
                    donor = card.alias if card.alias in ids else ids[0] if len(ids) == 1 else None
                    break
        if donor and donor != cid:
            donors[cid] = donor
    for base, alts in reverse.items():
        if len(alts) == 1:
            donors.setdefault(base, alts[0])
    return donors


def _open(data: bytes):
    if len(data) > MAX_BYTES:
        raise ValueError("Image exceeds 25 MiB")
    try:
        if data.startswith(b"\x89PNG"):
            if not data.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"):
                raise ValueError("Incomplete PNG trailer")
            with Image.open(BytesIO(data), formats=("PNG",)) as png:
                png.verify()  # Pillow's load() alone skips IDAT checksums.
        elif b"\xff\xd9" not in data[-64:]:
            raise ValueError("Incomplete JPEG trailer")
        im = Image.open(BytesIO(data), formats=("JPEG", "PNG"))
    except Image.DecompressionBombError as exc:
        raise ValueError("Image exceeds the pixel limit") from exc
    except (SyntaxError, struct.error) as exc:
        raise ValueError("Invalid artwork") from exc
    if im.width * im.height > MAX_PIXELS:
        im.close()
        raise ValueError("Image exceeds the pixel limit")
    return im


def image_dimensions(data: bytes) -> tuple[int, int]:
    with _open(data) as im:
        # _open checks JPEG headers/trailer and PNG chunk CRCs. Decode pixels
        # only when they are needed for a field crop.
        return im.size


@dataclass(frozen=True)
class ImageAsset:
    data: bytes
    width: int
    height: int
    extension: str
    printing: int
    source: str
    fallback: bool = False
    recheck_after: float = 0


def _asset(data: bytes, printing: int, source: str) -> ImageAsset:
    width, height = image_dimensions(data)
    return ImageAsset(data, width, height, "png" if data.startswith(b"\x89PNG") else "jpg", printing, source)


def _downloaded(data: bytes, printing: int, url: str) -> ImageAsset:
    asset = _asset(data, printing, url)
    community = urlsplit(url).hostname == RAW_HOST
    if community:
        entry = rush_index().get(printing)
        blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if entry != (url, blob) or asset.extension != "png" or not is_hd(asset.width, asset.height):
            raise ValueError("Rush artwork differs from the reviewed HD print")
    elif url.endswith(".jpg") and asset.extension != "jpg":
        raise ValueError("Expected a JPEG from the artwork host")
    with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
        if not community:
            image.draft("L", (1, 1))
        image.load()
    return asset


def _crop(data: bytes, card: Card) -> ImageAsset:
    bounds = (0.117, 0.182, 0.883, 0.707)
    if card.rush:
        bounds = (0.08, 0.145, 0.92, 0.684)
    elif (card.type or 0) & 0x8000000:
        bounds = (0.13, 0.19, 0.87, 0.707)
    with _open(data) as im:
        box = tuple(round(n * side) for n, side in zip(bounds, im.size * 2, strict=True))
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("Card face is too small to crop")
        crop = im.crop(box).convert("RGB")
        stream = BytesIO()
        crop.save(stream, format="JPEG", quality=92, comment=b"EDOPro field source\0derived")
        return ImageAsset(stream.getvalue(), *crop.size, "jpg", card.id, "derived", True)


class FetchError(Exception):
    """The servers could not complete a request. A later sync may succeed."""


def _retry_after(value: str | None) -> float:
    if value:
        if re.fullmatch(r"[0-9]{1,12}", value.strip()):
            return float(value)
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    return 1.0


class ArtworkClient:
    def __init__(
        self,
        cache: Path,
        install: Install,
        cancelled: Callable[[], bool] = lambda: False,
        *,
        pins=None,
        rush=False,
        attempts=2,
    ):
        self.cache = cache / "hdcards"
        self.install_key = os.path.normcase(str(install.root.resolve()))
        self.cancelled = cancelled
        self.pins = pins
        self.rush = rush
        self.attempts = attempts
        self._hits: OrderedDict[str, ImageAsset | None] = OrderedDict()
        self._hit_bytes = 0
        self._inflight = {}
        self._gates = defaultdict(asyncio.Lock)
        self._connections = defaultdict(lambda: asyncio.Semaphore(CONNECTIONS_PER_HOST))
        self._connections[RAW_HOST] = asyncio.Semaphore(1)
        self._next = defaultdict(float)
        self._catalog = None

    async def __aenter__(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / "unavailable.sqlite3"
        self.db = sqlite3.connect(path, isolation_level=None)
        self._cache_db = None
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            # Keep misses and server backoff durable. Losing an ordinary start
            # timestamp or cached body on power loss only requires a cache refill.
            self._cache_db = sqlite3.connect(path, isolation_level=None)
            self._cache_db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("CREATE TABLE IF NOT EXISTS misses (key TEXT PRIMARY KEY, until REAL NOT NULL)")
            self.db.execute("DELETE FROM misses WHERE until <= ?", (time.time(),))
            self.db.execute("CREATE TABLE IF NOT EXISTS cooldowns (host TEXT PRIMARY KEY, until REAL NOT NULL)")
            self.db.execute("DELETE FROM cooldowns WHERE until <= ?", (time.time(),))
            self.db.execute("CREATE TABLE IF NOT EXISTS downloads (url TEXT PRIMARY KEY, digest BLOB, body BLOB NOT NULL)")
            trust = ssl.create_default_context()
            trust.load_verify_locations(cafile=certifi.where())
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=trust, limit_per_host=CONNECTIONS_PER_HOST),
                timeout=aiohttp.ClientTimeout(total=20),
                trust_env=True,
            )
        except BaseException:
            if self._cache_db is not None:
                self._cache_db.close()
            self.db.close()
            raise
        return self

    async def __aexit__(self, *_):
        try:
            for task in self._inflight.values():
                task.cancel()
            await asyncio.gather(*self._inflight.values(), return_exceptions=True)
            await self.session.close()
        finally:
            self._cache_db.close()
            self.db.close()

    def _check(self):
        if self.cancelled():
            raise InterruptedError("Artwork sync cancelled")

    async def _pace(self, host: str):
        async with self._gates[host]:
            while True:
                self._check()
                now = time.time()
                delay = self._next[host] - time.monotonic()
                if delay <= 0:
                    interval = 1.0 if host in (IGNIS_HOST, RAW_HOST) else 1 / CDN_RATE
                    # A conditional write shares the start limit (and Retry-After)
                    # with any other EDOPal instance using this cache directory.
                    admitted = self._cache_db.execute(
                        "INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(host) DO UPDATE SET "
                        "until=excluded.until WHERE cooldowns.until<=?",
                        (host, now + interval, now),
                    )
                    if admitted.rowcount:
                        self._next[host] = time.monotonic() + interval
                        return
                    row = self.db.execute("SELECT until FROM cooldowns WHERE host=?", (host,)).fetchone()
                    delay = max(0.0, row[0] - now) if row else 0
                if delay > 10:
                    raise FetchError("Artwork host requested a longer cooldown")
                await asyncio.sleep(min(0.2, delay))

    async def _fetch(self, printing: int, url: str) -> ImageAsset | None:
        self._check()
        if url in self._hits:
            self._hits.move_to_end(url)
            return self._hits[url]
        # Concurrent printings can resolve to one URL. The client owns the
        # request task so one cancelled waiter cannot cancel another's result.
        request = self._inflight.get(url)
        if request is None:
            request = self._inflight[url] = asyncio.create_task(self._lookup(printing, url))
        try:
            while not request.done():
                await asyncio.wait([request], timeout=0.15)
                self._check()
            return request.result()
        finally:
            if request.done() and self._inflight.get(url) is request:
                del self._inflight[url]

    async def _lookup(self, printing: int, url: str) -> ImageAsset | None:
        row = self._cache_db.execute(
            "SELECT digest, body FROM downloads WHERE url=? AND length(body)<=?", (url, MAX_BYTES)
        ).fetchone()
        result = None
        if row and hashlib.sha256(row[1]).digest() == row[0]:
            try:
                result = _downloaded(row[1], printing, url)
            except (ValueError, OSError):
                pass
        if result is None:
            result = await self._request(printing, url)
            if result:
                self._cache_db.execute(
                    "INSERT OR REPLACE INTO downloads VALUES (?,?,?)",
                    (url, hashlib.sha256(result.data).digest(), result.data),
                )
        self._hits[url] = result
        self._hit_bytes += len(result.data) if result else 0
        while self._hit_bytes > 32 * 1024**2 or len(self._hits) > 4096:
            _, old = self._hits.popitem(last=False)
            self._hit_bytes -= len(old.data) if old else 0
        return result

    async def _request(self, printing: int, url: str) -> ImageAsset | None:
        host = urlsplit(url).hostname
        for attempt in range(self.attempts):
            # Admit a start only once a connection slot is available.
            async with self._connections[host]:
                await self._pace(host)
                try:
                    async with self.session.get(url, allow_redirects=False) as response:
                        if host == RAW_HOST and response.headers.get("X-RateLimit-Remaining") == "0":
                            reset = response.headers.get("X-RateLimit-Reset", "")
                            if re.fullmatch(r"[0-9]{1,12}", reset):
                                self.db.execute(
                                    "INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(host) DO UPDATE SET "
                                    "until=max(until,excluded.until)",
                                    (host, int(reset)),
                                )
                        if response.status == 404:
                            return None
                        if response.status == 200:
                            if (response.content_length or 0) > MAX_BYTES:
                                raise FetchError("Artwork exceeds 25 MiB")
                            data = bytearray()
                            async for chunk in response.content.iter_chunked(128 * 1024):
                                self._check()
                                if len(data) + len(chunk) > MAX_BYTES:
                                    raise FetchError("Artwork exceeds 25 MiB")
                                data.extend(chunk)
                            return _downloaded(bytes(data), printing, url)
                        if response.status not in (403, 429) and response.status < 500:
                            raise FetchError(f"Artwork host returned HTTP {response.status}")
                        retry = response.headers.get("Retry-After")
                        delay = _retry_after(retry)
                        if retry:
                            self.db.execute(
                                "INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(host) DO UPDATE SET "
                                "until=max(until,excluded.until)",
                                (host, time.time() + delay),
                            )
                        self._next[host] = max(self._next[host], time.monotonic() + delay)
                except InterruptedError:
                    raise
                except (aiohttp.ClientError, OSError, ValueError) as exc:
                    if attempt + 1 == self.attempts:
                        raise FetchError("Could not read artwork") from exc
                    self._next[host] = max(self._next[host], time.monotonic() + 1.0)
        raise FetchError("Artwork host unavailable")

    def _miss_key(self, cid, kind, urls):
        return hashlib.sha256(json.dumps([self.install_key, cid, kind, urls]).encode()).hexdigest()

    def _miss(self, key):
        return self.db.execute("SELECT 1 FROM misses WHERE key=? AND until>?", (key, time.time())).fetchone()

    def _remember(self, key):
        self._check()
        self.db.execute("INSERT OR REPLACE INTO misses VALUES (?,?)", (key, time.time() + MISS_TTL))

    async def face(self, card: Card, catalog: Catalog, original: bool = False) -> ImageAsset | None:
        self._check()
        sources = candidates(card, catalog, original, self.pins, self.rush)
        key = self._miss_key(card.id, "face", sources)
        expects_hd = card.id in DIRECT or (self.rush and card.rush and card.id in MEASURED_RUSH)
        if not expects_hd and self._miss(key):
            return None
        transient = None
        for rank, (printing, url) in enumerate(sources):
            try:
                if result := await self._fetch(printing, url):
                    if expects_hd and not is_hd(result.width, result.height):
                        transient = FetchError("The known HD print is unavailable at its source")
                        continue
                    if transient and self.attempts == 1 and not is_hd(result.width, result.height):
                        continue
                    return replace(
                        result,
                        fallback=bool(rank),
                        recheck_after=time.time() + MISS_TTL if rank and transient is None else 0,
                    )
            except FetchError as exc:
                transient = exc
        if transient:
            raise transient
        if expects_hd:
            for _, url in sources:
                if self._hits.get(url) is None:
                    self._hits.pop(url, None)
            raise FetchError("The reviewed HD print could not be downloaded")
        self._remember(key)
        return None

    async def field(
        self, card: Card, catalog: Catalog, *, local_donor: Callable[[int], bytes | None], face: Callable[[], bytes | None]
    ) -> ImageAsset | None:
        self._check()
        if self._catalog is not catalog:
            self._donors = field_donors(catalog)
            self._catalog = catalog
        prints = [card.id]
        if donor := self._donors.get(card.id):
            prints.append(donor)
        sources = [[f"{CDN}/cards_cropped/{cid}.jpg", f"{IGNIS}/field/{cid}.png"] for cid in prints]
        key = self._miss_key(card.id, "field", sources)
        missing = self._miss(key)
        error = None
        for cid, urls in zip(prints, sources, strict=True):
            if cid != card.id:
                try:
                    data = local_donor(cid)
                    if data and b"EDOPro field source\0derived" not in data[:128]:
                        return replace(
                            _asset(data, cid, f"local:{cid}"),
                            fallback=True,
                            recheck_after=time.time() + MISS_TTL if not error and not missing else 0,
                        )
                except InterruptedError:
                    raise
                except (ValueError, OSError):
                    pass
            if not missing:
                for url in urls:
                    try:
                        if result := await self._fetch(cid, url):
                            fallback = url != sources[0][0]
                            return replace(
                                result,
                                fallback=fallback,
                                recheck_after=time.time() + MISS_TTL if fallback and not error else 0,
                            )
                    except FetchError as exc:
                        error = exc
        if not missing and not error:
            self._remember(key)
        try:
            if data := face():
                return _crop(data, card)
        except InterruptedError:
            raise
        except (ValueError, OSError) as exc:
            raise FetchError("Could not crop the installed face") from exc
        if error:
            raise error
        return None
