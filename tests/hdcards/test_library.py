import json
from contextlib import closing
from dataclasses import replace

from edopal.core.jobs import JobContext
from edopal.core.settings import Settings
from edopal.dev.preview import make_core
from edopal.features.hdcards.catalog import Card, Catalog
from edopal.features.hdcards.controller import ViewState
from edopal.features.hdcards.coverage import Asset, Coverage
from edopal.features.hdcards.gallery import PACK, load_banner
from edopal.features.hdcards.library import inventory, last_measurement
from edopal.features.hdcards.reviewed import SEED_NAMES
from edopal.features.hdcards.sources import is_hd, rush_index
from edopal.features.hdcards.sync import _open_index


def test_evidence_union_and_scope(tmp_path):
    core = make_core(tmp_path, tmp_path / "packs", tmp_path / "home")
    a, r = 100465001, 160003043
    unmeasured = next(cid for cid in rush_index() if cid not in SEED_NAMES)
    cards = [
        Card(a, SEED_NAMES[a]),
        Card(r, SEED_NAMES[r], rush=True),
        Card(23, "Import", unofficial=True),
        Card(unmeasured, "Index match only", rush=True),
    ]
    cat = Catalog(cards={c.id: c for c in cards})
    scope = set(cat.cards)
    hd = Asset(tmp_path / "pic", None, 407, 593, ())
    assert hd.is_hd and not is_hd(800, 800)
    art = Coverage(faces={23: hd, a: hd})
    options = ViewState()
    with closing(_open_index(core, core.installs.current())) as db:
        before = inventory(db, cat, art, scope, options)
        assert (before["hd"], before["known"], before["ready"], before["pending"]) == (2, 3, 0, 1)
        assert before["groups"] == ((1, 1), (0, 1), (1, 1))
        assert inventory(db, cat, art, scope, replace(options, rush=True))["ready"] == 1
        art.blocked.add(("face", r))
        assert inventory(db, cat, art, scope, replace(options, rush=True))["ready"] == 0
        art.faces.clear()
        now = inventory(db, cat, art, scope, options)
        assert (now["hd"], now["known"], now["ready"]) == (0, 3, 1)
        assert inventory(db, cat, art, {unmeasured}, options)["known"] == 0
        last_measurement(db, options, now)
        assert last_measurement(db, options)["checked"] == now["checked"]
        assert not last_measurement(db, replace(options, deck_only=True))


def test_banner_stays_for_session_then_avoids_recent_choices(tmp_path):
    core = make_core(tmp_path, tmp_path / "packs", tmp_path / "home")
    root = core.packs.root
    pack = root / "art/card-of-the-day"
    banners = pack / "banners"
    banners.mkdir(parents=True)
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "packs": [
                    {
                        "id": PACK,
                        "kind": "art",
                        "name": "Card of the Day",
                        "url": "art/card-of-the-day",
                        "version": "1",
                        "size": 1,
                        "sha256": "",
                    }
                ]
            }
        )
    )
    (pack / "manifest.json").write_text("{}")
    rows = []
    for n in range(4):
        rows.append({"id": n, "name": f"Card {n}", "set_name": "First set", "set_code": "ABC"})
        (banners / f"{n}.jpg").write_bytes(bytes([n]))
    (pack / "banners.json").write_text(json.dumps(rows))
    shown = []
    for _ in range(9):
        core.settings = Settings(core.settings.path)
        record, data = load_banner(core, JobContext())
        assert load_banner(core, JobContext()) == (record, data)
        assert data == bytes([record["id"]]) and record["id"] not in shown[-3:]
        shown.append(record["id"])
