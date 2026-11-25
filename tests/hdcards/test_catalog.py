import hashlib
import io
import json
import sqlite3
from concurrent.futures import CancelledError
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from edopal.core.install import Install
from edopal.core.settings import Settings
from edopal.features.hdcards.catalog import Card, check_updates, deck_ids, read_catalog
from edopal.features.hdcards.coverage import coverage

FIXTURE = Path(__file__).parents[1] / "fixtures/hdcards/catalog.sql"


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "Project Ignis #1"
    root.mkdir()
    with closing(sqlite3.connect(root / "cards.cdb")) as db:
        db.executescript(FIXTURE.read_text())
    return Install(root)


def db_file(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, type INTEGER, alias INTEGER)")
        db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO datas VALUES (?,?,?)", [(cid, kind, alias) for cid, _, kind, alias in rows])
        db.executemany("INSERT INTO texts VALUES (?,?)", [(cid, name) for cid, name, _, _ in rows])
        db.commit()
    return path


def picture(size=(400, 600), fmt="PNG"):
    b = io.BytesIO()
    Image.new("RGB", size, "blue").save(b, format=fmt)
    return b.getvalue()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def config(game, name, repos):
    save(game.path("config", name), json.dumps({"repos": repos}).encode())


def test_catalog_precedence_and_readonly(game, tmp_path, monkeypatch):
    root = game.root
    # Provisional definitions are overridden by stable rows, including type/alias/rush metadata.
    db_file(root / "repositories/delta-bagooska/prerelease-cards-rush.cdb", (1, "Old", 524290, 2), (9, "Rush", 1, 0))
    db_file(root / "expansions/nested/pack/z.cdb", (8, "Nested", 1, 0))
    db_file(root / "expansions/skip/too/deep/ignored.cdb", (10, "Too deep", 1, 0))
    packed = db_file(tmp_path / "packed.cdb", (2, "ZIP card", 1, 8), (11, "ZIP field", 524290, 0))
    with ZipFile(root / "expansions/test.zip", "w") as z:
        z.write(packed, "a/b/c/cards.cdb")
        z.writestr("a/b/c/d/skipped.cdb", b"bad")
    db_file(root / "repositories/custom/db/custom.cdb", (2, "Repo field", 524290, 1))
    db_file(root / "repositories/disabled/custom.delta.cdb", (12, "Disabled", 1, 0))
    db_file(root / "repositories/language/strings.cdb", (2, "Language", 1, 1))
    config(game, "user_configs.json", [{"repo_path": "repositories/disabled", "should_read": False}])
    config(
        game,
        "configs.json",
        [
            {"repo_path": "repositories/delta-bagooska"},
            {"repo_path": "repositories/custom", "data_path": "db"},
            {"repo_path": "repositories/disabled"},
            {"repo_path": "repositories/language", "is_language": True},
        ],
    )
    connect = sqlite3.connect
    opened = []

    def readonly(path, **kwargs):
        opened.append(path)
        assert path == ":memory:" or ("mode=ro&immutable=1" in path and kwargs["uri"])
        return connect(path, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", readonly)
    c = read_catalog(game)
    assert c.complete and opened
    assert set(c.cards) == {1, 2, 3, 8, 9, 11}
    assert c.sources["expansions/test.zip!a/b/c/cards.cdb"] == {2, 11}
    assert c.fields == {2, 11}
    assert c.cards[1].name == "First" and not c.cards[1].rush
    assert c.cards[2].alias == 1 and c.cards[9].rush
    assert c.official["Repo field"] == [2]
    assert not {"ZIP card", "First field", "Old"} & c.official.keys()


def test_legacy_database_without_type_or_alias(game):
    path = game.path("expansions", "legacy.cdb")
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE datas(id); CREATE TABLE texts(id, name); "
            "INSERT INTO datas VALUES(2); INSERT INTO texts VALUES(2, 'Legacy');"
        )
    c = read_catalog(game)
    assert c.complete and c.cards[2].type is None and c.cards[2].alias == 0 and 2 not in c.fields


def test_coverage_visible_png_shadow_and_damaged_art(game):
    config(game, "configs.json", [{"repo_path": "repositories/custom", "pics_path": "art"}])
    cdb = game.path("expansions", "extra.cdb")
    db_file(cdb, (7, "Seven", 1, 0), (8, "Eight", 1, 0), (9, "Nine", 1, 0))
    jpg, png = picture(fmt="JPEG"), picture((177, 254))
    repo = save(game.path("repositories/custom/art/1.jpg"), jpg)
    expansion = save(game.path("expansions/pics/3.jpg"), jpg)
    save(game.path("expansions/pics/field/2.png"), png)
    save(game.path("pics/7.jpg"), jpg)
    small = save(game.path("pics/7.png"), png)
    save(game.path("pics/9.png"), png[:30] + b"broken compressed pixels" + png[-12:])
    clean = save(game.path("pics/9.jpg"), jpg)
    for name, items in (("a", {"pics/8.jpg": jpg}), ("b", {"pics/8.png": png, "pics/2.png": png, "pics/field/2.png": png})):
        with ZipFile(game.path(f"expansions/{name}.zip"), "w") as z:
            for path, data in items.items():
                z.writestr(path, data)
    c = read_catalog(game)
    a = coverage(game, c)
    assert a.complete and c.complete
    assert a.faces[1].path == repo and a.faces[3].path == expansion
    assert a.faces[8].path.name == "b.zip" and a.faces[8].read_bytes() == png
    assert a.fields[2].path == game.path("expansions/pics/field/2.png")
    assert a.blocked == {("face", 1), ("face", 2), ("face", 3), ("face", 8), ("field", 2)}
    assert a.faces[7].path == small and a.faces[9].path == clean
    assert a.root_png == {("face", 7), ("face", 9)}
    assert a.hd_faces == {1, 3, 9}
    small.write_bytes(jpg)
    assert 7 in coverage(game, c).hd_faces


def test_new_cards_ignore_moves_and_preserve_baseline_on_partial_scan(game, tmp_path):
    store = Settings(tmp_path / "settings.json")

    def check():
        c = read_catalog(game)
        return check_updates(store, game, c, coverage(game, c))

    assert not check().new
    loose = game.path("expansions", "moved.cdb")
    loose.parent.mkdir()
    game.path("cards.cdb").rename(loose)
    assert not check().new
    added = db_file(game.path("expansions/new.cdb"), (9, "New field", 524290, 0), (10, "New monster", 1, 0))
    save(game.path("pics/10.jpg"), picture(fmt="JPEG"))
    notice = check()
    assert notice.new == {9, 10} and notice.needs == {9}
    failing = save(game.path("expansions/broken.cdb"), b"invalid")
    added.unlink()
    assert check() == notice
    failing.unlink()
    db_file(added, (9, "Renamed field", 524290, 0), (10, "New monster", 1, 0))
    save(game.path("pics/9.jpg"), picture(fmt="JPEG"))
    assert check().needs == {9}
    save(game.path("pics/field/9.jpg"), picture(fmt="JPEG"))
    assert not check().new
    c = read_catalog(game)
    save(game.path("expansions/more.cdb"), b"not finished")
    assert not c.stable()


def test_deck_scope_and_cancellation(game):
    save(game.path("deck/nested/Test.YDK"), b"#main\n2\n3\n#extra\n2\n!side\n42\n-1\n0\n99999999999\n")
    assert deck_ids(game) == {2, 3, 42}
    with pytest.raises(CancelledError):
        read_catalog(game, cancelled=lambda: True)
    with pytest.raises(CancelledError):
        coverage(game, read_catalog(game), cancelled=lambda: True)


def test_changed_art_during_scan_keeps_previous_notice(game, tmp_path, monkeypatch):
    from edopal.features.hdcards import coverage as images

    settings = Settings(tmp_path / "preferences.json")
    c = read_catalog(game)
    first = check_updates(settings, game, c, coverage(game, c))
    db_file(game.path("expansions/addition.cdb"), (9, "New field", 524290, 0))
    c = read_catalog(game)
    path = save(game.path("pics/9.jpg"), picture(fmt="JPEG"))
    measure = images._local

    def replace_after_measurement(candidate, stamp):
        measured = measure(candidate, stamp)
        path.write_bytes(picture((177, 254), "JPEG"))
        return measured

    monkeypatch.setattr(images, "_local", replace_after_measurement)
    partial = coverage(game, c)
    assert not partial.complete
    assert check_updates(settings, game, c, partial) == first
    monkeypatch.setattr(images, "_local", measure)
    assert check_updates(settings, game, c, coverage(game, c)).needs == {9}


def test_persistent_coverage_cache_revalidates_files_and_archives(game, tmp_path, monkeypatch):
    from edopal.features.hdcards import coverage as images

    cache = tmp_path / "app-cache"
    small, hd = picture((177, 254)), picture()
    one = save(game.path("pics/1.png"), small)
    two = save(game.path("pics/2.png"), b"damaged png")
    archive = game.path("expansions/art.zip")
    archive.parent.mkdir()
    with ZipFile(archive, "w") as z:
        z.writestr("pics/3.png", hd)
    first = coverage(game, read_catalog(game), cache=cache)
    assert first.complete and first.hd_faces == {3}
    assert len(list(cache.rglob("*.sqlite3"))) == 1

    checked = []
    decode = images.image_dimensions

    def count(data):
        checked.append(data)
        return decode(data)

    monkeypatch.setattr(images, "image_dimensions", count)
    images._local.cache_clear()
    assert coverage(game, read_catalog(game), cache=cache) == first
    assert checked == []

    # Same limit/policy, but both a prior failure and a prior valid image changed.
    one.write_bytes(hd)
    two.write_bytes(hd)
    with ZipFile(archive, "w") as z:
        z.writestr("pics/3.png", small)
    images._local.cache_clear()
    second = coverage(game, read_catalog(game), cache=cache)
    assert second.complete and second.hd_faces == {1, 2}
    assert len(checked) == 3


def test_unusable_cache_still_measures_and_never_writes_to_game(game, tmp_path):
    c = read_catalog(game)
    save(game.path("pics/1.png"), picture())
    unwritable = save(tmp_path / "file-instead-of-cache-folder", b"")
    for cache in (unwritable, game.path("nested/cache")):
        measured = coverage(game, c, cache=cache)
        assert measured.complete and measured.hd_faces == {1}
    assert not game.path("nested").exists()
    outside = tmp_path / "outside-cache"
    coverage(game, c, cache=outside)
    db = next(outside.rglob("*.sqlite3"))
    db.write_bytes(b"Damaged SQLite cache")
    assert coverage(game, c, cache=outside).hd_faces == {1}


def test_hd_header_scan_and_lazy_provenance(game, tmp_path, monkeypatch):
    from edopal.features.hdcards import coverage as images

    data = picture((813, 1185), "JPEG")
    valid = save(game.path("pics/1.jpg"), data)
    save(game.path("pics/2.jpg"), data[:-2])
    cat = read_catalog(game)
    cache = tmp_path / "cache"
    reads = []
    original = images.Asset.read_bytes

    def record_read(asset):
        reads.append(asset.path)
        return original(asset)

    monkeypatch.setattr(images.Asset, "read_bytes", record_read)
    monkeypatch.setattr(Image.Image, "load", lambda *_: pytest.fail("decoded JPEG pixels"))
    first = coverage(game, cat, cache=cache)
    assert first.complete and first.hd_faces == {1} and reads == []
    owned = {(1, "face")}
    digest = hashlib.sha256(data).hexdigest()
    for _ in range(2):
        images._local.cache_clear()
        art = coverage(game, cat, cache=cache, digest_ids=owned)
        assert art == first and art.faces[1].digest == digest
    assert reads == [valid]

    valid.write_bytes(b"changed after scan")
    with pytest.raises(OSError, match="changed"):
        first.faces[1].read_bytes()


def test_coverage_streams_missing_ids_before_measuring_pics(game, monkeypatch):
    from edopal.features.hdcards import coverage as images

    field = save(game.path("pics/field/2.jpg"), picture((323, 322), "JPEG"))
    face = save(game.path("pics/1.jpg"), picture(fmt="JPEG"))
    cat = read_catalog(game)
    batches = []
    measured = []
    load = images._local

    def observe(path, stamp):
        measured.append(path)
        return load(path, stamp)

    def on_batch(snapshot, ids):
        assert snapshot.fields[2].path == field
        batches.append((snapshot, ids, tuple(measured)))

    monkeypatch.setattr(images, "_local", observe)
    complete = coverage(game, cat, on_batch=on_batch)
    assert complete.complete
    assert {cid for _, ids, _ in batches for cid in ids} == cat.cards.keys()
    assert sum(len(ids) for _, ids, _ in batches) == len(cat.cards)
    early, ids, seen = batches[0]
    assert 2 in ids and face not in seen and not early.faces
    assert batches[-1][0].faces == complete.faces


def test_adjacent_scans_revalidate_visible_files_and_directories(game, tmp_path, monkeypatch):
    from edopal.features.hdcards import coverage as images

    cat = read_catalog(game)
    cat.cards.update({cid: Card(cid, str(cid)) for cid in range(100, 250)})
    cache = tmp_path / "cache"
    hd = picture((813, 1185), "JPEG")
    path = save(game.path("pics/1.jpg"), hd)
    first = coverage(game, cat, cache=cache)
    assert first.hd_faces == {1}
    first.faces.clear()  # A caller cannot change a later scan.
    scans = []
    scandir = images.os.scandir

    def record(folder):
        scans.append(folder)
        return scandir(folder)

    monkeypatch.setattr(images.os, "scandir", record)
    assert coverage(game, cat, cache=cache).hd_faces == {1}
    assert not scans
    assert coverage(game, cat, cache=cache, digest_ids={(1, "face")}).faces[1].digest == hashlib.sha256(hd).hexdigest()
    assert scans
    path.write_bytes(picture((150, 250), "JPEG"))
    assert not coverage(game, cat, cache=cache).hd_faces
    save(game.path("pics/2.jpg"), hd)
    assert coverage(game, cat, cache=cache).hd_faces == {2}

    def change_while_streaming(partial, ids):
        path.unlink(missing_ok=True)

    assert not coverage(game, cat, cache=cache, on_batch=change_while_streaming).complete
    with pytest.raises(CancelledError):
        coverage(game, cat, cache=cache, cancelled=lambda: True)


def test_validated_hd_requires_decodable_portrait_and_reuses_stamp(game, tmp_path, monkeypatch):
    good = picture((407, 593), "JPEG")
    sos = good.index(b"\xff\xda")
    corrupt = good[: sos + 5] + b"\xff" * 4 + good[sos + 9 :]
    save(game.path("pics/1.jpg"), corrupt)
    save(game.path("pics/2.jpg"), good)
    save(game.path("pics/3.jpg"), picture((600, 600), "JPEG"))
    cat = read_catalog(game)
    first = coverage(game, cat, cache=tmp_path / "cache", verify_faces=True)
    assert first.complete and first.hd_faces == {2}
    monkeypatch.setattr(Image.Image, "load", lambda *_: pytest.fail("re-decoded unchanged pixels"))
    assert coverage(game, cat, cache=tmp_path / "cache", verify_faces=True).hd_faces == {2}
