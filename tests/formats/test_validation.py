from __future__ import annotations

import json
from pathlib import Path

from edopal.features.formats.builder.pool import Printing
from edopal.features.formats.builder.validation import (
    ARCHIVE,
    ROLLING,
    SUPPLEMENT,
    historical_validation,
    rolling_validation,
)


def test_archived_evidence_is_bound_to_list_and_keeps_diagnostics():
    archive = json.loads(ARCHIVE.read_bytes())
    assert len(archive["formats"]) == 125
    assert len(archive["commit"]) == 40 and len(archive["inputs"]["responses"]) == 1423
    rows = [historical_validation(name, row["hash"]) for name, row in archive["formats"].items() if row["samples"]]
    assert sum(v["decks"] for v in rows) == 346
    assert sum(v["slots"] for v in rows) == 24133
    assert sum(v["failed_decks"] for v in rows) == sum(v["failed_slots"] for v in rows) == 3
    ocg = archive["formats"]["OCG Goat"]
    result = historical_validation("OCG Goat", ocg["hash"])
    (issue,) = [f for s in result["samples"] for f in s["failures"]]
    assert issue == dict(code=5318639, name="Mystical Space Typhoon", reason="Copy 2 exceeds limit 1", section="main")
    assert result["status"] == "partial" and result["passed_decks"] == 3
    assert historical_validation("OCG Goat", "changed")["status"] == "changed_list"
    # Correct room hash alone is insufficient when the caller provides different card rows.
    assert historical_validation("OCG Goat", ocg["hash"], entries={123: 1}, db={})["archived_decks"] == 0


def test_search_pin_covers_every_format_without_archived_tournaments():
    archive = json.loads(ARCHIVE.read_bytes())["formats"]
    supplemental = json.loads(SUPPLEMENT.read_bytes())
    missing = {name for name, row in archive.items() if not row["samples"]}
    assert missing | set(ROLLING) <= supplemental["formats"].keys()
    for name in missing:
        record = supplemental["formats"][name]
        assert len(supplemental["responses"][record["search"]]) == 64
        assert record["format"] == name
        result = historical_validation(name, archive[name]["hash"], entries={}, db={})
        assert result["checked"] == supplemental["checked"]
        assert result["decks_first_page"] == 0 and result["status"] == "no_samples"


def test_recorded_rolling_samples_check_installed_list_and_note_age(tmp_path: Path):
    pin = json.loads(SUPPLEMENT.read_bytes())["formats"]
    advanced = pin["Advanced"]
    assert advanced["decks_first_page"] == 12 and advanced["events_first_page"] == []
    cards = {
        code: Printing(name, 0, 3, 1)
        for sample in advanced["samples"]
        for section in sample["sections"].values()
        for code, name, _count in section["detail"]
    }
    # The detail response names Bagooska’s alternate artwork; the original YDK uses its base ID.
    cards[90590303] = Printing(cards[90590304].name, 0, 3, 1)
    lists = tmp_path / "repositories/lflists"
    lists.mkdir(parents=True)
    (lists / "0TCG.lflist.conf").write_text("!Current TCG\n24224830 0\n90590303 0\n")
    (lists / "Traditional.lflist.conf").write_text("!Current Traditional\n24224830 1\n")
    result = rolling_validation("Advanced", tmp_path, db=cards)
    assert (result["decks"], result["passed_decks"], result["failed_decks"], result["failed_slots"]) == (2, 1, 1, 2)
    assert {s["deck_format"] for s in result["samples"]} == {"Advanced"}
    assert result["samples"][1]["published"].startswith("2024")
    assert rolling_validation("Traditional", tmp_path, db=cards)["status"] == "no_samples"
    assert rolling_validation("Genesys", tmp_path)["status"] == "unsupported"


def test_fresh_tournament_evidence_covers_every_remaining_sampled_format():
    from edopal.features.formats.builder.sources import SAMPLES
    from edopal.features.formats.builder.validation import TOURNAMENT

    archive = json.loads(ARCHIVE.read_bytes())["formats"]
    supplement = json.loads(SUPPLEMENT.read_bytes())["formats"]
    current = json.loads(TOURNAMENT.read_bytes())
    expected = {n for n, r in archive.items() if r["samples"] and n not in SAMPLES and n not in supplement}
    assert expected == current["formats"].keys()
    for name, row in current["formats"].items():
        assert row["samples"] or row["unavailable"]
        if row["samples"]:
            (sample,) = row["samples"]
            archived = {old["deck"]: old for old in archive[name]["samples"]}
            assert archived[sample["deck"]]["event"] == sample["event"]
            assert archived[sample["deck"]]["deck_format"] == sample["format"]
            assert len(current["responses"][f"decks/{sample['deck']}"]) == 64


def test_fresh_event_replaces_archived_deck_id_in_total(monkeypatch):
    from edopal.features.formats.builder import validation

    old = validation._input
    name = "OCG Goat"
    entries = {5318639: 1}
    db = {5318639: Printing("Mystical Space Typhoon", 0, 3, 2)}

    def alternate_list(path):
        if path != ARCHIVE:
            return old(path)
        archived = json.loads(ARCHIVE.read_bytes())
        archived["formats"][name]["limits_sha256"] = validation._digest(entries)
        return archived

    monkeypatch.setattr(validation, "_input", alternate_list)
    room_hash = old(ARCHIVE)["formats"][name]["hash"]
    result = historical_validation(name, room_hash, entries=entries, db=db)
    assert (result["decks"], result["fresh_decks"], result["archived_decks"]) == (4, 1, 3)
    assert len({s["deck"] for s in result["samples"]}) == 4
    assert result["fresh_counts"]["slots"] + result["archived_counts"]["slots"] == result["slots"]
    assert result["fresh_counts"]["failed_decks"] == 1
