"""Refresh the factual release inputs from Format Library. Uses one shared regional pool."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://formatlibrary.com/api/"
INPUT = Path(__file__).with_name("inputs") / "regional.json.gz"
# Published samples from the reviewed historical-format archive.
SAMPLES = {
    "Goat": (88837, 88815, 88443, 88459),
    "Edison": (87757, 87773, 85583, 85576),
    "HAT": (88769, 88767, 88053, 88064),
    "Tengu": (87586, 86718, 86714),
    "Wind-Up": (86778, 86763, 85861, 85862),
    "Cat": (56096, 56093, 39619),
    "Trooper": (75073, 75068, 74654, 74650),
    "Chaos": (81983, 81980, 74505, 74512),
}
LIMITS = {"forbidden": 0, "limited": 1, "semi-limited": 2, "unlimited": 3, "no longer on list": 3}


class Responses:
    """A frozen response cache per source revision. Start with a new directory to refresh."""

    def __init__(self, cache: Path, offline: bool):
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.digests: dict[str, str] = {}

    def json(self, route: str):
        url = API + route
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists():
            raw = path.read_bytes()
        elif self.offline:
            raise ValueError(f"Missing pinned response: {route}")
        else:
            time.sleep(0.6)
            request = Request(url, headers={"User-Agent": "EDOPal/6 historical format pack builder"})
            try:
                with urlopen(request, timeout=40) as response:
                    raw = response.read(8 * 1024 * 1024)
                    if response.headers.get("Content-Length") and len(raw) != int(response.headers["Content-Length"]):
                        raise ValueError(f"Incomplete response: {route}")
            except HTTPError as exc:
                if exc.code == 429:
                    raise RuntimeError(f"Format Library rate limit (Retry-After: {exc.headers.get('Retry-After')})") from exc
                raise
            json.loads(raw)
            with NamedTemporaryFile(dir=self.cache, delete=False) as stream:
                stream.write(raw)
            Path(stream.name).replace(path)
        self.digests[route] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)


def regional_cards(responses: Responses, region: str, until: str, after: str = "") -> list[dict]:
    constraints = f"{region}:eq:true,{region}Date:lte:{until}"
    if after:
        constraints += f",{region}Date:gt:{after}"
    total = responses.json(f"cards/count?filter={constraints}")
    cards = []
    fields = ("id", "name", "konamiCode", "ypdId", "artworkId", "tcgDate", "ocgDate")
    for page in range(1, math.ceil(total / 100) + 1):
        rows = responses.json(f"cards?limit=100&page={page}&sort=id:asc&filter={constraints}")
        cards.extend({field: row[field] for field in fields} for row in rows)
    if len(cards) != total or len({row["id"] for row in cards}) != total:
        raise ValueError(f"Incomplete or duplicated {region} pool")
    # FL can apply only the last constraint on a repeated date field. Apply both
    # endpoints of the requested interval locally as well; regional counts guard completeness.
    return [c for c in cards if after < c[region + "Date"] <= until]


def samples(responses: Responses, name: str) -> list[dict]:
    result = []
    for deck_id in SAMPLES.get(name, ()):
        try:
            deck = responses.json(f"decks/{deck_id}")
        except HTTPError as exc:
            if exc.code in (403, 404):
                continue
            raise
        if deck.get("display") is False:
            continue
        if deck["id"] != deck_id or deck["formatName"] != name or not deck.get("eventAbbreviation"):
            raise ValueError(f"Unexpected sample deck for {name}: {deck_id}")
        sample = {key: dict(Counter(card["id"] for card in deck[key])) for key in ("main", "extra", "side")}
        result.append(dict(id=deck_id, event=deck["eventAbbreviation"], **sample))
    return result


def collect(responses: Responses, refresh_counts: bool = False) -> dict:
    formats = responses.json("formats")
    if len({f["id"] for f in formats}) != len(formats):
        raise ValueError("Duplicate format identities in the catalogue")
    regions = {f["category"].lower() for f in formats}
    if regions != {"ocg", "tcg"}:
        raise ValueError("Review the regions in the new catalogue")
    cards = {}
    members = {}
    hat_cutoff = next(f["date"] for f in formats if f["name"] == "HAT")
    for region in sorted(regions):
        end = max(f["date"] for f in formats if f["date"] and f["category"].lower() == region)
        # Preserve the reviewed HAT pages as the first slice of the larger regional source.
        if region == "tcg":
            rows = regional_cards(responses, region, hat_cutoff) + regional_cards(responses, region, end, hat_cutoff)
        else:
            rows = regional_cards(responses, region, end)
        members[region] = {row["id"] for row in rows}
        if len(members[region]) != len(rows):
            raise ValueError(f"Overlapping {region} pages")
        cards.update({row["id"]: row for row in rows})
    for card in cards.values():
        for region in regions:
            if card["id"] not in members[region]:
                card[region + "Date"] = None
    expected = json.loads(INPUT.with_name("counts.json").read_bytes())
    profiles = {}
    for row in formats:
        name, category, when = row["name"], row["category"], row["date"]
        region = category.lower()
        profile = {key: row[key] for key in ("id", "date", "banlist", "eventName", "category", "era", "isPopular")}
        profiles[name] = profile
        if not when:
            continue
        count = expected.get(name)
        if refresh_counts or count is None:
            count = responses.json(f"cards/count?filter={region}:eq:true,{region}Date:lte:{when}")
        if sum(bool(c[region + "Date"] and c[region + "Date"] <= when) for c in cards.values()) != count:
            raise ValueError(f"Regional count changed for {name}. Review source corrections using --refresh-counts.")
        expanded = responses.json(f"banlists/{quote(row['banlist'].replace(' ', '-'), safe='')}?category={category}")
        rows = [r for v in expanded.values() if isinstance(v, list) for r in v]
        if not rows or expanded["id"] != row["banlist"]:
            raise ValueError(f"Empty or unexpected {category} restrictions for {name}")
        effective_dates = {r["date"] for r in rows}
        if len(effective_dates) != 1:
            raise ValueError(f"Review the list effective date for {name}")
        bans = {}
        for entry in rows:
            fid, cap = str(entry["cardId"]), LIMITS[entry["restriction"]]
            previous = bans.setdefault(fid, cap)
            if cap != previous:
                # FL has conflicting Blaster rows in both 2023 lists. The Project Ignis
                # release banlists for June and September each admit exactly one copy.
                if (category, row["banlist"], fid) not in {("TCG", "June 2023", "1045"), ("TCG", "September 2023", "1045")}:
                    raise ValueError(f"Conflicting limits for {name}, FL ID {fid}")
                bans[fid] = 1
        profile.update(count=count, bans=bans, banlist_date=effective_dates.pop(), samples=samples(responses, name))
    return dict(cards=list(sorted(cards.values(), key=lambda r: r["id"])), formats=profiles, responses=responses.digests)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True, help="A dedicated build-time HTTP cache")
    parser.add_argument("--offline", action="store_true", help="Read all raw responses from that cache")
    parser.add_argument("--out", type=Path, default=INPUT)
    parser.add_argument(
        "--refresh-counts", action="store_true", help="Re-query historical count endpoints after corrections"
    )
    args = parser.parse_args()
    payload = collect(Responses(args.cache, args.offline), args.refresh_counts)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    args.out.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    print(f"{len(payload['cards'])} regional identities, {len(payload['responses'])} pinned responses")


if __name__ == "__main__":
    main()
