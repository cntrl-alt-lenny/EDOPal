"""Format-by-format evidence for the public lists, including archive provenance and sampled rejections."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import date
from functools import cache
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

from ..deck import ARTWORK, HISTORICAL, artwork_replacements, read_ydk
from ..http_cache import _retry_seconds
from .pool import Database, database, effective
from .recipes import recipe
from .sources import API, SAMPLES, Responses

INPUTS = Path(__file__).with_name("inputs")
ARCHIVE = INPUTS / "archive-audit.json"
SUPPLEMENT = INPUTS / "published-samples.json"
TOURNAMENT = INPUTS / "tournament-samples.json"
ROLLING = ("Advanced", "Traditional", "Genesys")
SECTIONS = ("main", "extra", "side")


@cache
def _input(path: Path) -> dict:
    return json.loads(path.read_bytes())


def collect(responses: Responses, names: list[str], as_of: date) -> dict:
    """Preserve the previous sample size when rebuilding limits; otherwise check up to two published lists."""
    formats = {}
    for name in names:
        # Record the difference between no events and no public decklists for rolling formats.
        events = None
        if name in ROLLING:
            route = "events?" + urlencode(dict(page=1, limit=12, sort="startedAt:desc", filter=f"format:eq:{name}"))
            events = [e["abbreviation"] for e in responses.json(route) if e.get("display") is not False]
        route = "decks?" + urlencode(dict(page=1, limit=12, sort="publishDate:desc", filter=f"format:eq:{name}"))
        found = responses.json(route)
        previous = _input(ARCHIVE)["formats"].get(name, {}).get("samples", [])
        target = max(2, len(previous))
        samples = []
        for row in found:
            if row["formatName"] != name:
                raise ValueError(f"Deck search returned {row['formatName']} for {name}")
            if row.get("display") is False:
                continue
            try:
                detail = responses.json(f"decks/{row['id']}")
            except HTTPError as exc:
                if exc.code in (403, 404, 410):
                    continue
                raise
            if detail.get("display") is not True:
                continue
            if detail["formatName"] != name or detail["id"] != row["id"]:
                raise ValueError(f"Unexpected deck detail for {name}")
            samples.append(_deck_facts(detail, row))
            if len(samples) == target:
                break
        formats[name] = dict(
            format=name, search=route, events_first_page=events, decks_first_page=len(found), samples=samples
        )
    return dict(checked=as_of.isoformat(), responses=responses.digests, formats=formats)


def _deck_facts(detail: dict, search: dict) -> dict:
    # The detail contains the published YDK. Reading it avoids incrementing the download counter.
    raw = read_ydk(detail["ydk"])
    sections = {}
    for section, original in zip(SECTIONS, raw, strict=True):
        sections[section] = dict(
            ydk=dict(Counter(map(int, original))),
            detail=[
                [code, label, n]
                for (code, label), n in Counter((int(c["artworkId"]), c["name"]) for c in detail[section]).items()
            ],
        )
    return dict(
        deck=detail["id"],
        format=detail["formatName"],
        event=detail.get("eventAbbreviation"),
        published=detail.get("publishDate") or search.get("publishDate"),
        origin=detail.get("origin") or search.get("origin"),
        sections=sections,
    )


def collect_tournament(responses: Responses, as_of: date) -> dict:
    supplemental = _input(SUPPLEMENT)["formats"]
    records = {}
    next_request = 0.0
    for name, archived in _input(ARCHIVE)["formats"].items():
        if not archived["samples"] or name in SAMPLES or name in supplemental:
            continue
        unavailable = []
        records[name] = dict(samples=[], unavailable=unavailable)
        # Check a previously rejected slot first to preserve actual source diagnostics in this build.
        for sample in sorted(archived["samples"], key=lambda s: not s["failures"]):
            route = f"decks/{sample['deck']}"
            base = hashlib.sha256((API + route).encode()).hexdigest()
            cached = responses.cache / (base + ".json")
            status_file = responses.cache / (base + ".status")
            status = int(status_file.read_text()) if status_file.exists() and not cached.exists() else None
            detail = None
            for attempt in range(3):
                if status:
                    break
                if not cached.exists() and not responses.offline:
                    time.sleep(max(0, next_request - time.monotonic()))
                    next_request = time.monotonic() + 1.05
                try:
                    detail = responses.json(route)
                    break
                except (HTTPError, RuntimeError) as exc:
                    http = exc if isinstance(exc, HTTPError) else exc.__cause__
                    if not isinstance(http, HTTPError):
                        raise
                    if http.code in (403, 404, 410):
                        status = http.code
                        status_file.write_text(str(status))
                        break
                    if attempt == 2 or (http.code != 429 and http.code < 500):
                        raise
                    time.sleep(_retry_seconds(http.headers.get("Retry-After"), time.time(), 60))
            if status or not detail or detail.get("display") is not True:
                unavailable.append(dict(deck=sample["deck"], status=status or "private"))
                continue
            if (detail["id"], detail["formatName"], detail["eventAbbreviation"]) != (
                sample["deck"],
                sample["deck_format"],
                sample["event"],
            ):
                unavailable.append(dict(deck=sample["deck"], status="Event or format association changed"))
                continue
            try:
                facts = _deck_facts(detail, {})
            except (KeyError, TypeError, ValueError) as exc:
                unavailable.append(dict(deck=sample["deck"], status=f"Incomplete published list: {exc}"))
                continue
            records[name]["samples"].append(facts)
            break
    return dict(checked=as_of.isoformat(), formats=records, responses=responses.digests)


def _digest(entries: dict[int, int]) -> str:
    return hashlib.sha256("".join(f"{i} {cap}\n" for i, cap in sorted(entries.items())).encode()).hexdigest()


def summarize_samples(samples: list[dict], source: str, empty_label: str) -> dict:
    def counts(rows):
        rejected = sum(bool(s["failures"] or s.get("deck_errors")) for s in rows)
        return dict(
            decks=len(rows),
            slots=sum(s["slots"] for s in rows),
            passed_decks=len(rows) - rejected,
            failed_decks=rejected,
            failed_slots=sum(len(s["failures"]) for s in rows),
        )

    if len({s["deck"] for s in samples}) != len(samples):
        raise ValueError("A published deck was counted more than once")
    total = counts(samples)
    fresh = [s for s in samples if not s.get("source", source).startswith("archiv")]
    archived = [s for s in samples if s.get("source", source).startswith("archiv")]
    slots, failed_slots, failed_decks, count = total["slots"], total["failed_slots"], total["failed_decks"], len(samples)
    if failed_decks:
        status = "partial"
        label = f"{failed_decks} of {count} sampled decks rejected · {failed_slots} rejected slots"
    elif count:
        status = "verified"
        label = f"{count} sampled decks passed · {slots} slots"
    else:
        status = "no_samples"
        label = empty_label
    return dict(
        status=status,
        label=label,
        source=source,
        **total,
        failures=failed_slots,
        fresh_decks=len(fresh),
        archived_decks=len(archived),
        fresh_counts=counts(fresh),
        archived_counts=counts(archived),
        samples=samples,
    )


def _detail_ids(section: dict, db: Database) -> dict[int, int]:
    """Match a missing artwork code only when the entire section supports a unique substitution."""
    source = [int(code) for code, count in section["ydk"].items() for _ in range(count)]
    detail = [(code, name) for code, name, count in section["detail"] for _ in range(count)]
    return artwork_replacements(source, detail, db)


def _check_sample(sample: dict, entries: dict[int, int], db: Database, name: str, *, stock: bool) -> dict:
    historical = defaultdict(list)
    if not stock:
        for code, cap in entries.items():
            p = db.get(code)
            if p and p.alias and cap and HISTORICAL.search(p.name):
                historical[p.alias, HISTORICAL.sub("", p.name).casefold()].append(code)
    families = Counter()
    slots = 0
    problems, failures, conversions = [], [], []
    # Only size constraints enter this check. Source-era host-rule fidelity is documented separately.
    category = "TCG" if stock else _input(ARCHIVE)["formats"][name]["category"]
    host = recipe(dict(name=name, id=0, category=category))
    for section in SECTIONS:
        cards = sample["sections"][section]
        artwork = _detail_ids(cards, db)
        size = sum(cards["ydk"].values())
        low, high = host[section]
        if not low <= size <= high:
            problems.append(f"{section.title()} has {size} cards (expected {low}–{high}).")
        slots += size
        for original, n in cards["ydk"].items():
            start = int(original)
            code = artwork.get(start, start)
            p = db.get(code)
            reason = None
            if not p:
                reason = "No installed printing"
            elif p.kind & 0x4000 or not p.kind & 7:
                reason = "The downloaded printing cannot be played in a deck"
            elif stock and p.scope not in (2, 3):
                reason = "This card is outside the installed TCG card pool"
            cap = effective(entries, code, p.alias) if p else None
            if stock and p:
                cap = entries.get(code, entries.get(p.alias or code, 3))
            if cap is None and p and not reason:
                choices = historical.get((p.alias or code, HISTORICAL.sub("", p.name).casefold()), [])
                if len(choices) > 1:
                    choices = [cid for cid in choices if ARTWORK.get(cid) == code]
                if len(choices) == 1:
                    code = choices[0]
                    p = db[code]
                    cap = entries[code]
            if cap is None and not reason:
                reason = "No verified printing in the historical whitelist"
            if code != start:
                conversions.extend([[section, start, code]] * n)
            family = (p.alias or code) if p else code
            for _ in range(n):
                families[family] += 1
                rejected = reason
                if not rejected and families[family] > cap:
                    rejected = f"Copy {families[family]} exceeds limit {cap}"
                if rejected:
                    failures.append(
                        dict(section=section, code=code, name=p.name if p else f"Unknown printing {code}", reason=rejected)
                    )
    return dict(
        deck=sample["deck"],
        event=sample["event"],
        deck_format=sample["format"],
        published=sample["published"],
        origin=sample["origin"],
        slots=slots,
        failures=failures,
        deck_errors=problems,
        conversions=conversions,
    )


def historical_validation(
    name: str, room_hash: str, *, entries: dict[int, int] | None = None, db: Database | None = None
) -> dict:
    archive = _input(ARCHIVE)
    archived = archive["formats"][name]
    matched = archived["hash"] == room_hash and (entries is None or archived["limits_sha256"] == _digest(entries))
    fresh = None
    if entries is not None and db is not None and TOURNAMENT.is_file():
        live = _input(TOURNAMENT)
        if name in live["formats"]:
            fresh = live["formats"][name]
    if archived["samples"] and (matched or fresh):
        merged = {}
        if matched:
            merged.update(
                {
                    s["deck"]: dict(s, source="archived_tournament_check", checked=archive["built"])
                    for s in archived["samples"]
                }
            )
        if fresh is not None:
            for withdrawn in fresh["unavailable"]:
                merged.pop(withdrawn["deck"], None)
            for sample in fresh["samples"]:
                result = _check_sample(sample, entries, db, name, stock=False)
                prior = merged.get(sample["deck"], {})
                reasons = {(f["section"], f["code"]): f["reason"] for f in prior.get("failures", [])}
                for failure in result["failures"]:
                    if reason := reasons.get((failure["section"], failure["code"])):
                        failure["previous_check_reason"] = reason
                merged[sample["deck"]] = dict(result, source="current_tournament_check", checked=live["checked"])
        source = "archived_tournament_check" if fresh is None else "tournament_deck_checks"
        result = summarize_samples(list(merged.values()), source, "Previously sampled decks are no longer available.")
        result.update(
            checked=archive["built"] if fresh is None else live["checked"],
            archive_commit=archive["commit"],
            archive_limits_sha256=archived["limits_sha256"],
        )
        if fresh is not None and fresh["unavailable"]:
            result["unavailable"] = fresh["unavailable"]
        mismatched = [s for s in result["samples"] if s["deck_format"] != name]
        if mismatched:
            result["note"] = (
                f"{len(mismatched)} sampled lists come from {name} events but carry the deck format "
                + ", ".join(sorted({s["deck_format"] for s in mismatched}))
                + " on Format Library."
            )
        return result
    supplement = _input(SUPPLEMENT)
    if name in supplement["formats"] and entries is not None and db is not None:
        sampled = supplement["formats"][name]
        result = summarize_samples(
            [_check_sample(s, entries, db, name, stock=False) for s in sampled["samples"]],
            "published_deck_check",
            "No published sample deck was available.",
        )
        result.update(checked=supplement["checked"], decks_first_page=sampled["decks_first_page"])
        return result
    empty = "No tournament samples in the recorded events." if matched else "The rebuilt limits differ from the archive."
    result = summarize_samples([], "archive", empty)
    if not matched:
        result["status"] = "changed_list"
    return result


def rolling_validation(name: str, game: Path, *, db: Database | None = None) -> dict:
    supplemental = _input(SUPPLEMENT)
    record = supplemental["formats"][name]
    checks = dict(
        checked=supplemental["checked"],
        events_first_page=record["events_first_page"],
        decks_first_page=record["decks_first_page"],
    )
    if name == "Genesys":
        result = summarize_samples([], "unsupported", "EDOPro cannot enforce the Genesys points budget.")
        return dict(result, **checks, status="unsupported")
    stock_name = {"Advanced": "0TCG.lflist.conf", "Traditional": "Traditional.lflist.conf"}[name]
    data = (game / "repositories/lflists" / stock_name).read_bytes()
    rows = data.decode("utf-8-sig").splitlines()
    (display,) = [line.removeprefix("!") for line in rows if line.startswith("!")]
    restrictions = {int(p[0]): int(p[1]) for line in rows if line and line[0].isdigit() and (p := line.split())}
    if not restrictions or "$whitelist" in rows:
        raise ValueError(f"Unexpected stock {name} list")
    if record["samples"] and db is None:
        db, _ = database(game)
    result = summarize_samples(
        [_check_sample(s, restrictions, db, name, stock=True) for s in record["samples"]],
        "stock_list_check",
        "No published sample deck was available.",
    )
    if result["samples"]:
        result["note"] = f"Public user decklists were checked against the installed {display} restrictions."
    return dict(result, **checks, stock_display=display, stock_sha256=hashlib.sha256(data).hexdigest())


def main():
    parser = argparse.ArgumentParser(description="Capture public samples for review against rebuilt lists.")
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--tournament", action="store_true", help="Capture one event deck per remaining sampled format")
    parser.add_argument("--also", action="append", default=[], help="Recheck a format whose historical limits changed.")
    args = parser.parse_args()
    if args.tournament:
        data = collect_tournament(Responses(args.cache, args.offline), args.date)
        TOURNAMENT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
        return
    needed = [name for name, row in _input(ARCHIVE)["formats"].items() if not row["samples"]]
    data = collect(Responses(args.cache, args.offline), list(dict.fromkeys([*needed, *ROLLING, *args.also])), args.date)
    SUPPLEMENT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
