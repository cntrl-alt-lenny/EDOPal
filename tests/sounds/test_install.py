import hashlib
import json
from pathlib import Path

import pytest

import extras
from sounds import (
    SoundSet,
    apply_set,
    bundled_sets,
    can_restore,
    can_undo,
    chant_key,
    restore,
    undo,
)
from sounds.audio import decode_clip


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "studio-config" / "config.json"


def test_apply_subset_undo_and_restore_original_files(game, config_path, wav):
    sound = game / "sound"
    (sound / "draw.wav").write_bytes(original := wav(value=1))
    (sound / "draw.mp3").write_bytes(b"original alternate")
    (sound / "attack.ogg").write_bytes(b"original attack")
    (sound / "BGM").mkdir()
    (sound / "BGM/draw.wav").write_bytes(b"untouched music")
    chant = chant_key("summon", 24550676)
    clip = decode_clip(wav(value=2), "wav")
    mixed = SoundSet("Mixed", {"draw": clip, "attack": None, chant: clip})
    apply_set(game, config_path, mixed, events=("draw", "phase"))
    assert (sound / "draw.wav").read_bytes() == clip.data
    assert not (sound / "draw.mp3").exists()
    assert (sound / "attack.ogg").exists()
    assert not (sound / "summon").exists()

    apply_set(game, config_path, mixed, events=("attack", chant))
    assert not (sound / "attack.ogg").exists()
    assert (sound / "summon/24550676.wav").read_bytes() == clip.data
    assert can_undo(game, config_path) and can_restore(game, config_path)
    undo(game, config_path)
    assert (sound / "attack.ogg").read_bytes() == b"original attack"
    assert not (sound / "summon/24550676.wav").exists()
    assert not can_undo(game, config_path)
    assert can_restore(game, config_path)
    restore(game, config_path)
    assert (sound / "draw.wav").read_bytes() == original
    assert (sound / "draw.mp3").read_bytes() == b"original alternate"
    assert not can_restore(game, config_path)
    assert (sound / "BGM/draw.wav").read_bytes() == b"untouched music"
    undo(game, config_path)
    assert (sound / "draw.wav").read_bytes() == clip.data
    assert not (sound / "draw.mp3").exists()


@pytest.mark.parametrize("mutation", ["previous bytes", "removed", "new alternative"])
def test_drift_blocks_all_writes_including_restore_and_legacy_apply(
    game, config_path, wav, mutation
):
    sound = game / "sound"
    (sound / "draw.wav").write_bytes(original := wav(value=1))
    clip = decode_clip(wav(value=2), "wav")
    mixed = SoundSet("Mixed", {"draw": clip, "attack": clip})
    apply_set(game, config_path, mixed)
    if mutation == "previous bytes":
        (sound / "draw.wav").write_bytes(original)
        expected = "draw.wav"
    elif mutation == "removed":
        (sound / "draw.wav").unlink()
        expected = "draw.wav"
    else:
        (sound / "draw.flac").write_bytes(b"external replacement")
        expected = "draw.flac"
    for operation in (
        lambda: apply_set(game, config_path, SoundSet("Quiet", {"attack": None})),
        lambda: undo(game, config_path),
        lambda: restore(game, config_path),
        lambda: extras.apply_appearance(str(game), str(config_path), effects="Synth"),
    ):
        with pytest.raises(ValueError, match=expected):
            operation()
        assert (sound / "attack.wav").read_bytes() == clip.data
        assert not (sound / "phase.wav").exists()


def test_interrupted_apply_can_be_undone_without_consuming_last_change(
    game, config_path, wav, monkeypatch
):
    sound = game / "sound"
    (sound / "draw.mp3").write_bytes(b"before")
    clip = decode_clip(wav(), "wav")
    apply_set(game, config_path, SoundSet("Earlier", {"summon": clip}))
    original_write = extras._atomic_write

    def interrupt(path: Path, data: bytes) -> None:
        if path == sound / "attack.wav":
            raise KeyboardInterrupt
        original_write(path, data)

    mixed = SoundSet("New", {"draw": None, "attack": clip})
    with monkeypatch.context() as patch:
        patch.setattr(extras, "_atomic_write", interrupt)
        with pytest.raises(KeyboardInterrupt):
            apply_set(game, config_path, mixed)
    assert not (sound / "draw.mp3").exists()
    assert can_undo(game, config_path)
    undo(game, config_path)
    assert (sound / "draw.mp3").read_bytes() == b"before"
    assert (sound / "summon.wav").read_bytes() == clip.data
    undo(game, config_path)
    assert not (sound / "summon.wav").exists()


def test_interrupted_restore_retries_and_recovers_original(game, config_path, wav, monkeypatch):
    sound = game / "sound"
    (sound / "draw.wav").write_bytes(original := wav(value=1))
    (sound / "attack.wav").write_bytes(original)
    clip = decode_clip(wav(value=2), "wav")
    apply_set(game, config_path, SoundSet("New", {"draw": clip, "attack": clip}))
    original_write = extras._atomic_write

    def interrupt(path: Path, data: bytes) -> None:
        if path == sound / "draw.wav" and data == original:
            raise KeyboardInterrupt
        original_write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(extras, "_atomic_write", interrupt)
        with pytest.raises(KeyboardInterrupt):
            restore(game, config_path)
    assert (sound / "attack.wav").read_bytes() == original
    assert (sound / "draw.wav").read_bytes() == clip.data
    restore(game, config_path)
    assert (sound / "draw.wav").read_bytes() == original
    assert (sound / "attack.wav").read_bytes() == original


def test_latest_legacy_journal_is_adopted_by_new_and_old_apis(game, config_path, wav):
    sound = game / "sound"
    before = wav(value=1)
    after = wav(value=2)
    relative = "sound/draw.wav"
    (game / relative).write_bytes(after)
    first, last = [hashlib.sha256(data).hexdigest() for data in (before, after)]
    backups = extras._backup_dir(game, config_path)
    legacy = backups / "undo-effects/last.json"
    extras._atomic_write(legacy, json.dumps({relative: [first, last]}).encode())
    extras._atomic_write(backups / first[:16] / relative, before)
    assert can_restore(game, config_path) and extras.can_undo(
        str(game), str(config_path), "effects"
    )
    thonk, _ = bundled_sets()
    apply_set(game, config_path, thonk, events=["draw"])
    extras.apply_appearance(str(game), str(config_path), undo_group="effects")
    assert (sound / "draw.wav").read_bytes() == after
    restore(game, config_path)
    assert (sound / "draw.wav").read_bytes() == before
    assert not legacy.exists()


def test_noncanonical_chant_is_detected_before_applying_silence(game, config_path, wav):
    sound = game / "sound"
    (sound / "attack").mkdir()
    alias = sound / "attack/0012345 custom.wav"
    alias.write_bytes(wav())
    with pytest.raises(ValueError, match="nonstandard chant name"):
        apply_set(game, config_path, SoundSet("Quiet", {chant_key("attack", 12345): None}))
    assert alias.is_file()
