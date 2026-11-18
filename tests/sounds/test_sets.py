import json
import stat
import warnings
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

import extras
from sounds import (
    EVENTS,
    Credits,
    SoundLibrary,
    SoundSet,
    bundled_sets,
    chant_key,
    event_for,
    export_set,
    installed_set,
    load_clip,
    load_pack,
    read_set,
    scan_installation,
)
from sounds.audio import MAX_CLIP_BYTES, decode_clip


def test_catalogue_matches_both_packs_and_expands_shared_aliases():
    assert len(EVENTS) == len({event.stem for event in EVENTS}) == 24
    assert all(event.formats == ("wav", "mp3", "ogg", "flac") for event in EVENTS)
    thonk, synth = bundled_sets()
    with ZipFile(extras.PACKS / "shared.zip") as archive:
        aliases = json.loads(archive.read("duplicates.json"))
        names = set(archive.namelist()) | aliases.keys()
        expected = {Path(name).stem for name in names if name.startswith("sound/")}
        sound_aliases = {
            name: member for name, member in aliases.items() if name.startswith("sound/")
        }
        assert sound_aliases
        for name, member in sound_aliases.items():
            assert thonk.assignments[Path(name).stem].data == archive.read(member)
    assert expected == set(thonk.assignments) == {event.stem for event in EVENTS}
    assert set(synth.assignments) == {path.stem for path in (extras.ASSETS / "sound").glob("*.wav")}
    assert len(synth.assignments) == 10
    assert thonk.pick("phase").credits.license == "CC0 1.0"


def test_scan_respects_file_priority_and_a_bad_primary_is_visible(game, wav):
    sound = game / "sound"
    (sound / "draw.wav").write_bytes(wav())
    (sound / "draw.ogg").write_bytes(b"lower priority")
    (sound / "attack.wav").touch()
    (sound / "attack").mkdir()
    (sound / "attack/24550676.wav").write_bytes(wav())
    rows = {row.key: row for row in scan_installation(game)}
    assert [path.name for path in rows["draw"].paths] == ["draw.wav", "draw.ogg"]
    assert rows["attack"].error
    assert rows["phase"].path is None
    assert rows[chant_key("attack", 24550676)].clip is not None
    snapshot = installed_set(tuple(rows.values()))
    assert set(snapshot.assignments) == {"draw", "chant/attack/24550676"}


def test_mix_with_silence_and_persist_portable_archive(tmp_path, wav, monkeypatch):
    monkeypatch.setattr("config.user_data_dir", lambda: str(tmp_path))
    source = tmp_path / "Imported.WAV"
    source.write_bytes(wav())
    credits = Credits("Author", "Private", "A personal recording")
    thonk, _ = bundled_sets()
    chant = chant_key("summon", 24550676)
    mixed = (
        SoundSet("A mixed set", credits=credits)
        .with_assignment("activate", load_clip(source, credits=credits))
        .with_assignment("draw", thonk.pick("draw"))
        .with_assignment("attack", None)
        .with_assignment(chant, thonk.pick("summon"))
        .with_assignment("phase", None)
        .without_assignment("phase")
    )
    source.unlink()
    path = tmp_path / "portable.edosounds"
    export_set(mixed, path)
    with ZipFile(path) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "audio/draw.wav",
            "audio/activate.wav",
            "audio/chant/summon/24550676.wav",
        }
    assert read_set(path) == load_pack(path) == mixed
    with pytest.raises(ValueError, match="sound-effects"):
        load_pack(path, kind="music")
    library = SoundLibrary()
    assert library.directory == tmp_path / "sounds"
    set_id = library.import_set(path)
    broken = library.directory / ("a" * 32 + ".edosounds")
    broken.write_bytes(b"damaged")
    entries = {entry.id: entry for entry in library.list()}
    assert entries[set_id].sound_set == mixed
    assert entries[broken.stem].error
    assert library.load(set_id) == mixed
    with pytest.raises(ValueError, match="ID"):
        library.load("../portable")
    assert event_for(chant).group == "Chants"
    with pytest.raises(ValueError, match="Unknown"):
        chant_key("summon", 2**32)


@pytest.mark.parametrize(
    "member",
    [
        "../escape.wav",
        "audio/../escape.wav",
        "/escape.wav",
        "C:/escape.wav",
        "audio\\draw.wav",
        "audio/DRAW.wav",
        "other.txt",
        "audio/draw.wav/",
    ],
)
def test_reject_unexpected_and_escaping_members_without_extracting(tmp_path, member):
    path = tmp_path / "bad.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", _manifest({}))
        archive.writestr(member, b"no")
    with pytest.raises(ValueError):
        read_set(path)
    assert list(tmp_path.iterdir()) == [path]


def _manifest(events):
    return json.dumps({"kind": "sound-effects", "version": 1, "name": "Pack", "events": events})


@pytest.mark.parametrize("attack", ["duplicate", "duplicate key", "link", "bomb", "crc"])
def test_reject_ambiguous_oversize_and_corrupted_archives(tmp_path, attack, wav):
    path = tmp_path / "bad.zip"
    cue = "audio/draw.wav"
    data = wav()
    with warnings.catch_warnings(), ZipFile(path, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        manifest = _manifest({"draw": {"format": "wav"}})
        if attack == "duplicate key":
            manifest = _manifest({}).replace('"events": {}', '"events": {}, "events": {}')
        archive.writestr("manifest.json", manifest)
        if attack == "duplicate":
            archive.writestr(cue, data)
            archive.writestr(cue, data)
        elif attack == "link":
            link = ZipInfo(cue)
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../outside")
        elif attack == "bomb":
            archive.writestr(cue, b"0" * (MAX_CLIP_BYTES + 1), compress_type=ZIP_DEFLATED)
        elif attack == "crc":
            archive.writestr(cue, data)
    if attack == "crc":
        path.write_bytes(path.read_bytes().replace(data, bytes(len(data)), 1))
    with pytest.raises(ValueError):
        read_set(path)


def test_import_enforces_audio_type_duration_size_and_data(tmp_path, wav):
    assert decode_clip(wav(10), "wav").duration == 10
    with pytest.raises(ValueError, match="10 seconds"):
        decode_clip(wav(10.1), "wav")
    with pytest.raises(ValueError, match="sample rate"):
        decode_clip(wav(rate=4000), "wav")
    with pytest.raises(ValueError, match="no playable"):
        decode_clip(wav(0), "wav")
    with pytest.raises(ValueError, match="decodable MP3"):
        decode_clip(wav(), "mp3")
    with pytest.raises(ValueError, match="decodable WAV"):
        decode_clip(b"not an audio file", "wav")
    too_big = tmp_path / "huge.wav"
    too_big.write_bytes(bytes(MAX_CLIP_BYTES + 1))
    with pytest.raises(ValueError, match="4,194,304-byte limit"):
        load_clip(too_big)


def test_archives_retain_mp3_and_flac_audio(tmp_path):
    # Eight silent MPEG-1 Layer III frames and a 160-sample FLAC constant frame.
    mp3 = (bytes.fromhex("ff fb 90 64") + bytes(413)) * 8
    flac = (
        bytes.fromhex("664c61438000002200a000a000000000000001f400f0000000a0")
        + bytes(16)
        + bytes.fromhex("fff86408009f37001234ad96")
    )
    clips = {"draw": decode_clip(mp3, "mp3"), "attack": decode_clip(flac, "flac")}
    path = tmp_path / "lossless-and-lossy.edosounds"
    export_set(SoundSet("Two formats", clips), path)
    assert read_set(path).assignments == clips
