import hashlib
import json
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtGui import QImage

import backup
import music_library
import sleeves
from decks import DeckIndexer
from music import audio, catalogue, discovery
from replays import read_replay
from tests.music.test_engine import wav
from tests.replays.test_replays import streamed
from themes import PRESETS, ThemeLibrary

from .test_transfer import put


def photo(path: Path) -> Path:
    image = QImage(24, 32, QImage.Format.Format_RGB32)
    image.fill(0xFF123456)
    assert image.save(str(path))
    return path


def test_fresh_profile_round_trip(pair: tuple, snapshot: Path) -> None:
    src, dst = pair
    original = photo(snapshot.with_suffix(".png"))
    skins = ThemeLibrary(src.data / "themes")
    look = replace(PRESETS[0], wallpaper="image", image=skins.add_image(original))
    theme_id = skins.save(look)
    library = sleeves.Library(src.data / "sleeves")
    design = library.add_design("Stars", sleeves.Recipe("stars", seed=123))
    imported = library.add_image("Photo", original)
    root = src.data / "soundtracks"
    album = catalogue.create_game("My recordings", root=root)
    for n, scene in enumerate(catalogue.SCENES):
        audio.import_track(wav(src.data / (scene + ".wav"), n), album.id, scene, root=root)
    put(src, "game/deck/Trial.ydk", b"#main\n123\n#extra\n!side\n")
    put(src, "game/replay/Trial.yrpX", streamed())
    backup.create(src, snapshot, ["decks", "replays", "studio", "audio"])
    assert not dst.data.exists() and not list(dst.game.iterdir())
    backup.execute(backup.plan_restore(snapshot, dst))

    skins = ThemeLibrary(dst.data / "themes")
    restored = skins.entries()[theme_id]
    assert restored == look and skins.image(restored).size().toTuple() == (24, 32)
    library = sleeves.Library(dst.data / "sleeves")
    assert library.entries("Stars") == [design]
    rendered = sleeves.render(library.entries("Stars")[0].recipe)
    saved_print = sleeves.decode(library.image_path(design.id).read_bytes())
    assert saved_print.convertToFormat(rendered.format()) == rendered
    assert library.entries("Photo") == [imported]
    restored_photo = library.image_path(imported.id, original=True)
    assert restored_photo.read_bytes() == original.read_bytes()
    cropped = library.add_image("New crop", restored_photo, sleeves.Layout(crop=(2, 1, 15, 24)))
    assert cropped.layout.crop == (2, 1, 15, 24)
    root = dst.data / "soundtracks"
    assert catalogue.catalog(root=root)[album.id] == album
    for scene in catalogue.SCENES:
        (track,) = [t for t in discovery.tracks(dst.game, scene, root=root) if t.game == album.id]
        audio.validate_audio(track.path)
        assert track.path.read_bytes() == (src.data / (scene + ".wav")).read_bytes()
        if scene == "menu":
            music_library.use_track(dst.game, track, root=root)
            (active,) = music_library.playlist(dst.game, scene, root=root)
            assert (active.game, active.digest) == (album.id, track.digest)
    (deck,) = DeckIndexer(dst.game).scan().decks
    assert deck.preview.sections.main == (123,)
    assert read_replay(dst.game / "replay/Trial.yrpX").turns == 2


@pytest.mark.parametrize("selection", [("studio",), ("audio",)])
@pytest.mark.parametrize("combined", [False, True])
def test_soundtrack_selections(
    pair: tuple, snapshot: Path, selection: tuple[str], combined: bool
) -> None:
    src, dst = pair
    album = catalogue.create_game("私の音楽", root=src.data / "soundtracks")
    tracks = [f"{album.id}/{s}/cue.ogg" for s in catalogue.SCENES]
    for path in tracks:
        put(src, "studio/soundtracks/" + path)
    backup.create(src, snapshot, ("studio", "audio") if combined else selection)
    backup.execute(backup.plan_restore(snapshot, dst, categories=selection))
    root = dst.data / "soundtracks"
    assert catalogue.catalog(root=root)[album.id] == album
    if "audio" in selection:
        assert all((root / path).is_file() for path in tracks)
    else:
        assert json.loads((root / "index.json").read_bytes()) == sorted(tracks)
        assert not any((root / path).exists() for path in tracks)
    assert {diff.status for diff in backup.compare(dst, snapshot, selection)} == {"identical"}


@pytest.mark.parametrize("object_kind", ["wallpaper", "original", "print"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_incomplete_collection_never_published(
    pair: tuple, snapshot: Path, object_kind: str, damage: str
) -> None:
    src, _ = pair
    original = photo(snapshot.with_suffix(".png"))
    if object_kind == "wallpaper":
        library = ThemeLibrary(src.data / "themes")
        digest = library.add_image(original)
        library.save(replace(PRESETS[0], wallpaper="image", image=digest))
        path = src.data / "themes/images" / digest
    else:
        library = sleeves.Library(src.data / "sleeves")
        entry = library.add_image("My sleeve", original)
        path = library.image_path(entry.id, original=object_kind == "original")
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    with pytest.raises((OSError, ValueError)):
        backup.create(src, snapshot, ["studio"])
    assert not snapshot.exists()


def test_keep_both_themes_remain_editable_and_undoable(pair: tuple, snapshot: Path) -> None:
    src, dst = pair
    theme_id = ThemeLibrary(src.data / "themes").save(PRESETS[0])
    library = ThemeLibrary(dst.data / "themes")
    library.save(PRESETS[1], theme_id)
    backup.create(src, snapshot, ["studio"])
    plan = backup.plan_restore(snapshot, dst, "keep_both")
    tx = backup.execute(plan)
    incoming = Path(plan.items[0].target).stem
    assert library.entries() == {theme_id: PRESETS[1], incoming: PRESETS[0]}
    backup.recover(dst, tx.id, "rollback")
    assert library.entries() == {theme_id: PRESETS[1]}


def test_undo_preserves_original_used_by_later_theme(pair: tuple, snapshot: Path) -> None:
    src, dst = pair
    library = ThemeLibrary(src.data / "themes")
    digest = library.add_image(photo(snapshot.with_suffix(".png")))
    look = replace(PRESETS[0], wallpaper="image", image=digest)
    library.save(look)
    backup.create(src, snapshot, ["studio"])
    tx = backup.execute(backup.plan_restore(snapshot, dst))
    library = ThemeLibrary(dst.data / "themes")
    later = library.save(replace(look, name="My new theme"))
    assert not backup.preview_undo(dst, tx.id).available
    with pytest.raises(ValueError, match="still uses"):
        backup.recover(dst, tx.id, "rollback")
    assert library.image(library.load(later)).size().toTuple() == (24, 32)


def legacy_archive(path: Path, files: dict[str, tuple[str, bytes]]) -> None:
    rows = []
    with ZipFile(path, "w") as archive:
        for name, (category, data) in files.items():
            rows.append(
                dict(
                    path=name,
                    category=category,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
            archive.writestr(name, data)
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "app_version": "5.5.0",
                    "source_platform": "linux",
                    "edopro_version": None,
                    "categories": sorted({row["category"] for row in rows}),
                    "entries": rows,
                }
            ),
        )


def test_legacy_two_scene_list_and_audio(pair: tuple, snapshot: Path) -> None:
    _, dst = pair
    tracks = ["local/deck/a.ogg", "local/duel/b.ogg"]
    prefix = "studio/soundtracks/"
    legacy_archive(
        snapshot,
        {
            prefix + "index.json": ("studio", json.dumps(tracks).encode()),
            **{prefix + track: ("audio", track.encode()) for track in tracks},
        },
    )
    backup.execute(backup.plan_restore(snapshot, dst))
    assert json.loads(dst.target(prefix + "index.json").read_bytes()) == tracks
    assert all(dst.target(prefix + track).read_bytes() == track.encode() for track in tracks)


def test_legacy_incoming_theme_is_restored_under_valid_id(pair: tuple, snapshot: Path) -> None:
    _, dst = pair
    stem = "b" * 32
    legacy_archive(
        snapshot,
        {
            f"studio/themes/{stem}.json": ("studio", PRESETS[0].to_json()),
            f"studio/themes/{stem} (incoming 2).json": ("studio", PRESETS[1].to_json()),
        },
    )
    tx = backup.execute(backup.plan_restore(snapshot, dst))
    library = ThemeLibrary(dst.data / "themes")
    assert set(library.entries().values()) == {PRESETS[0], PRESETS[1]}
    backup.recover(dst, tx.id, "rollback")
    assert not library.entries()


def test_legacy_missing_photo_requires_matching_local_original(pair: tuple, snapshot: Path) -> None:
    _, dst = pair
    original = photo(snapshot.with_suffix(".png"))
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    look = replace(PRESETS[0], wallpaper="image", image=digest)
    theme_id = "a" * 32
    legacy_archive(snapshot, {f"studio/themes/{theme_id}.json": ("studio", look.to_json())})
    plan = backup.plan_restore(snapshot, dst)
    with pytest.raises(ValueError, match="editable asset"):
        backup.execute(plan)
    assert not backup.transactions(dst)
    library = ThemeLibrary(dst.data / "themes")
    assert library.add_image(original) == digest
    backup.execute(plan)
    assert library.image(library.load(theme_id)).size().toTuple() == (24, 32)


def test_collection_skips_download_cache(pair: tuple, snapshot: Path) -> None:
    src, _ = pair
    put(src, "studio/sleeves/card-art/123.jpg")
    put(src, "studio/sleeves/card-art/missing.json", b"{}")
    assert not backup.create(src, snapshot, ["studio"]).entries
