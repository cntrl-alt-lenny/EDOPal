"""Build the bundled offline catalogue from pinned responses and a read-only game snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
if __package__ in ("", None):
    sys.path.insert(0, str(ROOT))

from config import APP_NAME  # noqa: E402
from formats.catalogue import filename, marked, validate_data  # noqa: E402
from formats.files import staged_sibling  # noqa: E402
from tools.format_history import PIN  # noqa: E402
from tools.format_pool import Index, build_pool, database, digest, effective, history_at, list_hash  # noqa: E402
from tools.format_recipes import recipe  # noqa: E402
from tools.format_sources import Responses  # noqa: E402
from tools.format_validation import check_deck, evidence  # noqa: E402


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()


def build(cache: Responses, game: Path, out: Path, built: date, pin: Path = PIN) -> dict:
    db, hashes = database(game)
    cards = cache.cards()
    index = Index(cards, db)
    excluded = {reg: {c["id"] for c in cache.cards(reg)} for reg in ("tcg", "ocg")}
    stock_file = (game / "repositories/lflists/GOAT.lflist.conf").read_bytes()
    stock_rows = [
        tuple(map(int, line.split()[:2]))
        for line in stock_file.decode("utf-8-sig").splitlines()
        if line[:1].isdigit()
    ]
    stock = dict(stock_rows)
    history = json.loads(pin.read_bytes())
    scripts = {
        int(p.stem[1:])
        for folder in (game / "script", game / "repositories/delta-bagooska/script")
        for p in folder.rglob("c*.lua")
        if p.stem[1:].isdigit()
    }
    rows = sorted(
        cache.json("formats"),
        key=lambda r: (r["category"] != "TCG", r["date"] or "9999", r["name"]),
    )
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate catalogue identity")
    recipes = [recipe(r) for r in rows]
    inputs = dict(
        databases=hashes,
        goat=digest(stock_file),
        history=digest(pin.read_bytes()),
        script_ids=digest(json_bytes(sorted(scripts))),
        responses=cache.digests,
    )
    records = []
    manifest = dict(built=built.isoformat(), inputs=inputs, formats=records)
    out.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        staged = {
            name: stack.enter_context(staged_sibling(out / name))
            for name in ("lists.zip", "recipes.json", "manifest.json")
        }
        with ZipFile(staged["lists.zip"], "w") as archive:
            for source in rows:
                row = {k: source[k] for k in ("id", "category", "name", "date", "banlist", "era")}
                records.append(row)
                name, when, region = row["name"], row["date"], row["category"].lower()
                if not when:
                    row["unsupported"] = (
                        "A card list cannot enforce the shared Genesys points budget."
                        if name == "Genesys"
                        else "This rolling format has no fixed card-pool date."
                    )
                    continue
                members = {
                    c["id"] for c in cards if c[region + "Date"] and c[region + "Date"][:10] <= when
                } - excluded[region]
                count = cache.json(f"cards/count?filter={region}:eq:true,{region}Date:lte:{when}")
                if len(members) != count:
                    raise ValueError(f"Regional pool count differs for {name}")
                bans = cache.restrictions(source)
                decisions = history_at(history, when) if region == "tcg" and name != "Goat" else {}
                pool = build_pool(row, members, bans, index, db, decisions, stock, scripts)
                if name == "Goat":
                    differences = [
                        i
                        for i, p in db.items()
                        if effective(stock, i, p.alias) != effective(pool.entries, i, p.alias)
                    ]
                    if differences:
                        raise ValueError(
                            f"Goat differs from the stock list on {len(differences)} printings: {differences[:5]}"
                        )
                    manifest["goat_parity"] = dict(
                        checked=len(db), differences=0, stock_hash=list_hash(stock_rows)
                    )
                display = f"{APP_NAME} {when[:7]} {name}"
                lines = sorted(pool.entries.items())
                sample = [
                    check_deck(event, deck, raw, row, pool, index, db)
                    for event, deck, raw in cache.samples(name)
                ]
                review = evidence(sample)
                header = [
                    f"!{display}",
                    "$whitelist",
                    f"# Format Library · {row['category']} · {when} · {row['banlist']}",
                    f"# {review['label']} · {review['slots']} slots checked",
                ]
                body = (
                    "\n".join([*header, *(f"{code} {cap}" for code, cap in lines)]) + "\n"
                ).encode()
                data = marked(row["id"], body)
                counts = Counter(pool.entries.values())
                row.update(
                    display=display,
                    file=filename(row["id"]),
                    sha256=digest(data),
                    hash=list_hash(lines),
                    regional_pool=len(members),
                    pool=len(pool.targets),
                    limits=[counts[n] for n in range(4)],
                    historical=len(pool.historical),
                    fallback=dict(pool.fallback),
                    exclusions=pool.exclusions,
                    notes=pool.notes,
                    validation=review,
                )
                validate_data(data, out / row["file"], row)
                info = ZipInfo(row["file"])
                info.create_system = 3
                info.external_attr = 0o644 << 16
                archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)
        staged["manifest.json"].write_bytes(json_bytes(manifest))
        staged["recipes.json"].write_bytes(json_bytes(recipes))
        for name, stage in staged.items():
            stage.replace(out / name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument(
        "--build-date", type=date.fromisoformat, required=True, help="Pinned ISO build date"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "assets/formats")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    cache = Responses(args.cache, args.offline)
    manifest = build(cache, args.game, args.out, args.build_date)
    print(
        f"{len(manifest['formats'])} formats. {len(cache.digests)} distinct input requests, {cache.requests} fetched."
    )


if __name__ == "__main__":
    main()
