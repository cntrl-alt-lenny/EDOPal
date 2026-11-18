import asyncio
import csv
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

import main
from artwork_coverage import crop_field_face, field_source
from artwork_flow import coverage_summary, sync_summary
from card_databases import field_donor
from config import Config
from tests.test_field_art import JPEG_BODY, _FakeResponse


def picture(fmt: str, width: int = 200, height: int = 300) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    for y in range(height // 8, 3 * height // 4):
        for x in range(width // 2, width):
            image.setPixelColor(x, y, QColor("blue"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buf, fmt)
    return bytes(buf.data())


@pytest.mark.parametrize(
    ("layout", "dimensions"),
    [
        ("card", (154, 157)),
        ("rush", (168, 161)),
        ("skill", (148, 155)),
    ],
)
def test_crop_decodes_at_native_layout_dimensions(
    tmp_path: Path, layout: str, dimensions: tuple[int, int]
) -> None:
    face = tmp_path / "face.jpg"
    face.write_bytes(picture("JPEG"))
    result = crop_field_face(face, layout)
    assert main._looks_like_jpeg(result)
    image = QImage.fromData(result)
    assert image.size().toTuple() == dimensions
    assert image.pixelColor(10, 10).red() > 245
    assert image.pixelColor(image.width() - 10, 10).blue() > 245


def database(path: Path, rows: list[tuple[int, int, int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE datas(id PRIMARY KEY,type,alias); CREATE TABLE texts(id PRIMARY KEY,name);"
        )
        db.executemany("INSERT INTO datas VALUES (?,?,?)", [r[:3] for r in rows])
        db.executemany("INSERT INTO texts VALUES (?,?)", [(r[0], r[3]) for r in rows])
        db.commit()


@pytest.fixture
def run_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable:
    cfg = Config(
        [
            "--edopro-path",
            str(tmp_path),
            "--config",
            str(tmp_path / "cfg.json"),
            "--quiet",
            "--no-textures",
            "--max-retries",
            "0",
        ]
    )
    calls: list[str] = []
    responses: dict[str, tuple[int, bytes]] = {}

    class Session:
        def get(self, url: str, **kwargs: object) -> _FakeResponse:
            key = url.split(".test/")[1]
            calls.append(key)
            return _FakeResponse(*responses.get(key, (404, b"")))

    cfg.sources = {key: f"https://art.test/{key}" for key in cfg.sources}
    monkeypatch.setattr(main, "_create_http_session", lambda **kw: nullcontext(Session()))
    monkeypatch.setattr(main, "notify_if_update_available", AsyncMock())

    def run(replies: dict[str, tuple[int, bytes]] | None = None) -> tuple:
        calls.clear()
        responses.clear()
        responses.update(replies or {})
        return cfg, asyncio.run(main.run(cfg)), calls.copy()

    return run


def test_helper_rows_tokens_and_overlapping_release(tmp_path: Path, run_sync: Callable) -> None:
    database(
        tmp_path / "cards.cdb",
        [
            (10, 0, 0, "Internal"),
            (11, 0x4000, 0, "Placeholder"),
            (12, 0x10004000, 0, "Action helper"),
            (13, 0x4011, 0, "Game piece"),
            (14, 0x4001, 0, "Piece with art"),
            (15, 1, 0, "Actual Token"),
            (16, 0x8000000, 0, "Character"),
        ],
    )
    database(
        tmp_path / "repositories/delta-bagooska/prerelease-demo.cdb",
        [
            (15, 1, 0, "Provisional name"),
            (17, 2, 0, "New spell"),
        ],
    )
    cfg, stats, calls = run_sync({"official/14.jpg": (200, JPEG_BODY)})
    assert {int(key.split("/")[1][:-4]) for key in calls} == {13, 14, 15, 16, 17}
    assert calls.count("official/15.jpg") == 1
    assert stats.optional_tokens == {13}
    assert set(stats.failed_cards) == {(15, "Actual Token"), (16, "Character"), (17, "New spell")}
    names, _, _, fields = main.scan_databases(main.get_db_files(str(tmp_path)))
    coverage = main.collect_coverage_stats(cfg, names, fields)
    assert (coverage["cards_total"], coverage["cards_expected"], coverage["known_missing"]) == (
        5,
        4,
        3,
    )
    assert not run_sync()[2]


def test_field_retry_crop_upgrade_and_unaffected_cache(tmp_path: Path, run_sync: Callable) -> None:
    database(tmp_path / "cards.cdb", [(1, 0x80002, 0, "Field"), (2, 1, 0, "Missing")])
    pics = tmp_path / "pics"
    pics.mkdir()
    (pics / "1.jpg").write_bytes(picture("JPEG"))
    ts = time.time()
    (tmp_path / "failed_fields.json").write_text(json.dumps({1: ts, 999: ts}))
    (tmp_path / "failed_cards.json").write_text(json.dumps({2: ts}))
    cfg, stats, calls = run_sync()
    assert calls == ["field/1.jpg", "field_backup/1.png"]
    assert stats.field_ok == stats.field_derived == 1
    jpg = pics / "field/1.jpg"
    assert field_source(jpg.read_bytes()) == "derived"
    assert QImage(str(jpg)).size().toTuple() == (154, 157)
    coverage = main.collect_coverage_stats(cfg, {1: "Field", 2: "Missing"}, {1})
    assert coverage["known_missing"] == coverage["fields_derived"] == 1
    assert ("Derived from card faces", "1") in coverage_summary(coverage).rows
    assert ("Derived from card faces", "1") in sync_summary(
        cfg, {"stats": stats}, False, False, False
    ).rows
    assert not run_sync()[2]  # definitive published misses honor the normal TTL
    (tmp_path / "failed_fields.json").write_text(json.dumps({1: ts - 15 * 86400, 999: ts}))
    _, stats, calls = run_sync({"field_backup/1.png": (200, picture("PNG"))})
    assert calls == ["field/1.jpg", "field_backup/1.png"]
    assert stats.field_ok == 1 and stats.field_derived == 0
    assert not jpg.exists()
    png = pics / "field/1.png"
    assert field_source(png.read_bytes()) == "field_backup:1"
    assert QImage(str(png)).size().toTuple() == (200, 300)
    assert main.load_failure_cache(str(tmp_path / "failed_fields.json")) == {999: ts}
    assert main.load_failure_cache(str(tmp_path / "failed_cards.json")) == {2: ts}
    assert not run_sync()[2]


def test_reviewed_donor_waterfall_and_transient_retry(tmp_path: Path, run_sync: Callable) -> None:
    database(
        tmp_path / "cards.cdb", [(7, 0x80002, 0, "Umi"), (8, 0x10080002, 7, "Umi (Action Field)")]
    )
    pics = tmp_path / "pics"
    field = pics / "field"
    field.mkdir(parents=True)
    for cid in (7, 8):
        (pics / f"{cid}.jpg").write_bytes(picture("JPEG"))
    (field / "7.jpg").write_bytes(main.mark_field_source(picture("JPEG"), "derived"))
    ts = time.time()
    (tmp_path / "failed_fields.json").write_text(json.dumps({7: ts, 8: ts}))
    cfg, stats, calls = run_sync({"field/8.jpg": (503, b"")})
    order = ["field/8.jpg", "field_backup/8.png", "field/7.jpg", "field_backup/7.png"]
    assert calls == order  # a donor's own derived copy is ineligible
    assert stats.field_derived == 1
    assert 8 not in main.load_failure_cache(main._field_failure_cache_path(cfg))
    _, stats, calls = run_sync({"field_backup/7.png": (200, picture("PNG"))})
    assert calls == order
    assert not (field / "8.jpg").exists()
    assert field_source((field / "8.png").read_bytes()) == "field_backup:7"
    (field / "8.png").unlink()
    (field / "7.jpg").write_bytes(picture("JPEG"))
    _, _, calls = run_sync()
    assert calls == order[:2]
    assert field_source((field / "8.jpg").read_bytes()) == "local:7"


def test_cache_tracks_donor_even_without_face(tmp_path: Path, run_sync: Callable) -> None:
    database(
        tmp_path / "cards.cdb",
        [(511003218, 0x80002, 97342942, "Ectoplasmer (Anime)"), (97342942, 2, 0, "Ectoplasmer")],
    )
    ts = time.time()
    (tmp_path / "pics").mkdir()
    (tmp_path / "pics/97342942.jpg").write_bytes(JPEG_BODY)
    for kind in ("cards", "fields"):
        (tmp_path / f"failed_{kind}.json").write_text(json.dumps({511003218: ts}))
    _, stats, calls = run_sync()
    assert calls == [
        "field/511003218.jpg",
        "field_backup/511003218.png",
        "field/97342942.jpg",
        "field_backup/97342942.png",
    ]
    assert stats.field_failed == 1
    assert not run_sync()[2]


def test_reviewed_exceptions_and_divergent_prints() -> None:
    assert (
        field_donor(
            151000026,
            "A Legendary Ocean (Action Field)",
            22702055,
            {"A Legendary Ocean": [295517]},
            {},
        )
        == 295517
    )
    assert field_donor(511015131, "Doll House (Manga)", 67331360, {}, {}) is None
    assert field_donor(160003053, "Forest (Rush)", 87430998, {}, {}) is None
    assert field_donor(101403062, "Salamangreat Shrine", 1295111, {}, {}) == 19832229
    assert field_donor(511600282, "Salamangreat Shrine (Anime)", 0, {}, {}) is None
    assert field_donor(1001, "New Field (Anime)", 42, {}, {42: "New Field"}) == 42


def test_investigated_fields_against_reviewed_sources(tmp_path: Path, run_sync: Callable) -> None:
    with Path(__file__).with_name("field_art_cases.csv").open(encoding="utf8") as data:
        cases = list(csv.DictReader(data))
    pics = tmp_path / "pics"
    field = pics / "field"
    field.mkdir(parents=True)
    jpeg, png = picture("JPEG"), picture("PNG")
    rows = {}
    replies = {}
    expected = {}
    for case in cases:
        cid, alias = int(case["field_id"]), int(case["alias"])
        rows[cid] = (cid, int(case["type_hex"], 16), alias, case["name"])
        source = case["expected_source"]
        if case["donor_id"]:
            donor = int(case["donor_id"])
            # Sacred Defense Barrier is represented by a reverse alias in the CDB.
            reverse_alias = cid if donor == 511000129 else 0
            rows[donor] = (
                donor,
                main.FIELD_SPELL_TYPE if reverse_alias else 2,
                reverse_alias,
                case["donor_name"],
            )
            (pics / f"{donor}.jpg").write_bytes(jpeg)
            if source.startswith("Installed donor"):
                ext = source.split()[-1].lower()
                (field / f"{donor}.{ext}").write_bytes(png if ext == "png" else jpeg)
                expected[cid] = f"local:{donor}"
            else:
                replies[f"field/{donor}.jpg"] = (200, jpeg)
                expected[cid] = f"field:{donor}"
        elif source == "YGOProDeck own JPEG":
            replies[f"field/{cid}.jpg"] = (200, jpeg)
            expected[cid] = f"field:{cid}"
        else:
            expected[cid] = "derived"
        if source == "Crop after face download":
            replies[f"official/{cid}.jpg"] = (200, jpeg)
        else:
            (pics / f"{cid}.jpg").write_bytes(jpeg)
    database(tmp_path / "cards.cdb", list(rows.values()))
    _, stats, _ = run_sync(replies)
    assert stats.field_failed == 0
    assert stats.field_derived == 84 + 11
    assert stats.field_ok == len(cases) == 157
    for cid, source in expected.items():
        (path,) = field.glob(f"{cid}.*")
        assert field_source(path.read_bytes()) == source, cid
