"""Build the single historical formats pack from pinned inputs and read-only EDOPro data."""

from __future__ import annotations

import argparse
import gzip
import json
import pprint
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from .pool import Database, Index, Pool, build_pool, database, digest, effective, history_at, list_hash
from .profiles import profile_settings
from .sources import INPUT
from .validation import historical_validation, rolling_validation, summarize_samples

HISTORY = INPUT.with_name("tcg-history.json")
CREDITS = [
    dict(
        what="Regional card pools, restrictions and tournament samples",
        source="Format Library",
        url="https://formatlibrary.com",
    ),
    dict(what="Stock 2005.4 GOAT whitelist", source="Project Ignis", url="https://github.com/ProjectIgnis/LFLists"),
    dict(what="Card databases and historical scripts", source="Project Ignis", url="https://github.com/ProjectIgnis"),
    dict(what="Catalogue thumbnail card illustrations", source="KONAMI", url="https://www.yugioh-card.com/"),
    dict(what="HAT rules and functional errata", source="HAT Format", url="https://www.hatformat.com/relevant-errata.html"),
]


def json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()


def check_samples(samples: list[dict], row: dict, pool: Pool, db: Database) -> list[dict]:
    """Check published card identities using the selected game printings across all sections."""
    checked = []
    for sample in samples:
        families = Counter()
        total = 0
        failures = []
        deck_errors = []
        for section in ("main", "extra", "side"):
            counts = sample[section]
            if any(type(n) is not int or n <= 0 for n in counts.values()):
                raise ValueError(f"Invalid card counts in pinned deck {sample['id']}")
            section_size = sum(counts.values())
            minimum, maximum = row["host"][section]
            if not minimum <= section_size <= maximum:
                deck_errors.append(f"{section.capitalize()} has {section_size} cards (allowed {minimum}–{maximum})")
            total += section_size
            for fid, count in counts.items():
                codes = pool.targets.get(int(fid))
                if not codes:
                    failures.extend(
                        dict(section=section, fl_id=int(fid), reason="Outside the regional pool") for _ in range(count)
                    )
                    continue
                code = min(codes)
                family = db[code].alias or code
                for _ in range(count):
                    families[family] += 1
                    if families[family] > pool.entries[code]:
                        failures.append(
                            dict(
                                section=section,
                                code=code,
                                name=db[code].name,
                                reason=f"Copy {families[family]} exceeds limit {pool.entries[code]}",
                            )
                        )
        checked.append(
            dict(deck=sample["id"], event=sample["event"], slots=total, failures=failures, deck_errors=deck_errors)
        )
    return checked


def render_list(row: dict, entries: dict[int, int]) -> bytes:
    lines = [f"!{row['display']}", "$whitelist", f"# Format Library · {row['region']} · {row['date']} · {row['banlist']}"]
    lines.extend(f"{code} {cap}" for code, cap in sorted(entries.items()))
    return ("\n".join(lines) + "\n").encode()


def stock_goat(game: Path) -> tuple[bytes, list[tuple[int, int]]]:
    raw = (game / "repositories/lflists/GOAT.lflist.conf").read_bytes()
    lines = raw.decode("utf-8-sig").splitlines()
    if [line for line in lines if line.startswith("!")] != ["!2005.4 GOAT"] or "$whitelist" not in lines:
        raise ValueError("Unrecognized stock Goat whitelist")
    rows = []
    for line in lines:
        if line and line[0].isdigit():
            code, cap = map(int, line.split()[:2])
            if not 0 < code < 2**32 or cap not in range(4):
                raise ValueError("Invalid stock Goat row")
            rows.append((code, cap))
    if not rows:
        raise ValueError("Empty stock Goat whitelist")
    return raw, rows


def stock_profile(game: Path, row: dict, db: Database) -> None:
    path = game / row["stock_lflist"]
    text = path.read_text(encoding="utf-8-sig")
    displays = [s[1:] for s in text.splitlines() if s.startswith("!")]
    if len(displays) != 1:
        raise ValueError(f"Expected one stock section in {path.name}")
    rows = [tuple(map(int, line.split()[:2])) for line in text.splitlines() if line[:1].isdigit()]
    allowed = {c: p for c, p in db.items() if p.scope in (2, 3) and not p.kind & (0x4000 | 0x8000000)}
    families = {
        p.alias if p.alias in allowed and p.name == db[p.alias].name and p.kind == db[p.alias].kind else c
        for c, p in allowed.items()
    }
    row.update(
        display=displays[0],
        hash=list_hash(rows),
        stock_sha256=digest(path.read_bytes()),
        pool=len(families),
        printings=len(allowed),
        historical=None,
        fallback={},
        coverage_note="Current TCG identities in the release game database, including Forbidden cards. "
        "The installed stock list follows Project Ignis updates.",
    )


def base_row(name: str, metadata: dict) -> dict:
    row = dict(
        id=name.lower().replace(" ", "-"),
        fl_id=metadata["id"],
        name=name,
        date=metadata["date"] or "Rolling",
        snapshot_date=metadata["date"],
        region=metadata["category"],
        era=metadata["era"],
        popular=metadata["isPopular"],
        banlist=metadata["banlist"],
        banlist_date=metadata.get("banlist_date", ""),
        event=metadata["eventName"] or "",
    )
    return row | profile_settings(row)


def build(game: Path, out: Path, built: date, *, source: Path = INPUT, history_file: Path = HISTORY) -> dict:
    game = game.resolve()
    out = out.resolve()
    if out.is_relative_to(game) or game.is_relative_to(out):
        raise ValueError("Build the pack outside the game folder")
    pinned = json.loads(gzip.decompress(source.read_bytes()))
    history = json.loads(history_file.read_bytes())
    db, db_digests = database(game)
    scripts = {
        int(p.stem[1:])
        for folder in (game / "script", game / "repositories/delta-bagooska/script")
        for p in folder.rglob("c*.lua")
        if p.stem[1:].isdigit()
    }
    stock_bytes, stock_rows = stock_goat(game)
    stock = dict(stock_rows)
    index = Index(pinned["cards"], db)
    if len(index.cards) != len(pinned["cards"]):
        raise ValueError("Duplicate Format Library card IDs")
    audit = dict(
        regional_sha256=digest(source.read_bytes()),
        history_sha256=digest(history_file.read_bytes()),
        goat_sha256=digest(stock_bytes),
        databases=db_digests,
        scripts_sha256=digest(json_bytes(sorted(scripts))),
        responses=pinned["responses"],
        evidence_inputs={
            key: digest(INPUT.with_name(key).read_bytes())
            for key in ("archive-audit.json", "published-samples.json", "tournament-samples.json", "counts.json")
        },
        formats={},
    )
    products = {}
    manifest = dict(
        schema=1,
        id="formats",
        kind="format",
        name="Classic formats",
        version=built.strftime("%Y.%m.%d") + ".2",
        tested_edopro="41.0.2",
        formats=[],
        credits=CREDITS,
        files={},
    )
    for name, metadata in pinned["formats"].items():
        row = base_row(name, metadata)
        slug = row["id"]
        manifest["formats"].append(row)
        if row["status"] != "historical":
            if row["status"] == "stock":
                stock_profile(game, row, db)
            else:
                row.update(
                    pool=None,
                    historical=None,
                    printings=None,
                    fallback={},
                    coverage_note="A points budget cannot be represented by a whitelist.",
                )
            row["validation"] = rolling_validation(name, game, db=db)
            continue
        date_key = row["region"].lower() + "Date"
        members = {key for key, card in index.cards.items() if card[date_key] and card[date_key] <= row["date"]}
        if len(members) != metadata["count"]:
            raise ValueError(f"Regional pool count differs for {name}")
        bans = {int(key): cap for key, cap in metadata["bans"].items()}
        if not bans or any(type(cap) is not int or cap not in range(4) for cap in bans.values()):
            raise ValueError(f"Invalid {name} restrictions")
        decisions = history_at(history, row["date"]) if row["region"] == "TCG" and name != "Goat" else {}
        pool = build_pool(row, members, bans, index, db, decisions, stock, scripts)
        samples = check_samples(metadata["samples"], row, pool, db)
        if name == "Goat":
            # Room hashes include the stock file's duplicate row; keep the original bytes.
            if any(effective(stock, code, p.alias) != effective(pool.entries, code, p.alias) for code, p in db.items()):
                raise ValueError("Generated Goat limits differ from the installed stock whitelist")
            data, rows = stock_bytes, stock_rows
            audit["goat_parity"] = dict(checked=len(db), differences=0)
        else:
            data, rows = render_list(row, pool.entries), sorted(pool.entries.items())
        room_hash = list_hash(rows)
        if samples:
            evidence = summarize_samples(samples, "published_tournament_cards", "No published event decks.")
            evidence["checked"] = built.isoformat()
        else:
            evidence = historical_validation(name, room_hash, entries=pool.entries, db=db)
        fallback = {key: pool.fallback[key] for key in ("ambiguous", "unavailable")}
        if row["region"] == "OCG":
            fallback["unassessed"] = len(pool.targets)
        row.update(
            sha256=digest(data),
            hash=room_hash,
            pool=len(pool.targets),
            printings=len(dict(rows)),
            regional_pool=len(members),
            historical=len(pool.historical),
            fallback=fallback,
            validation=evidence,
        )
        products[row["lflist"]] = data
        audit["formats"][slug] = dict(
            exclusions=pool.exclusions,
            banlist_outside_region=pool.outside_banlist,
            samples=evidence.get("samples", []),
            historical=pool.historical,
            limits=dict(Counter(cap for _, cap in rows)),
        )
    manifest["formats"].sort(key=lambda r: (not r["popular"], r["region"] != "TCG", r["date"], r["fl_id"]))
    audit["restriction_decisions"] = [
        dict(
            region="TCG",
            banlist="June 2023",
            fl_id=1045,
            passcode=53804307,
            cap=1,
            name="Blaster, Dragon Ruler of Infernos",
            reason="Format Library has both forbidden and limited records. The June Project Ignis list caps Blaster at one.",
            url="https://github.com/ProjectIgnis/LFLists/blob/0e9f88d57d5ecabbeb7bbd20e63ce1ef6aa8df7a/0TCG.lflist.conf",
        ),
        dict(
            region="TCG",
            banlist="September 2023",
            fl_id=1045,
            passcode=53804307,
            cap=1,
            name="Blaster, Dragon Ruler of Infernos",
            reason="The September Project Ignis list confirms one copy for the duplicate FL record.",
            url="https://github.com/ProjectIgnis/LFLists/blob/71cb58b590f00cb9931496e364c3453ef6dc52cc/0TCG.lflist.conf",
        ),
        dict(
            region="OCG",
            banlist="September 2013",
            fl_id=8875,
            passcode=3280747,
            cap=0,
            name="Sixth Sense",
            reason="Format Library's expanded banlist and the official OCG list both forbid Sixth Sense (第六感).",
            url="https://www.yugioh-card.com/japan/event/limitregulation/?list=201309",
        ),
    ]
    audit["hat_event_policy"] = (
        "2014 North American WCQ excluded new Space-Time Showdown cards. "
        "Retain previously legal reprints, Realm of Light and the July 1 Shogi Rook promo."
    )
    products["provenance.json"] = json_bytes(audit)
    manifest["files"] = {key: digest(data) for key, data in products.items()}
    products["manifest.json"] = json_bytes(manifest)

    # Finish all validations before publishing. The manifest is replaced last.
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out) as tmp:
        for rel, data in products.items():
            stage = Path(tmp) / rel
            stage.parent.mkdir(exist_ok=True, parents=True)
            stage.write_bytes(data)
        for rel in products:
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / rel).replace(dest)
    return manifest


def update_summary(manifest: dict, target: Path) -> None:
    records = []
    for row in manifest["formats"]:
        keep = {key: value for key, value in row.items() if key not in ("host", "known_gaps")}
        # Detailed samples live in the pack; keep the per-profile outcome available at first launch.
        keep["validation"] = {key: value for key, value in row["validation"].items() if key != "samples"}
        records.append(keep)
    header = '"""Generated from the released formats pack by the offline builder."""\n# ruff: noqa: E501\n# fmt: off\n\n'
    target.write_text(header + "RECORDS = " + pprint.pformat(records, width=115, sort_dicts=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--build-date", type=date.fromisoformat, required=True)
    parser.add_argument("--source", type=Path, default=INPUT)
    parser.add_argument("--update-summary", action="store_true")
    args = parser.parse_args()
    result = build(args.game, args.out, args.build_date, source=args.source)
    if args.update_summary:
        update_summary(result, Path(__file__).with_name("release.py"))
    lists = sum(row["status"] == "historical" for row in result["formats"])
    print(f"{len(result['formats'])} formats, {lists} regional whitelists.")


if __name__ == "__main__":
    main()
