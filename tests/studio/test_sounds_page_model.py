"""Unit checks for content matching, event choices and cached pack loading."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import sounds
from content.cache import PackCache
from content.catalogue import CatalogueSource, PackState, local_catalogue, resolve_manifest_url
from sounds.sets import InstalledCue
from sounds.settings import SoundSettings
from studio.pages.sounds_model import (
    Snapshot,
    Source,
    choice_for,
    coverage,
    installed_name,
    online_sets,
)


def test_installed_identity_distinguishes_silence_from_unreadable_audio(tmp_path: Path) -> None:
    clip = sounds.Clip("wav", b"", 0.25, "draw hash")
    named = sounds.SoundSet("Library mix", {"draw": clip, "attack": None})
    source = Source("example", named, "Saved")
    draw = InstalledCue("draw", (tmp_path / "draw.wav",), clip)
    attack = InstalledCue("attack", (), None)
    snapshot = Snapshot(
        (draw, attack),
        sounds.SoundSet("Installed", {"draw": clip}),
        SoundSettings(True, 100, "default"),
        False,
        False,
        {},
    )
    assert installed_name(snapshot, (source,)) == "Library mix"
    unreadable = replace(attack, paths=(tmp_path / "attack.wav",), error="Unreadable")
    assert installed_name(replace(snapshot, rows=(draw, unreadable)), (source,)) == "Installed mix"
    assert coverage(named) == (1, 1)
    assert choice_for("attack", named, snapshot, {}) == ("Silent", None, None)
    assert choice_for("draw", sounds.SoundSet("Draft"), snapshot, {}) == (
        "Keep installed sound",
        0.25,
        clip,
    )


def test_saved_catalogue_reopens_verified_previous_pack_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("EDOPRO_CONTENT_MANIFEST_URL", raising=False)
    cache = PackCache(tmp_path)
    url = resolve_manifest_url("https://example.test/catalogue.json")
    data = sounds.SoundSet(
        "Cached pack",
        {"damage": None},
        sounds.Credits("Original author", "CC0"),
    )
    path = tmp_path / "old.edosounds"
    sounds.export_set(data, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    group = tmp_path / "pack-tabletop"
    group.mkdir()
    path.rename(group / f"1.0.0--{digest}.zip")
    title = "New edition"
    pack = {
        "id": "tabletop",
        "kind": "sound-effects",
        "title": title,
        "description": "Demo set",
        "author": "Another author",
        "license": "CC0",
        "attribution": "A demo set.",
        "source_url": "https://example.test/",
        "version": "2.0.0",
        "download_url": "https://example.test/pack.zip",
        "sha256": "f" * 64,
        "size_bytes": 400,
        "min_app_version": "5.5.0",
        "previews": [{"url": "https://example.test/preview.png", "sha256": "0" * 64}],
    }
    catalogue = tmp_path / ("manifest-" + hashlib.sha256(url.encode()).hexdigest())
    catalogue.mkdir()
    raw = json.dumps({"schema_version": 1, "packs": [pack]})
    (catalogue / "catalogue.json").write_text(json.dumps({"manifest": raw, "etag": "example"}))
    stored = local_catalogue(cache, config_url=url)
    assert stored.source is CatalogueSource.CACHED
    result = online_sets(cache, "5.5.0", stored)
    assert result.packs[0].status.state is PackState.UPDATE_AVAILABLE
    assert result.packs[0].sound_set == data


def test_optional_cue_describes_its_duel_event() -> None:
    from studio.pages.sounds_model import event_description

    assert event_description("token").startswith("A Token is summoned.")
    assert "revealed" in event_description("reveal")
    assert "attacks the opponent directly" in event_description("directattack")
    assert all(
        "EDOPro version" in event_description(key) for key in ("token", "reveal", "directattack")
    )
