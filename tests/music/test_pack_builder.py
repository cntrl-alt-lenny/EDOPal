import importlib.util
import json
import wave
from pathlib import Path

import miniaudio
import pytest
from PIL import Image

PACKS = Path(__file__).resolve().parents[2] / "packs"
if not (PACKS / "music/build_pack.py").is_file():
    pytest.skip("Music pack authoring tests need the separate packs checkout", allow_module_level=True)
builder_spec = importlib.util.spec_from_file_location("music_build_pack", PACKS / "music/build_pack.py")
builder = importlib.util.module_from_spec(builder_spec)
builder_spec.loader.exec_module(builder)


@pytest.fixture
def authoring_folder(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with wave.open(str(source / "夜.wav"), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", ""))
        output.writeframes(b"\x01\x02" * 168_000)
    image = source / "jacket.png"
    Image.new("RGB", (50, 80), "#234054").save(image)
    kwargs = dict(
        slug="night",
        game="Night",
        name="Night",
        year=2026,
        platform="EDOPal",
        cover=image,
        credits=[{"what": "Audio and artwork", "source": "Example", "url": ""}],
    )
    return source, kwargs


def test_copy_without_ffmpeg_makes_a_bounded_clip_and_keeps_metadata(authoring_folder, tmp_path, monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda _: None)
    source, kwargs = authoring_folder
    builder.write_json(source / "tracks.json", {"夜.wav": {"title": "Night", "suggested_roles": ["menu", "deck"]}})
    root = tmp_path / "out"
    folder = builder.build(source, root, **kwargs)
    manifest = json.loads((folder / "manifest.json").read_text())
    track = manifest["tracks"][0]
    assert track == dict(
        id="track-1", title="Night", path="audio/track-1.wav", suggested_roles=["menu", "deck"], duration_seconds=21
    )
    assert (source / "夜.wav").read_bytes() == (folder / track["path"]).read_bytes()
    assert miniaudio.get_file_info(str(root / "previews/music-night.wav")).duration == 20
    with pytest.raises(ValueError, match="already exists"):
        builder.build(source, root, **kwargs)


def test_invalid_audio_leaves_no_published_pack(authoring_folder, tmp_path, monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda _: None)
    source, kwargs = authoring_folder
    (source / "bad.ogg").write_bytes(b"damaged")
    root = tmp_path / "out"
    with pytest.raises(miniaudio.DecodeError):
        builder.build(source, root, **kwargs)
    assert not list(root.rglob("*.webp"))
    assert not list(root.rglob("manifest.json"))


def test_catalog_matches_distributable_assets():
    catalog = json.loads((PACKS / "catalog.json").read_text(encoding="utf-8"))
    rows = [row for row in catalog["packs"] if row["kind"] == "music"]
    samples = [r for r in rows if r["id"].startswith("music-sample-")]
    assert rows, "the catalog lists the game soundtracks"
    for row in rows:
        folder = PACKS / row["url"]
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["tracks"] == row["meta"]["tracks"]
        assert len(manifest["tracks"]) == row["meta"]["track_count"]
        files = {
            p.relative_to(folder).as_posix(): builder.digest(p)
            for p in folder.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        }
        assert files == manifest["files"] and manifest["cover"] in files
        assert manifest["credits"] and all(c.keys() >= {"what", "source", "url"} for c in manifest["credits"])
        with Image.open(PACKS / row["thumbnail"]) as thumb:
            assert max(thumb.size) <= 480
        assert (PACKS / row["thumbnail"]).stat().st_size < 80_000
        if manifest.get("builtin"):
            continue  # EDOPro's stock soundtrack: tracks live in the game, not in the pack
        for track in manifest["tracks"]:
            audio = miniaudio.get_file_info(str(folder / track["path"]))
            assert abs(track["duration_seconds"] - audio.duration) < 0.01
            if row in samples:
                assert audio.sample_rate == 44100 and audio.nchannels == 2
        if row in samples:
            assert row["meta"]["track_count"] >= 2
            assert row["meta"]["preview_sha256"] == builder.digest(PACKS / row["preview"])
            assert row["meta"]["preview_track"] == manifest["tracks"][0]["id"]
            assert 19.9 <= miniaudio.get_file_info(str(PACKS / row["preview"])).duration <= 20.1
        elif not manifest["tracks"]:
            assert set(files) == {manifest["cover"]}
            assert not row["preview"]
