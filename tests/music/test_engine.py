from __future__ import annotations

import json
import shutil
import wave
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

import music_import
import music_library as music
from music.__main__ import report
from music.catalogue import normalized
from music.discovery import validate_records
from music.view import LibraryView, SeekState, run_job


def wav(path: Path, note: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", ""))
        stream.writeframes(note.to_bytes(2, "little") * 1600)
    return path


def active(game: Path, scene: str, root: Path) -> list[str]:
    return [row.title for row in music.tracks(game, scene, root=root) if row.in_playlist]


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("GX Tag Force 5", "tf5"),
        ("TagForce5", "tf5"),
        ("Tag Force 50", "local"),
        ("Tag Force 10", "local"),
        ("GX Tag Force 50", "local"),
        ("XTF5", "local"),
        ("TF2", "tf2"),
        ("WCT2005", "trials"),
        ("WCT2006", "ultimate"),
        ("GX Tag Force Evolution", "tfe"),
        ("Legacy of the Duelist Link Evolution", "linkevolution"),
        ("遊戯王 5D’s タッグフォース6", "tf6"),
        ("Duel Monsters 5 Expert 1", "eternal"),
        ("Duel Monsters 7 The Duelcity Legend", "sacred"),
        ("Shin Duel Monsters", "forbidden"),
        ("Duel Monsters Expert 3", "wc2004"),
        ("Duel Monsters International 2", "trials"),
        ("Day of the Duelist", "trials"),
        ("Duel Monsters Expert 2006", "ultimate"),
        ("GX Spirit Summoner", "spirit"),
        ("World Championship Tournament 2009", "wc2009"),
        ("WCT2010", "wc2010"),
        ("WCT2011", "wc2011"),
        ("double pack 2", "local"),
    ],
)
def test_match(name: str, key: str, tmp_path: Path) -> None:
    assert music._identify(f"{name} - Duel.flac", music.catalog(root=tmp_path)) == key


def test_normalized_specificity_and_ambiguity() -> None:
    a = music.Game("a", "A game", aliases=("A---------B", "!!!", "遊戯王"))
    b = music.Game("b", "Other", aliases=("A B C",))
    assert music._identify("A B C.wav", {"a": a, "b": b}) == "b"
    assert music._identify("unrelated.wav", {"a": a}) == music.LOCAL
    assert music._identify("A B C", {"a": replace(a, aliases=b.aliases), "b": b}) == music.LOCAL


def test_catalogue_and_selection_schema(tmp_path: Path) -> None:
    games = music.catalog(root=tmp_path)
    data = json.loads((music.ART / "catalog.json").read_bytes())
    assert len(data) == len(games) - 1 == 69
    aliases = {}
    for game in games.values():
        if game.id != music.LOCAL:
            assert (
                game.title and game.series and game.aliases and game.platform and game.first_region
            )
            assert 1998 <= game.first_year <= 2025
        for alias in (*game.aliases, game.title):
            token = normalized(alias)
            assert token and aliases.setdefault(token, game.id) == game.id
        validate_records(
            [
                {
                    "game": game.id,
                    "digest": "a" * 64,
                    "title": "Cue",
                    "name": f"HD Sync - {game.id} - {'a' * 16}.wav",
                }
            ]
        )


@pytest.mark.parametrize("scene", music.SCENES)
def test_scene_edit_restore_and_undo(tmp_path: Path, scene: str) -> None:
    game, root = tmp_path / "game", tmp_path / "personal"
    original = wav(game / "sound/BGM" / scene / "Original.wav", 1)
    neighbor = wav(game / "sound/BGM" / ("win" if scene != "win" else "lose") / "Keep.wav", 4)
    before = original.read_bytes()
    first = music.Track("tf5", "Zebra", wav(root / "tf5" / scene / "Zebra.wav", 2), scene)
    second = music.Track("tf4", "Alpha", wav(root / "tf4" / scene / "Alpha.wav", 3), scene)
    music.add_to_playlist(game, first, root=root)
    assert active(game, scene, root) == ["Original", "Zebra"]
    music.undo_playlist(game, scene, root=root)
    assert original.read_bytes() == before and not music.can_restore(game, scene)
    music.use_track(game, first, root=root)
    assert {t.source for t in music.tracks(game, scene, root=root)} >= {
        "parked",
        "personal",
        "installed",
    }
    music.add_to_playlist(game, second, root=root)
    music.add_to_playlist(game, first, root=root)
    assert active(game, scene, root) == ["Zebra", "Alpha"]
    music.remove_from_playlist(game, first, root=root)
    assert active(game, scene, root) == ["Alpha"]
    with pytest.raises(ValueError, match="at least one"):
        music.remove_from_playlist(game, second, root=root)
    first.path.unlink()
    music.undo_playlist(game, scene, root=root)
    assert active(game, scene, root) == ["Zebra", "Alpha"]
    music.restore_playlist(game, scene)
    assert original.read_bytes() == before and neighbor.is_file()
    assert not music.can_restore(game, scene) and music.can_undo(game, scene)
    music.undo_playlist(game, scene, root=root)
    assert active(game, scene, root) == ["Zebra", "Alpha"]
    music.restore_playlist(game, scene)
    assert original.read_bytes() == before


def test_personal_export_and_corrupt_manifest(tmp_path: Path) -> None:
    root, copy, game = (tmp_path / x for x in ("personal", "copy", "game"))
    game.mkdir()
    mix = music.create_game("My mix ♫", root=root)
    for i, title in enumerate(("B", "A")):
        music.import_track(wav(tmp_path / f"{title}.wav", i), mix.id, "menu", root=root)
    music.create_game("Still empty", root=root)
    assert music_import.export_library(copy, root=root) == (2, 0)
    assert music.catalog(root=copy) == music.catalog(root=root)
    music.use_game(game, "menu", mix.id, root=copy)
    assert active(game, "menu", copy) == ["A", "B"]
    (copy / mix.id / "game.json").write_bytes(b"\xff")
    assert music.catalog(root=copy)[mix.id].title.startswith("Recovered")
    assert len([t for t in music.tracks(game, "menu", root=copy) if t.game == mix.id]) == 2
    wav(copy / mix.id / "lose/Defeat.wav", 5)
    music.use_game(game, "menu", mix.id, root=copy, all_scenes=True)
    assert active(game, "menu", copy) == ["A", "B", "Defeat"]
    music.restore_playlist(game, "menu")


@pytest.mark.parametrize("bad", [b"{", b"\xff", b"[]", b'{"tracks":[{"name":"../../broken"}]}'])
def test_damage_isolated_restore_preserves_unproven_files(tmp_path: Path, bad: bytes) -> None:
    game, root = tmp_path / "game", tmp_path / "lib"
    orig = wav(game / "sound/BGM/deck/Stock.wav", 5)
    song = wav(root / "tf2/deck/Rip.wav", 7)
    music.use_track(game, music.Track("tf2", "Rip", song, "deck"), root=root)
    state = game / "sound/.edopro-hd-sync/deck"
    wav(root / "tf1/deck/Other.wav", 11)
    (state / "selection.json").write_bytes(bad)
    assert music.can_restore(game, "deck")
    assert {row.source for row in music.tracks(game, "deck", root=root)} >= {"personal", "parked"}
    music.restore_playlist(game, "deck")
    assert list(orig.parent.glob("*.wav")) == [orig]
    assert next((state / "recovered").glob("*.wav")).read_bytes() == song.read_bytes()


def test_same_name_same_size_and_different_name_duplicate(tmp_path: Path) -> None:
    bgm = tmp_path / "game/sound/BGM"
    wav(bgm / "duel/Battle.wav", 5)
    wav(bgm / "Battle.wav", 6)
    root = tmp_path / "lib"
    wav(root / "local/duel/Renamed.wav", 5)
    rows = music.tracks(tmp_path / "game", "duel", root=root)
    assert [r.source for r in rows if r.path.suffix == ".wav"] == ["installed", "installed"]


def test_cached_view_and_seek(tmp_path: Path) -> None:
    root, game = tmp_path / "lib", tmp_path / "game"
    wav(game / "sound/BGM/deck/TF5 theme.wav", 1)
    wav(root / "tf5/duel/Duel 12.wav", 5)
    view = LibraryView(game, root=root)
    view.refresh()
    wav(root / "tf2/deck/Added.wav", 8)
    with patch("music.view.scan_scene", side_effect=AssertionError):
        for search in ("TagForce5", "TF5", "Duel 12"):
            result = view.query("deck", search, audio_only=True)
            assert [a.game.id for a in result.games] == ["tf5"]
            assert result.games[0].counts["duel"] == result.games[0].counts["deck"] == 1
            assert result.track.source == "installed"
        assert not view.query("deck", "Added").games
        assert view.query("menu", "Glass Archive", audio_only=True).track.source == "bundled"
        assert view.query("deck", "PlayStation", series="Console").games
    view.refresh()
    assert view.query("deck", "Added").game == "tf2"
    seek = SeekState()
    seek.keyboard("Right", 4, 10)
    assert seek.position(1, 10) == 9
    seek.keyboard("End", 1, 10)
    assert seek.release(10) == 10 and seek.position(3, 10) == 3
    seek.drag(-1)
    assert seek.release(10) == 0


def test_rip_plan_confirm_continue_retry(tmp_path: Path) -> None:
    root, rip = tmp_path / "personal", tmp_path / "rip"
    folder = rip / "GX Tag Force 5/Disc 1"
    first = wav(folder / "01 Menu.wav", 1)
    broken = folder / "02 Broken.flac"
    broken.write_bytes(b"bad")
    last = wav(folder / "03 Victory.wav", 2)
    unknown = wav(rip / "Disc 2/04 Title.wav", 3)
    cover = rip / "cover.jpg"
    cover.write_bytes(b"cover")
    repeated = folder / "05 Copy.wav"
    shutil.copyfile(first, repeated)
    bundle = folder / "06 Duel.flac"
    shutil.copyfile(music.ART.parent / "extras/sound/BGM/menu/Glass Archive.flac", bundle)
    plan = music_import.plan_import([rip, first], root=root)
    by_path = {i.path: i for i in plan}
    assert len(plan) == 7 and not root.exists()
    assert by_path[first].game == "tf5" and by_path[first].scene == "menu"
    assert by_path[last].scene == "win" and by_path[unknown].game is None
    assert by_path[repeated].duplicate and not by_path[repeated].in_library
    assert by_path[repeated].duplicate_of == first
    corrected = [replace(i, game="legacy", scene="deck") if i.path == unknown else i for i in plan]
    result = music_import.execute_import(corrected, root=root)
    assert Counter(r.status for r in result) == {
        "imported": 4,
        "unsupported": 1,
        "duplicate": 1,
        "failed": 1,
    }
    assert next(r.reason for r in result if r.path == broken)
    assert (root / "legacy/deck/04 Title.wav").exists()
    assert music_import.plan_import([last], root=root)[0].in_library
    assert (
        Counter(r.status for r in music_import.execute_import(corrected, root=root))["duplicate"]
        == 5
    )


def test_album_tags_and_changed_source(tmp_path: Path) -> None:
    path = tmp_path / "01.wav"
    wav(path, 1)
    payload = b"Master Duel\0"
    chunk = b"INFOIPRD" + len(payload).to_bytes(4, "little") + payload
    data = path.read_bytes() + b"LIST" + len(chunk).to_bytes(4, "little") + chunk
    path.write_bytes(data[:4] + (len(data) - 8).to_bytes(4, "little") + data[8:])
    root = tmp_path / "lib"
    plan = music_import.plan_import(path, root=root)
    assert plan[0].game == "masterduel"
    wav(path, 4)
    (result,) = music_import.execute_import(plan, root=root)
    assert result.status == "failed" and "changed" in result.reason


def test_worker_and_transfer_counts(tmp_path: Path) -> None:
    root, source = tmp_path / "lib", tmp_path / "input"
    wav(root / "tf5/deck/Existing.wav", 1)
    wav(source / "tf5/deck/1.wav", 1)
    wav(source / "tf5/deck/2.wav", 2).write_bytes(b"invalid")
    wav(source / "tf5/deck/3.wav", 3)
    outcome = run_job(lambda: music_import.import_library(source, root=root))
    assert "Imported 1, already present 1, failed 1" in outcome.error
    assert (root / "tf5/deck/3.wav").exists()
    assert run_job(lambda: 1 / 0).error == "division by zero"


def test_report_read_only(tmp_path: Path) -> None:
    sample = tmp_path / "sample"
    wav(sample / "Game/sound/BGM/win/Unknown.wav", 4)
    wav(sample / "Library/tf5/menu/Menu.wav", 2)
    wav(sample / "Library/legacy/deck/Construction.wav", 3)
    copy = tmp_path / "report-copy"
    shutil.copytree(sample, copy)
    game, root = copy / "Game", copy / "Library"
    path = game / "sound/BGM/win/Unknown.wav"
    snapshot = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    output = report(game, root)
    assert str(path) in output and "tf2" in output and "local" in output
    assert snapshot == {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}


def test_legacy_record_edit_order_and_interrupted_restore(tmp_path: Path) -> None:
    game, lib = tmp_path / "game", tmp_path / "lib"
    game.mkdir()
    song = music.Track("tf1", "Former", wav(lib / "tf1/duel/Former.wav", 3), "duel")
    music.use_track(game, song, root=lib)
    state = game / "sound/.edopro-hd-sync/duel"
    selection = state / "selection.json"
    record = json.loads(selection.read_bytes())["tracks"][0]
    selection.write_text(json.dumps(record))
    other = music.Track("tf2", "Next", wav(lib / "tf2/duel/Next.wav", 5), "duel")
    music.add_to_playlist(game, other, root=lib)
    assert active(game, "duel", lib) == ["Former", "Next"]
    music.undo_playlist(game, "duel", root=lib)
    assert active(game, "duel", lib) == ["Former"]
    # After the retirement checkpoint an identical file can be a returned original.
    (state / "restoring.json").write_text('{"previous": []}')
    music.restore_playlist(game, "duel")
    assert (game / "sound/BGM/duel" / record["name"]).is_file()


def test_recover_partial_multitrack_publish(tmp_path: Path) -> None:
    game, lib = tmp_path / "game", tmp_path / "lib"
    original = wav(game / "sound/BGM/Stock.wav", 1)
    one = music.Track("tf3", "First", wav(lib / "tf3/duel/First.wav", 3), "duel")
    two = music.Track("tf4", "Second", wav(lib / "tf4/duel/Second.wav", 4), "duel")
    music.set_playlist(game, "duel", [two, one], root=lib)
    state = game / "sound/.edopro-hd-sync/duel"
    (state / "selection.json").rename(state / "pending.json")
    (
        game
        / "sound/BGM/duel"
        / json.loads((state / "pending.json").read_bytes())["tracks"][1]["name"]
    ).unlink()
    music.restore_playlist(game, "duel")
    assert original.exists() and active(game, "duel", lib) == ["Stock"]


def test_alternate_bank_takes(tmp_path: Path) -> None:
    import struct

    data = [wav(tmp_path / "source.wav", i).read_bytes() for i in (1, 2)]
    name = b"yugi/sound/stream/stage1.wav"
    encoded = bytes((b >> 4 | b << 4) & 255 for b in name).ljust(256, b"\0")
    start = 12 + 268 * 2
    table = bytearray()
    for clip in data:
        table.extend(encoded + struct.pack("<III", start, len(clip), len(clip)))
        start += len(clip)
    bank = tmp_path / "Voice.dat"
    bank.write_bytes(b"KCEJYUGI" + struct.pack("<I", 2) + table + b"".join(data))
    root = tmp_path / "lib"
    assert music_import.import_power_of_chaos(bank, root=root) == (2, 0)
    assert sorted(p.read_bytes() for p in (root / "pocyugi/duel").glob("*.wav")) == sorted(data)


def test_player_preview_and_seek_with_null_output() -> None:
    from functools import partial

    import miniaudio

    from music_player import PreviewPlayer

    path = music.ART.parent / "extras/sound/BGM/menu/Glass Archive.flac"
    player = PreviewPlayer()
    with patch.object(
        miniaudio,
        "PlaybackDevice",
        partial(miniaudio.PlaybackDevice, backends=[miniaudio.Backend.NULL]),
    ):
        try:
            player.play(path)
            assert player.playing and player.seekable
            player.seek(3)
            player.pause()
            assert not player.playing and 3 <= player.resume_position(path) < player.duration
            player.seek(player.duration)
            assert player.resume_position(path) == 0
        finally:
            player.stop()
    assert player.path is None and not player.seekable


@pytest.mark.parametrize("suffix", [".flac", ".ogg", ".mp3"])
def test_album_tag_hints(suffix: str, tmp_path: Path) -> None:
    from music.tags import tags

    album = b"ALBUM=Master Duel"
    comments = b"\0" * 4 + (1).to_bytes(4, "little") + len(album).to_bytes(4, "little") + album
    if suffix == ".flac":
        data = b"fLaC\x84" + len(comments).to_bytes(3, "big") + comments
    elif suffix == ".ogg":
        packet = b"\x03vorbis" + comments
        data = b"OggS" + bytes(22) + bytes([1, len(packet)]) + packet
    else:
        body = b"\x03Master Duel"
        frame = b"TALB" + len(body).to_bytes(4, "big") + b"\0\0" + body
        data = b"ID3\x03\0\0" + len(frame).to_bytes(4, "big") + frame
    path = tmp_path / ("unknown" + suffix)
    path.write_bytes(data)
    assert tags(path) == ["Master Duel"]
    assert music_import.plan_import(path, root=tmp_path / "lib")[0].game == "masterduel"


def test_panel_worker_always_delivers_completion() -> None:
    from concurrent.futures import Future
    from queue import Queue
    from unittest.mock import Mock

    pytest.importorskip("tkinter")
    from music_window import MusicPanel

    stopped = Future()
    stopped.set_result(None)
    panel = Mock(busy=False, events=Queue())
    panel.stop_preview.return_value = stopped

    def fail() -> None:
        raise RuntimeError("decoder worker failed")

    MusicPanel._work(panel, fail, "success")
    assert panel.events.get(timeout=3) == ("done", "decoder worker failed")
