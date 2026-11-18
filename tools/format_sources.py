"""Pinned Format Library GET responses, compatible with the research response cache."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = "https://formatlibrary.com/api/"


class CacheMiss(ValueError):
    pass


class Responses:
    def __init__(self, cache: Path, offline: bool = False) -> None:
        self.cache = cache
        self.offline = offline
        self.digests: dict[str, str] = {}
        self.requests = 0
        self.last = 0.0

    def get(self, route: str) -> bytes:
        url = API + route
        key = hashlib.sha256(url.encode()).hexdigest()
        body = self.cache / (key + ".body")
        meta = self.cache / (key + ".json")
        if not meta.exists() or not body.exists():
            if self.offline:
                raise CacheMiss(route)
            self.cache.mkdir(parents=True, exist_ok=True)
            time.sleep(max(0, self.last + 0.52 - time.monotonic()))
            self.last = time.monotonic()
            self.requests += 1
            with urlopen(
                Request(url, headers={"User-Agent": "Format-Catalogue/1.0"}), timeout=30
            ) as response:
                data = response.read()
            body.write_bytes(data)
            meta.write_bytes(json.dumps({"url": url, "status": 200}).encode())
        info = json.loads(meta.read_bytes())
        if info["url"] != url or info["status"] != 200:
            raise ValueError(f"Invalid cached response for {route}")
        data = body.read_bytes()
        self.digests[route] = hashlib.sha256(data).hexdigest()
        return data

    def json(self, route: str) -> Any:
        return json.loads(self.get(route))

    def cards(self, exclusion: str = "") -> list[dict]:
        query = f"filter={exclusion}:eq:false" if exclusion else ""
        count = self.json("cards/count" + ("?" + query if query else ""))
        cards = [
            card
            for page in range(1, math.ceil(count / 100) + 1)
            for card in self.json(
                f"cards?limit=100&page={page}&sort=id:asc" + ("&" + query if query else "")
            )
        ]
        if len(cards) != count or len({card["id"] for card in cards}) != count:
            raise ValueError("Incomplete card table or duplicate Format Library identities")
        return cards

    def restrictions(self, row: dict) -> dict[int, int]:
        # The compact endpoint takes the literal banlist title, unlike the expanded slug route.
        name = quote(row["banlist"], safe="")
        values = self.json(f"banlists/cards/{name}?category={row['category']}")
        if not values:
            raise ValueError(f"Empty restriction response for {row['name']}")
        limits = {
            "forbidden": 0,
            "limited": 1,
            "semi-limited": 2,
            "unlimited": 3,
            "no longer on list": 3,
        }
        return {int(key): limits[value] for key, value in values.items()}

    def _events(self, name: str) -> Iterator[dict]:
        page = 1
        seen = set()
        while True:
            query = urlencode(
                dict(page=page, limit=12, sort="startedAt:desc", filter="format:eq:" + name)
            )
            events = self.json("events?" + query)
            if not isinstance(events, list):
                raise ValueError(f"Invalid event search for {name}")
            for event in events:
                if event["id"] in seen:
                    raise ValueError(f"Repeated event while paging results for {name}")
                seen.add(event["id"])
                yield event
            if len(events) < 12:
                return
            page += 1

    def samples(self, name: str) -> Iterator[tuple[str, dict, bytes]]:
        used = 0
        seen: set[int] = set()
        for event in self._events(name):
            if event.get("display") is False or not event.get("abbreviation"):
                continue
            code = event["abbreviation"]
            detail = self.json("events/" + quote(code, safe=""))
            decks = [
                d
                for d in detail["topDecks"]
                if d.get("display") is not False and d.get("id") and d["id"] not in seen
            ]
            if not decks:
                continue
            accepted = 0
            for item in decks:
                did = item["id"]
                deck = self.json(f"decks/{did}")
                if deck.get("display") is False:
                    continue
                if detail["event"]["formatName"] != name or deck["eventId"] != event["id"]:
                    raise ValueError(f"Deck {did} does not belong to {code} / {name}")
                yield detail["event"]["name"], deck, self.get(f"decks/download/{did}")
                seen.add(did)
                accepted += 1
                if accepted == 2:
                    break
            used += bool(accepted)
            if used == 2:
                break
