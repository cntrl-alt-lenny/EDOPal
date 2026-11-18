import codecs
import json
from pathlib import Path

import pytest

import backup
from backup import Installation
from backup import restore as engine


def put(install: Installation, name: str, value: bytes = b"personal") -> Path:
    path = install.target(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def test_round_trip(pair: tuple, snapshot: Path) -> None:
    pc, deck = pair
    paths = [
        "game/deck/GOAT/tournament/A.ydk",
        "game/replay/Last.yrpX",
        "game/replay/B.yrp",
        "game/screenshots/card.png",
        "game/puzzles/my/test.lua",
        "studio/sleeves/mine.png",
        "studio/themes/night.json",
    ]
    for name in paths:
        put(pc, name)
    for name in (
        "game/pics/art.jpg",
        "game/script/skip.lua",
        "game/deck/readme.txt",
        "game/puzzles/Canon collection/stock.lua",
        "game/puzzles/Other repo/stock.lua",
        "studio/extras-backups/sleeves/mine.png",
    ):
        put(pc, name)
    put(pc, "game/config/configs.json", b'{"repos":[{"repo_path":"./puzzles/Other repo"}]}')
    track = "studio/soundtracks/pocyugi/duel/theme.ogg"
    put(pc, track)
    inventory = backup.inventory(pc)
    assert inventory["decks"] == backup.Total(1, 8)
    assert inventory["puzzles"].count == 1 and inventory["screenshots"].bytes == 8
    assert inventory["studio"].count == 3 and inventory["audio"].count == 1
    assert len(backup.create(pc, snapshot).entries) == 3
    all_but_audio = set(backup.CATEGORIES) - {"audio"}
    archive = snapshot.with_suffix(".all")
    manifest = backup.create(pc, archive, all_but_audio)
    assert manifest == backup.inspect(archive)
    assert {e.path for e in manifest.entries} == {*paths, "studio/soundtracks/index.json"}
    plan = backup.plan_restore(archive, deck)
    assert all(i.status == "add" for i in plan.items)
    assert not any(deck.game.iterdir()) and not deck.data.exists()
    backup.execute(plan)
    assert all(deck.target(n).read_bytes() == b"personal" for n in paths)
    assert not deck.target(track).exists()
    assert json.loads(deck.target("studio/soundtracks/index.json").read_bytes()) == [
        "pocyugi/duel/theme.ogg"
    ]
    assert {d.status for d in backup.compare(deck, archive)} == {"identical"}
    assert {d.status for d in backup.compare(pc, deck, all_but_audio)} == {"identical"}
    assert {i.status for i in backup.plan_restore(archive, deck).items} == {"identical"}
    backup.create(pc, snapshot.with_suffix(".audio"), ["audio"])
    backup.execute(backup.plan_restore(snapshot.with_suffix(".audio"), deck))
    assert deck.target(track).read_bytes() == b"personal"


@pytest.mark.parametrize(
    "policy,result,extra",
    [
        ("keep_mine", b"mine", False),
        ("take_theirs", b"theirs", False),
        ("keep_both", b"mine", True),
    ],
)
def test_conflicts_and_undo(
    pair: tuple, snapshot: Path, policy: str, result: bytes, extra: bool
) -> None:
    src, dest = pair
    name = "game/deck/sub/A.ydk"
    put(src, name, b"theirs")
    put(src, "game/deck/New.ydk")
    put(src, "game/deck/Equal.ydk")
    identical = put(dest, "game/deck/Equal.ydk")
    stamp = identical.stat().st_mtime_ns
    only_local = put(dest, "game/deck/Local.ydk")
    original = put(dest, name, b"mine")
    occupied = put(dest, "game/deck/sub/A (incoming).ydk", b"old copy")
    backup.create(src, snapshot)
    plan = backup.plan_restore(snapshot, dest, policy)
    assert {i.status for i in plan.items} == {"conflict", "add", "identical"}
    tx = backup.execute(plan)
    assert original.read_bytes() == result and only_local.exists()
    copy = dest.target("game/deck/sub/A (incoming 2).ydk")
    assert copy.exists() == extra
    if extra:
        assert copy.read_bytes() == b"theirs"
    backup.recover(dest, tx.id, "rollback")
    assert original.read_bytes() == b"mine" and occupied.read_bytes() == b"old copy"
    assert identical.stat().st_mtime_ns == stamp and only_local.exists()
    assert not dest.target("game/deck/New.ydk").exists() and not copy.exists()


@pytest.mark.parametrize("source,target", [("PLAY.ydk", "play.ydk"), ("play.ydk", "PLAY.ydk")])
def test_case_conflict(pair: tuple, snapshot: Path, source: str, target: str) -> None:
    src, dest = pair
    put(src, "game/deck/" + source, b"pc")
    original = put(dest, "game/deck/" + target, b"deck")
    backup.create(src, snapshot)
    plan = backup.plan_restore(snapshot, dest, "keep_both")
    (item,) = plan.items
    assert item.status == "conflict" and "spelling" in item.detail
    assert item.target == "game/deck/" + target[:-4] + " (incoming).ydk"
    backup.execute(plan)
    assert original.read_bytes() == b"deck" and dest.target(item.target).read_bytes() == b"pc"
    assert {d.status for d in backup.compare(src, dest)} == {"case conflict", "right only"}


@pytest.mark.parametrize("policy", ["keep_mine", "take_theirs", "keep_both"])
def test_pref_merge(
    pair: tuple, snapshot: Path, policy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dest = pair
    monkeypatch.setattr(engine, "_game_running", lambda game: False)
    conf = "game/config/system.conf"
    put(src, conf, b"nickname = Other\nmute_opponent = 1\ndpi_scale = 2\nlasthost = private\n")
    prior = (
        codecs.BOM_UTF8
        + b"nickname = Earlier\r\n# \xfe untouched\nnickname \t= Me  \r\ndpi_scale = 5"
    )
    original = put(dest, conf, prior)
    manifest = backup.create(src, snapshot, ["preferences"])
    assert {e.path for e in manifest.entries} == {
        "preferences/nickname",
        "preferences/mute_opponent",
    }
    plan = backup.plan_restore(snapshot, dest, policy)
    (conflict,) = [i for i in plan.items if i.status == "conflict"]
    assert (conflict.mine, conflict.theirs) == ("Me", "Other")
    tx = backup.execute(plan)
    expected = prior.replace(b"= Me ", b"= Other ") if policy == "take_theirs" else prior
    assert original.read_bytes() == expected + b"\r\nmute_opponent = 1\r\n"
    if policy == "keep_both":
        assert dest.target(conflict.target).read_bytes() == b"nickname = Other\n"
    backup.recover(dest, tx.id, "rollback")
    assert original.read_bytes() == prior


def test_game_open_and_stale_preview(
    pair: tuple, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dest = pair
    put(src, "game/config/system.conf", b"autochain = 1\n")
    put(src, "game/deck/a.ydk")
    original = put(dest, "game/config/system.conf", b"autochain = 0\n")
    backup.create(src, snapshot, ["decks", "preferences"])
    plan = backup.plan_restore(snapshot, dest, "take_theirs")
    original.write_bytes(b"# edit\nautochain = 0\n")
    with pytest.raises(ValueError, match="stale"):
        backup.execute(plan)
    plan = backup.plan_restore(snapshot, dest, "take_theirs")
    monkeypatch.setattr(engine, "_game_running", lambda game: True)
    with pytest.raises(OSError, match="Close EDOPro"):
        backup.execute(plan)
    assert not dest.target("game/deck/a.ydk").exists() and original.read_bytes().endswith(b"= 0\n")


def test_create_cancel_and_no_clobber(pair: tuple, snapshot: Path) -> None:
    src, _ = pair
    put(src, "game/deck/a.ydk")
    reached = []
    with pytest.raises(backup.Cancelled):
        backup.create(
            src, snapshot, progress=lambda *p: reached.append(p), cancel=lambda: bool(reached)
        )
    assert not snapshot.exists() and not list(snapshot.parent.glob(".backup-*"))
    snapshot.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        backup.create(src, snapshot)
    assert snapshot.read_bytes() == b"existing"


@pytest.mark.parametrize("mode", ["rollback", "finish"])
def test_interrupted_restore(pair: tuple, snapshot: Path, mode: str) -> None:
    src, dest = pair
    for n in ("a", "b"):
        put(src, f"game/deck/{n}.ydk", b"incoming")
    old = put(dest, "game/deck/a.ydk", b"old")
    new = dest.target("game/deck/b.ydk")
    backup.create(src, snapshot)
    plan = backup.plan_restore(snapshot, dest, "take_theirs")
    with pytest.raises(backup.Cancelled):
        backup.execute(plan, cancel=lambda: old.read_bytes() == b"incoming")
    (tx,) = backup.transactions(dest)
    assert tx.status == "pending" and not new.exists()
    snapshot.unlink()
    backup.recover(dest, tx.id, mode)
    assert old.read_bytes() == (b"old" if mode == "rollback" else b"incoming")
    assert new.exists() == (mode == "finish")
    backup.recover(dest, tx.id, mode)


def test_after_restore_edits_preserved(pair: tuple, snapshot: Path) -> None:
    src, dest = pair
    put(src, "game/deck/a.ydk")
    put(src, "game/deck/b.ydk")
    backup.create(src, snapshot)
    tx = backup.execute(backup.plan_restore(snapshot, dest))
    edited = put(dest, "game/deck/a.ydk", b"user edit")
    with pytest.raises(ValueError, match="changed"):
        backup.recover(dest, tx.id, "rollback")
    assert edited.read_bytes() == b"user edit" and dest.target("game/deck/b.ydk").exists()


def test_return_trip_preserves_both_edits(pair: tuple, snapshot: Path) -> None:
    pc, deck = pair
    name = "game/deck/GOAT/list.ydk"
    original = put(pc, name, b"original")
    backup.create(pc, snapshot)
    backup.execute(backup.plan_restore(snapshot, deck))
    deck.target(name).write_bytes(b"edited on Deck")
    put(deck, "game/replay/trip.yrpX", b"replay")
    original.write_bytes(b"edited on PC")
    returning = snapshot.with_suffix(".return")
    backup.create(deck, returning)
    backup.execute(backup.plan_restore(returning, pc, "keep_both"))
    assert original.read_bytes() == b"edited on PC"
    assert pc.target("game/deck/GOAT/list (incoming).ydk").read_bytes() == b"edited on Deck"
    assert pc.target("game/replay/trip.yrpX").read_bytes() == b"replay"
