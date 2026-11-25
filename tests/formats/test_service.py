from pathlib import Path

import pytest

from edopal.core.install import RunState
from edopal.core.jobs import JobContext
from edopal.core.storage import GameRunningError
from edopal.features.formats import register
from edopal.features.formats.deck import read_ydk

FIXTURES = Path(__file__).parent / "fixtures"


def hat(service):
    return next(row for row in service.profiles(JobContext()) if row["id"] == "hat")


def test_banlist_install_conflict_undo_and_room_recipe(formats_service):
    service = formats_service
    spec = register(service.core)
    assert (spec.id, spec.label, spec.icon, spec.order) == ("formats", "Formats", "formats", 40)
    profile = hat(service)
    install = service.core.installs.current()
    ctx = JobContext()
    text = service.host_text(profile)
    for phrase in ("2014-04-01", "Master Rule 2", "0x23f83669", "TCG SEGOC", "Anything goes", "Gearframe", "8,000", "40–60"):
        assert phrase in text
    path = install.root / "lflists/edopal-hat.lflist.conf"
    assert service.status(ctx, profile, install) == "available"
    service.install(ctx, profile, install)
    assert "$whitelist" in path.read_text()
    assert service.status(ctx, profile, install) == "installed"
    assert "already" in service.install(ctx, profile, install)
    assert service.can_undo(ctx, install)
    service.undo(ctx, install)
    assert not path.exists()
    path.write_bytes(b"my own banlist")
    assert service.status(ctx, profile, install) == "edited"
    with pytest.raises(ValueError, match="different list"):
        service.install(ctx, profile, install)
    assert path.read_bytes() == b"my own banlist"


def test_recorded_event_to_converted_deck_preserves_source_and_personal_edits(formats_service):
    service = formats_service
    ctx = JobContext()
    profile = hat(service)
    install = service.core.installs.current()
    listing = service.events(ctx, profile, 1)
    event = listing.events[0]
    deck = service.standings(ctx, event).decks[0]
    preview = service.preview(ctx, profile, install, deck)
    assert (event.code, deck.builder) == ("PPAY25", "goodmorning")
    assert [sum(count for _, count in section) for section in preview.detail.sections] == [40, 15, 15]
    assert preview.changes and not preview.issues
    assert preview.composition == (40, 0, 0)
    source = (FIXTURES / "88769.ydk").read_bytes()
    directory = install.root / "deck"
    target = directory / "PPAY25-goodmorning.ydk"
    original = directory / "PPAY25-goodmorning (source).ydk"
    service.save(ctx, profile, install, event, deck, preview)
    assert original.read_bytes() == source
    assert "511099999" in read_ydk(target.read_bytes())[0]
    assert service.save(ctx, profile, install, event, deck, preview).startswith("Already saved")
    target.write_text("personal edit")
    with pytest.raises(ValueError, match="Your file was kept"):
        service.undo(ctx, install)
    service.save(ctx, profile, install, event, deck, preview)
    assert target.read_text() == "personal edit"
    second = directory / "PPAY25-goodmorning (2).ydk"
    assert second.is_file()
    service.undo(ctx, install)
    assert not second.exists() and target.is_file() and original.exists()


def test_changed_game_running_game_and_damaged_pack(formats_service, monkeypatch):
    service = formats_service
    ctx, install = JobContext(), service.core.installs.current()
    row = hat(service)
    monkeypatch.setattr(service.core.installs, "run_state", lambda: RunState.RUNNING)
    with pytest.raises(GameRunningError):
        service.install(ctx, row, install, confirmed_closed=True)
    monkeypatch.setattr(service.core.installs, "run_state", lambda: RunState.UNKNOWN)
    with pytest.raises(GameRunningError):
        service.install(ctx, row, install)
    (service.core.packs.cached("formats") / row["lflist"]).write_bytes(b"truncated")
    with pytest.raises(ValueError, match="integrity"):
        service.install(ctx, row, install, confirmed_closed=True)
    service.core.installs.select(install.root / "another game")
    with pytest.raises(ValueError, match="installation changed"):
        service.install(ctx, row, install, confirmed_closed=True)
    assert not (install.root / "lflists").exists()


def test_offline_first_download_uses_the_recorded_embedded_source(formats_service, monkeypatch):
    from edopal.features.formats import http_cache

    service, ctx = formats_service, JobContext()
    install = service.core.installs.current()
    profile = hat(service)
    event = service.events(ctx, profile, 1).events[0]
    deck = service.standings(ctx, event).decks[0]
    preview = service.preview(ctx, profile, install, deck)

    def offline(*args):
        raise OSError("offline")

    monkeypatch.setattr(http_cache, "_get", offline)
    message = service.save(ctx, profile, install, event, deck, preview)
    assert "cached Format Library preview" in message
    assert (install.root / "deck/PPAY25-goodmorning (source).ydk").read_bytes() == (FIXTURES / "88769.ydk").read_bytes()


def test_stock_host_uses_selected_games_list_and_never_installs_a_duplicate(formats_service):
    service, ctx = formats_service, JobContext()
    install = service.core.installs.current()
    row = next(r for r in service.profiles(ctx) if r["id"] == "advanced")
    assert service.status(ctx, row, install) == "stock-missing"
    assert not row["hash"] and "Current stock TCG" in service.host_text(row)
    path = install.path(row["stock_lflist"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"!2027.01 TCG\r\n#Forbidden\r\n100 0 --Example\r\n")
    profiles = service.profiles(ctx)
    row = next(r for r in profiles if r["id"] == "advanced")
    assert service.status(ctx, row, install) == "stock"
    assert "2027.01 TCG" in service.host_text(row) and row["hash"].startswith("0x")
    assert "2027.01 TCG" in service.install(ctx, row, install)
    assert not service.core.storage.last_receipt(install, "formats")
    assert not (install.root / "lflists").exists()
    path.write_text("!Revised TCG\n123 2\n")
    updated = next(r for r in service.profiles(ctx) if r["id"] == "advanced")
    assert updated["hash"] != row["hash"] and updated["display"] == "Revised TCG"
    other = next(r for r in profiles if r["id"] == "traditional")
    assert service.status(ctx, other, install) == "stock-missing"


def test_genesys_remains_browsable_and_saves_only_published_source(formats_service):
    service, ctx = formats_service, JobContext()
    install = service.core.installs.current()
    row = next(r for r in service.profiles(ctx) if r["id"] == "genesys")
    assert service.status(ctx, row, None) == "unsupported"
    assert "points" in service.host_text(row)
    with pytest.raises(ValueError, match="points"):
        service.install(ctx, row, install)
    event = service.events(ctx, hat(service), 1).events[0]
    deck = service.standings(ctx, event).decks[0]
    detail = service.preview(ctx, row, install, deck)
    assert detail.issues == (row["reason"],)
    service.save(ctx, row, install, event, deck, detail)
    assert [p.name for p in install.path("deck").iterdir()] == ["PPAY25-goodmorning (source).ydk"]
    assert not install.path("lflists").exists()


def test_stock_defaults_region_and_aliases(tmp_path):
    import sqlite3
    from contextlib import closing

    from edopal.core.install import Install
    from edopal.features.formats.deck import prepare

    with closing(sqlite3.connect(tmp_path / "cards.cdb")) as db, db:
        db.executescript("""
            CREATE TABLE datas(id INTEGER PRIMARY KEY,alias INTEGER,ot INTEGER,type INTEGER);
            CREATE TABLE texts(id INTEGER PRIMARY KEY,name TEXT);
            INSERT INTO datas VALUES(100,0,2,1),(900,100,2,1),(105,100,3,1),
                (7,0,1,1),(8,0,8,1),(9,0,3,16385),(1010,0,2,1);
            INSERT INTO texts VALUES(100,'Card'),(900,'Variant'),(105,'Card'),(7,'OCG'),
                (8,'Promo'),(9,'Token'),(1010,'Another TCG card');
        """)
    install, limits = Install(tmp_path), {100: 1}
    assert not prepare(install, limits, b"#main\n1010\n900", stock_tcg=True).issues
    assert any("exceed" in i for i in prepare(install, limits, b"#main\n105\n!side\n900", stock_tcg=True).issues)
    assert len(prepare(install, limits, b"#main\n7\n8\n9", stock_tcg=True).issues) == 3


def test_reopens_with_full_catalog_and_offline_legacy_pack(formats_service):
    import json
    from shutil import rmtree

    from edopal.features.formats.library_pack import LEGACY_IDS

    service, ctx = formats_service, JobContext()
    local = service.core.packs
    folder = local.cached("formats")
    doc = json.loads((folder / "manifest.json").read_bytes())
    full_doc = json.loads(json.dumps(doc))
    doc["formats"] = [r for r in doc["formats"] if r["id"] in LEGACY_IDS]
    (folder / "manifest.json").write_text(json.dumps(doc))
    # First simulate a disconnected checkout, with the original 8 cached rows.
    rmtree(local.root / "formats")
    assert len(service.profiles(ctx)) == 128
    assert local.cached("formats") and service.pack.whitelist("hat", ctx).limits
    with pytest.raises(ValueError, match="full formats pack"):
        service.pack.whitelist("tokyo-dome", ctx)
    # The local pack service reuses its first cached copy. Reconnecting upgrades it.
    from shutil import copytree

    copytree(folder, local.root / "formats")
    (local.root / "formats/manifest.json").write_text(json.dumps(full_doc))
    assert len(service.profiles(ctx)) == 128
    assert len(json.loads((folder / "manifest.json").read_bytes())["formats"]) == 128


def test_bulk_install_is_atomic_repeatable_and_undo_preserves_local_lists(formats_service):
    import hashlib
    import json

    service, ctx = formats_service, JobContext()
    install = service.core.installs.current()
    folder = service.core.packs.cached("formats")
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    historical = [r for r in manifest["formats"] if r.get("status") == "historical"]
    for row in historical:
        data = f"!{row['display']}\n$whitelist\n100 2\n".encode()
        (folder / row["lflist"]).write_bytes(data)
        row["sha256"] = manifest["files"][row["lflist"]] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    last = folder / historical[-1]["lflist"]
    intact = last.read_bytes()
    last.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="integrity"):
        service.install_all(ctx, install)
    assert not install.path("lflists").exists()
    last.write_bytes(intact)
    own = install.path(service._list_name(historical[0]))
    own.parent.mkdir()
    own.write_text("personal list")
    assert "Installed 124 banlists" in service.install_all(ctx, install)
    receipt = service.core.storage.last_receipt(install, "formats")
    assert len(receipt.changed) == 124
    assert "Installed 0 banlists" in service.install_all(ctx, install)
    assert service.core.storage.last_receipt(install, "formats").id == receipt.id
    assert not install.path("repositories/lflists").exists()
    service.undo(ctx, install)
    assert list(own.parent.iterdir()) == [own] and own.read_text() == "personal list"
