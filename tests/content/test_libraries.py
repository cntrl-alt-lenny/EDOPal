import asyncio
import io
import json
import wave
from dataclasses import replace
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from conftest import FakeHTTP, pack_entry, send
from PySide6.QtGui import QColor, QImage

from content.cache import PackCache
from content.catalogue import PackState, PackStatus, fetch_catalogue, pack_status
from content.download import DownloadError, DownloadFailure
from content.layouts import staged_items
from content.libraries import installed_origins, make_available, remove_pack
from content.manifest import parse_manifest
from content.media import Changes, Origin
from music.importing import plan_import
from music.packs import cover_path, pack_media
from music_library import store_track, tracks, use_track
from sleeves import Library, ensure_gui, png_bytes
from themes import PRESETS, ThemeLibrary
from tools.build_content_release import build_release

VERSION = "5.5.0"


def picture(color: str) -> bytes:
    ensure_gui()
    image = QImage(12, 18, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return png_bytes(image)


def recording(tone: int) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setparams((1, 1, 8000, 0, "NONE", ""))
        wav.writeframes(bytes([tone]) * 800)
    return stream.getvalue()


def publish(folder: Path, cache: PackCache, http: FakeHTTP, kind: str, edition: int) -> PackStatus:
    source = folder / f"source{edition}"
    album = source / kind
    album.mkdir(parents=True)
    color = "red" if edition == 1 else "blue"
    items = []
    if kind in {"wallpaper", "sleeves"}:
        (album / "images").mkdir()
        for name in ("First", "Second"):
            file = f"images/{name}.png"
            (album / file).write_bytes(picture(color))
            fields = {"name": name, "tags": ["ink"]} if kind == "sleeves" else {"title": name}
            items.append({"file": file, **fields})
    else:
        (album / "forbidden").mkdir()
        (album / "forbidden/cover.png").write_bytes(picture(color))
        items.append({"file": "forbidden/cover.png"})
        if kind == "music":
            file = "forbidden/cue.wav"
            items.append({"file": file, "scenes": ["duel", "deck"], "title": "Opening"})
            (album / file).write_bytes(recording(edition))
    (album / "pack.json").write_text(json.dumps({"items": items}))
    (source / "preview.png").write_bytes(picture(color))
    row = pack_entry(http.url, kind=kind, id=kind, version=f"{edition}.0.0")
    for key in ("download_url", "size_bytes", "sha256", "previews"):
        del row[key]
    (source / "packs.json").write_text(json.dumps([{**row, "preview_files": ["preview.png"]}]))
    manifest = build_release(source, folder / f"release{edition}", repo="test/content", tag="demo")
    body = json.loads(manifest.read_bytes())
    entry = body["packs"][0]
    archive = manifest.parent / entry["download_url"].split("/")[-1]
    entry["download_url"] = http.url + "/" + archive.name
    http.routes["/" + archive.name] = lambda handler: send(handler, archive.read_bytes())
    http.routes["/manifest"] = lambda handler: send(handler, json.dumps(body).encode())
    cat = asyncio.run(fetch_catalogue(cache, config_url=http.url + "/manifest"))
    return cat.list_packs(cache, app_version=VERSION)[0]


def install(cache: PackCache, status: PackStatus) -> Changes:
    return asyncio.run(make_available(cache, status, app_version=VERSION))


@pytest.mark.parametrize("kind", ["music", "covers", "wallpaper", "sleeves"])
def test_folder_release_to_library_update_and_remove(
    tmp_path: Path, http: FakeHTTP, kind: str
) -> None:
    root = tmp_path / "profile"
    cache = PackCache(root / "packs")
    game = tmp_path / "game"
    game.mkdir()
    status = publish(tmp_path, cache, http, kind, 1)
    origin = Origin.from_pack(status.pack)
    phases, progress = [], []
    assert status.state is PackState.NOT_DOWNLOADED
    result = asyncio.run(
        make_available(
            cache, status, app_version=VERSION, progress=progress.append, phase=phases.append
        )
    )
    assert result.added and phases == ["downloading", "verifying", "installing"]
    assert progress[-1].bytes_done == status.pack.size_bytes
    assert install(cache, status) == Changes()
    assert installed_origins(cache, kind) == (origin,)
    music = root / "soundtracks"
    themes = ThemeLibrary(root / "themes")
    sleeves = Library(root / "sleeves")
    if kind == "music":
        deck = tracks(game, "deck", root=music)[0]
        duel = tracks(game, "duel", root=music)[0]
        assert deck.origin == duel.origin == origin
        assert deck.game == "forbidden" and deck.title == "Opening" and deck.path == duel.path
        assert plan_import(deck.path, root=music)[0].in_library
        use_track(game, deck, root=music)
        deck.path.write_bytes(recording(44))
    elif kind == "covers":
        first_cover = cover_path("forbidden", music)
        assert first_cover and first_cover.read_bytes() == picture("red")
        first_cover.write_bytes(picture("green"))
    elif kind == "wallpaper":
        first, second = themes.wallpapers()
        assert first.path == second.path and first.item.title == "First" and first.origin == origin
        theme = replace(PRESETS[0], image=first.digest, wallpaper="image")
        saved_id = themes.save(theme)
    else:
        first, second = sleeves.entries("ink")
        assert first.origin == origin and first.tags == ("ink",) and first.image == second.image
        sleeves.edit(first.id, name="My edit", favourite=True)

    newer = publish(tmp_path, cache, http, kind, 2)
    assert newer.state == PackState.UPDATE_AVAILABLE
    assert install(cache, newer).added
    assert installed_origins(cache, kind) == (Origin.from_pack(newer.pack),)
    if kind in {"music", "covers"}:
        assert cover_path("forbidden", music).read_bytes() == picture("blue")
    elif kind == "wallpaper":
        assert all(r.path.read_bytes() == picture("blue") for r in themes.wallpapers())
    else:
        assert sleeves.entries("Second")[0].id == second.id
        assert sleeves.entries("My edit")[0].origin == origin

    assert remove_pack(cache, newer).removed
    assert remove_pack(cache, newer).removed == ()
    assert cache.list_cached() == () and installed_origins(cache, kind) == ()
    if kind == "music":
        (kept,) = pack_media(music)
        assert kept.retained and kept.origin == origin and kept.path.read_bytes() == recording(44)
        active = tracks(game, "deck", root=music)[0]
        assert active.in_playlist and active.origin == origin
    elif kind == "covers":
        (kept,) = pack_media(music)
        assert kept.retained and cover_path("forbidden", music).read_bytes() == picture("green")
    elif kind == "wallpaper":
        assert themes.wallpapers() == ()
        assert themes.image(themes.load(saved_id)).pixelColor(0, 0).name() == "#ff0000"
    else:
        (kept,) = sleeves.entries("ink")
        assert kept.name == "My edit" and kept.favourite and kept.origin == origin


@pytest.mark.parametrize(
    ("kind", "row", "data"),
    [
        ("music", {"file": "forbidden/cue.wav", "scenes": ["unknown"]}, b"bad"),
        ("music", {"file": "forbidden/cue.wav", "scenes": ["deck"]}, b"bad"),
        ("covers", {"file": "unknown/cover.png"}, b"bad"),
        ("covers", {"file": "forbidden/cover.jpg"}, b"bad"),
        ("wallpaper", {"file": "images/a.png", "title": "Title"}, b"bad"),
        ("sleeves", {"file": "images/a.png", "name": "Name", "tags": "bad"}, b"bad"),
    ],
)
def test_rejects_invalid_metadata_or_media(
    tmp_path: Path, kind: str, row: dict, data: bytes
) -> None:
    path = tmp_path / "bad.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("pack.json", json.dumps({"items": [row]}))
        archive.writestr(row["file"], data)
    with pytest.raises(ValueError), staged_items(path, kind, tmp_path):
        pytest.fail("Invalid pack accepted")


@pytest.mark.parametrize("kind", ["music", "covers", "wallpaper", "sleeves"])
@pytest.mark.parametrize("extra", ["../outside", "images/CON.png", "undeclared.exe"])
def test_unsafe_update_keeps_installed_edition(
    tmp_path: Path, http: FakeHTTP, kind: str, extra: str
) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    first = publish(tmp_path, cache, http, kind, 1)
    install(cache, first)
    archive_path = next((tmp_path / "release1").glob("*.zip"))
    with ZipFile(archive_path, "a") as archive:
        archive.writestr(extra, b"unexpected")
    body = archive_path.read_bytes()
    row = pack_entry(http.url, body, kind=kind, id=kind, version="2.0.0")
    http.routes["/asset"] = lambda handler: send(handler, body)
    (pack,) = parse_manifest(json.dumps({"schema_version": 1, "packs": [row]})).packs
    with pytest.raises(ValueError):
        install(cache, pack_status(cache, pack, app_version=VERSION))
    assert installed_origins(cache, kind) == (Origin.from_pack(first.pack),)
    assert not (tmp_path / "outside").exists()


def test_cancel_after_download_and_offline_retry(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    status = publish(tmp_path, cache, http, "wallpaper", 1)
    event = Event()
    with pytest.raises(DownloadError) as error:
        asyncio.run(
            make_available(
                cache,
                status,
                app_version=VERSION,
                cancel=event,
                phase=lambda name: event.set() if name == "verifying" else None,
            )
        )
    assert error.value.reason == DownloadFailure.CANCELLED
    assert installed_origins(cache, "wallpaper") == ()
    calls = len(http.calls)
    assert install(cache, status).added and len(http.calls) == calls
    cache.clear()
    assert installed_origins(cache, "wallpaper")
    assert remove_pack(cache, status).removed


def test_personal_recording_is_reused_and_kept(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    music = cache.root.parent / "soundtracks"
    status = publish(tmp_path, cache, http, "music", 1)
    source = tmp_path / "My track.wav"
    source.write_bytes(recording(1))
    personal, new = store_track(source, "forbidden", "menu", root=music)
    assert new
    install(cache, status)
    (track,) = [r for r in pack_media(music) if r.item.role == "track"]
    assert track.personal and track.path == personal
    assert store_track(source, "forbidden", "deck", root=music) == (personal, False)
    assert remove_pack(cache, status).removed
    assert personal.read_bytes() == source.read_bytes()


def test_worker_conflict_and_legacy_skin_reapply(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    import extras
    from sleeves import apply, reapply, status, undo
    from state_lock import appearance_lock

    library = Library(tmp_path / "profile/sleeves")
    game, config = tmp_path / "game", tmp_path / "profile/config.json"
    sleeve = library.add_image("Custom", picture("cyan"))
    with ThreadPoolExecutor(max_workers=1) as pool, appearance_lock(game):
        apply(library, game, config, mine=sleeve.id)
        for action in (
            lambda: apply(library, game, config, mine=sleeve.id),
            lambda: reapply(library, game, config),
            lambda: undo(library, game, config),
            lambda: extras.apply_sleeve_pngs(
                root=game, config_path=str(config), mine=picture("red")
            ),
        ):
            with pytest.raises(OSError):
                pool.submit(action).result(timeout=5)
        assert status(library, game)["mine"].matches
        # Simulate an unrelated existing skin whose own cover shadows the base texture.
        (game / "skin/Monokai Moon/textures").mkdir(parents=True)
        (game / "skin/Monokai Moon/textures/cover.jpg").write_bytes(picture("red"))
        extras.apply_appearance(str(game), str(config), skin="Moon")
        slot = status(library, game)["mine"]
        assert slot.matches and slot.path == game / "skin/Monokai Moon/textures/cover.png"
        extras.apply_appearance(str(game), str(config), restore=True)
        assert status(library, game)["mine"].matches


def test_sound_layout_reserved_before_network(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    row = pack_entry(http.url, kind="sound-effects")
    (pack,) = parse_manifest(json.dumps({"schema_version": 1, "packs": [row]})).packs
    with pytest.raises(ValueError, match="unavailable"):
        install(cache, pack_status(cache, pack, app_version=VERSION))
    assert not http.calls


def test_pack_limits_are_checked_before_extraction(tmp_path: Path) -> None:
    from content.layouts import MAX_FILES, MAX_METADATA

    path = tmp_path / "large.zip"
    with ZipFile(path, "w") as archive:
        for i in range(MAX_FILES + 1):
            archive.writestr(f"{i}.png", b"x")
    with pytest.raises(ValueError, match="directory"), staged_items(path, "covers", tmp_path):
        pytest.fail("Too many entries")
    with ZipFile(path, "w") as archive:
        archive.writestr("pack.json", b"x" * (MAX_METADATA + 1))
    with pytest.raises(ValueError, match="bounded"), staged_items(path, "covers", tmp_path):
        pytest.fail("Oversized metadata")


def test_second_pack_keeps_shared_cover_and_credits(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    music = cache.root.parent / "soundtracks"
    first = publish(tmp_path, cache, http, "covers", 1)
    install(cache, first)
    second = replace(first, pack=replace(first.pack, id="second", author="Second artist"))
    install(cache, second)
    a, b = pack_media(music)
    assert a.path == b.path and a.origin.author != b.origin.author
    remove_pack(cache, first)
    assert cover_path("forbidden", music) == b.path and b.path.is_file()
    remove_pack(cache, second)
    assert not b.path.exists()


def test_failed_publication_keeps_previous_album(
    tmp_path: Path, http: FakeHTTP, monkeypatch: pytest.MonkeyPatch
) -> None:
    from content.media import MediaCollection

    cache = PackCache(tmp_path / "profile/packs")
    first = publish(tmp_path, cache, http, "music", 1)
    install(cache, first)
    directory = cache.root.parent / "soundtracks/pack-media"
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    second = publish(tmp_path, cache, http, "music", 2)

    def fail(*args: object) -> None:
        raise OSError("disk full during publication")

    with monkeypatch.context() as patch:
        patch.setattr(MediaCollection, "_save", fail)
        with pytest.raises(OSError, match="disk full"):
            install(cache, second)
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert install(cache, second).added


def test_wallpaper_reuses_personal_image_and_preserves_title_edit(
    tmp_path: Path, http: FakeHTTP
) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    status = publish(tmp_path, cache, http, "wallpaper", 1)
    themes = ThemeLibrary(cache.root.parent / "themes")
    source = tmp_path / "source1/wallpaper/images/First.png"
    key = themes.add_image(source)
    install(cache, status)
    a, b = themes.wallpapers()
    assert a.personal and a.path == b.path == themes.directory / "images" / key
    index = themes.directory / "images/packs/index.json"
    rows = json.loads(index.read_bytes())
    rows[0]["item"]["title"] = "My wallpaper"
    index.write_text(json.dumps(rows))
    result = remove_pack(cache, status.pack.id)
    assert result.retained == (a.item.member,) and result.removed == (b.item.member,)
    (kept,) = themes.wallpapers()
    assert kept.item.title == "My wallpaper" and kept.path.read_bytes() == source.read_bytes()


def test_mixed_release_stages_only_chosen_packs(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    publish(tmp_path, cache, http, "music", 1)
    folder = tmp_path / "source1"
    source = folder / "packs.json"
    (entry,) = json.loads(source.read_bytes())
    source.write_text(json.dumps([entry, {**entry, "id": "future-music", "withheld": True}]))
    manifest = build_release(folder, tmp_path / "mixed", tag="proof", repo="test/content")
    assert [p.id for p in parse_manifest(manifest.read_bytes()).packs] == ["music"]
    assert not any(path.name.startswith("future-music") for path in manifest.parent.iterdir())


def test_two_games_without_titles_share_one_recording(tmp_path: Path, http: FakeHTTP) -> None:
    cache = PackCache(tmp_path / "profile/packs")
    status = publish(tmp_path, cache, http, "music", 1)
    folder = tmp_path / "source1/music"
    metadata = {"items": []}
    for game in ("forbidden", "wc2011"):
        (folder / game).mkdir(exist_ok=True)
        (folder / game / "cue.wav").write_bytes(recording(1))
        metadata["items"].append({"file": f"{game}/cue.wav", "scenes": ["lose"]})
    (folder / "forbidden/cover.png").unlink()
    (folder / "pack.json").write_text(json.dumps(metadata))
    manifest = build_release(folder.parent, tmp_path / "multi", tag="proof", repo="test/content")
    (pack,) = parse_manifest(manifest.read_bytes()).packs
    http.routes["/multi"] = lambda handler: send(
        handler, next(manifest.parent.glob("*.zip")).read_bytes()
    )
    install(cache, replace(status, pack=replace(pack, download_url=http.url + "/multi")))
    a, b = pack_media(cache.root.parent / "soundtracks")
    assert a.path == b.path and a.item.title == b.item.title == "cue"
    assert {a.item.game, b.item.game} == {"forbidden", "wc2011"}


def test_folder_skin_and_shared_dependency(tmp_path: Path, http: FakeHTTP) -> None:
    import extras

    source = tmp_path / "source"
    skin = source / "grid/skin/Monokai Grid"
    skin.mkdir(parents=True)
    (skin / "skin.xml").write_bytes(b"<Skin/>")
    (skin / "Button.png").write_bytes(picture("blue"))
    fonts = source / "shared/fonts"
    fonts.mkdir(parents=True)
    for name in ("Inter-Medium.ttf", "InterEDO-Regular.ttf", "Inter-OFL.txt"):
        (fonts / name).write_bytes(b"synthetic")
    (source / "preview.png").write_bytes(picture("blue"))
    rows = []
    for pack_id, kind, title in (("grid", "skin", "Grid"), ("shared", "sound-effects", "Shared")):
        row = pack_entry(http.url, id=pack_id, kind=kind, title=title)
        for key in ("download_url", "size_bytes", "sha256", "previews"):
            del row[key]
        rows.append({**row, "preview_files": ["preview.png"]})
    (source / "packs.json").write_text(json.dumps(rows))
    manifest = build_release(source, tmp_path / "release", repo="test/content", tag="proof")
    cache = PackCache(tmp_path / "profile/packs")
    selected = []
    for pack in parse_manifest(manifest.read_bytes()).packs:
        archive = manifest.parent / f"{pack.id}.1.0.0.zip"
        route = "/" + archive.name
        http.routes[route] = lambda handler, path=archive: send(handler, path.read_bytes())
        pack = replace(pack, download_url=http.url + route)
        status = pack_status(cache, pack, app_version=VERSION)
        assert install(cache, status) == Changes()
        selected.append(pack)
    game, config = tmp_path / "game", cache.root.parent / "config.json"
    extras.apply_appearance(
        str(game),
        str(config),
        skin="Grid",
        skin_pack=selected[0],
        shared_pack=selected[1],
        pack_cache=cache,
    )
    assert extras.current_skin(str(game)) == "Monokai Grid"
    for pack in selected:
        remove_pack(cache, pack.id)
    assert not cache.list_cached()
    extras.apply_appearance(str(game), str(config), restore=True)
    assert extras.current_skin(str(game)) != "Monokai Grid"


def test_downloadable_sleeves_without_bundled_zip(
    tmp_path: Path, http: FakeHTTP, monkeypatch: pytest.MonkeyPatch
) -> None:
    import extras

    monkeypatch.setattr(extras, "PACKS", tmp_path / "no-bundle")
    cache = PackCache(tmp_path / "profile/packs")
    status = publish(tmp_path, cache, http, "sleeves", 1)
    assert len(install(cache, status).added) == 2
    library = Library(cache.root.parent / "sleeves")
    first, second = library.entries()
    # The two entries share one immutable print. Preserve an external replacement,
    # including when the player already kept a copy by renaming it.
    library.edit(first.id, name="My print")
    edited = picture("green")
    library.image_path(first.id).write_bytes(edited)
    assert set(install(cache, status).retained) == {first.id, second.id}
    remove_pack(cache, status)
    assert len(library.entries()) == 2
    assert {library.image_path(e.id).read_bytes() for e in library.entries()} == {edited}
