"""Refresh the pinned TCG printing decisions from the reviewed retroformats chronology.

Run as a module with --retro pointing to an edopro-retro-formats checkout.
The release build consumes the exported decisions without importing that project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from tools.format_sources import Responses

PIN = Path(__file__).with_name("format_inputs") / "tcg-history.json"


def export(retro: Path, cache: Path, output: Path) -> None:
    sys.path.insert(0, str(retro.resolve()))
    from retroformats.model import load_erratum_record

    dates = sorted(
        {
            r["date"]
            for r in Responses(cache, True).json("formats")
            if r["category"] == "TCG" and r["date"]
        }
    )
    records = {}
    fingerprint = hashlib.sha256()
    for path in sorted((retro / "data/errata").glob("*.json")):
        raw = path.read_bytes()
        data = json.loads(raw)
        if data["review"]["status"] != "reviewed":
            continue
        fingerprint.update(hashlib.sha256(raw).digest())
        record = load_erratum_record(data, path)
        changes = {}
        previous = None
        for when in dates:
            result = record.selection_at(date.fromisoformat(when))
            decision: str | list[int] = "modern"
            if result.chronology != "determinate":
                decision = "ambiguous"
            elif not result.is_modern:
                cov = result.candidates[0].coverage
                if cov.kind.value == "reuse-upstream" and cov.historical_passcode:
                    decision = sorted({cov.historical_passcode, *cov.historical_variant_passcodes})
                elif cov.kind.value not in ("none-needed", "modern"):
                    decision = "unavailable"
            if decision != previous:
                changes[when] = decision
            previous = decision
        records[str(record.modern_card.passcode)] = changes
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        (
            json.dumps(
                dict(source_sha256=fingerprint.hexdigest(), dates=dates, cards=records),
                sort_keys=True,
                indent=1,
            )
            + "\n"
        ).encode()
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retro", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=PIN)
    args = parser.parse_args()
    export(args.retro, args.cache, args.output)
