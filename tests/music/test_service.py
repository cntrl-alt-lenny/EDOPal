import hashlib
import json
from concurrent.futures import CancelledError, ThreadPoolExecutor

import pytest

from edopal.core.install import RunState
from edopal.core.jobs import JobContext
from edopal.core.pack_client import GitHubPacks
from edopal.core.storage import GameRunningError
from edopal.dev.preview import make_core
from edopal.features.music.service import ROLES, ConfirmClosedError, MusicService

CTX = JobContext()


@pytest.fixture
def service(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    (game / "config").mkdir()
    (game / "config/system.conf").write_bytes(b"music_volume = 34\nenable_music = 0 # off\n")
    root = tmp_path / "packs"
    folder = root / "music/test"
    (folder / "audio").mkdir(parents=True)
    tracks = []
    files = {}
    for index, title in enumerate(("Morning Sun", "Night Tide")):
        track_id = title.lower().replace(" ", "-")
        relative = f"audio/{track_id}.ogg"
        payload = f"sample full track {index}".encode()
        (folder / relative).write_bytes(payload)
        files[relative] = hashlib.sha256(payload).hexdigest()
        tracks.append({"id": track_id, "title": title, "path": relative, "duration_seconds": 24})
    data = {
        "schema": 1,
        "id": "music-test",
        "kind": "music",
        "name": "Test",
        "version": "1.0.0",
        "cover": "cover.webp",
        "tracks": tracks,
        "files": files,
    }
    (folder / "manifest.json").write_text(json.dumps(data))
    (root / "preview.ogg").write_bytes(b"preview")
    (root / "thumb.webp").write_bytes(b"thumbnail")
    meta = {
        "game": "Test game",
        "year": 2004,
        "platform": "Windows",
        "tracks": tracks,
        "track_count": 2,
        "preview_sha256": hashlib.sha256(b"preview").hexdigest(),
    }
    row = {
        "id": "music-test",
        "kind": "music",
        "name": "Test",
        "version": "1.0.0",
        "url": "music/test",
        "size": 1,
        "sha256": "",
        "thumbnail": "thumb.webp",
        "preview": "preview.ogg",
        "meta": meta,
    }
    (root / "catalog.json").write_text(json.dumps({"packs": [row]}))
    core = make_core(game, root, tmp_path / "home")
    core.packs = GitHubPacks(core.paths, local_dir=root)
    return MusicService(core)


def _put(install, relative, payload=b"original"):
    path = install.path("sound", "BGM", relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_development_catalog_preview_then_full_track(service):
    (album,) = service.catalog(CTX)
    assert album.game == "Test game"
    assert album.cover.read_bytes() == b"thumbnail"
    assert not album.cached
    assert not service.load_album(album, CTX).cached
    assert service.preview(album, album.tracks[0], CTX)[0].read_bytes() == b"preview"
    assert service.core.packs.cached(album.id) is None
    assert service.preview(album, album.tracks[1], CTX)[0].read_bytes() == b"sample full track 1"
    assert service.load_album(album, CTX).cached
    assert service.preview(album, album.tracks[0], CTX)[0].read_bytes() == b"sample full track 0"


def test_release_catalog_track_discovery_and_stub(service):
    root = service.core.packs.local_dir
    rows = json.loads((root / "catalog.json").read_text())["packs"]
    rows[0]["meta"].pop("tracks")
    rows.append({**rows[0], "id": "music-stub", "meta": {"track_count": 0}, "url": "unused"})
    (root / "catalog.json").write_text(json.dumps({"packs": rows}))
    album, stub = service.catalog(CTX)
    assert not album.tracks
    assert [t.id for t in service.load_album(album, CTX).tracks] == ["morning-sun", "night-tide"]
    assert service.load_album(stub, CTX) == stub
    assert service.core.packs.cached(stub.id) is None


def test_preview_corruption_and_file_hash_fail_safely(service):
    (album,) = service.catalog(CTX)
    (service.core.packs.local_dir / "preview.ogg").write_bytes(b"bad clip")
    with pytest.raises(ValueError, match="preview verification"):
        service.preview(album, album.tracks[0], CTX)
    install = service.core.installs.current()
    source = service.core.packs.local_dir / "music/test/audio/morning-sun.ogg"
    source.write_bytes(b"bad full track")
    with pytest.raises(ValueError, match="[Pp]ack.*(verification|integrity)"):
        service.assign(album, album.tracks[0], "win", install, CTX)
    assert not install.path("sound").exists()


def test_assign_reassign_restore_all_roles_across_restarts(service):
    install = service.core.installs.current()
    original = _put(install, "deck/Field_notes.MP3")
    loose = _put(install, "Legacy.WAV")
    special = _put(install, "advantage/Edge.ogg")
    extra = _put(install, "duel/unrelated.ini")
    (album,) = service.catalog(CTX)
    first, second = album.tracks
    for role in ROLES:
        current = service.assign(album, first, role, install, CTX)
        assert current.assignments[role] == first.title
        assert install.path(f"sound/BGM/{role}/edopal-test-morning-sun.ogg").read_bytes() == b"sample full track 0"
    assert current.music_off and current.can_restore
    assert current.pools["duel"] == (first.title, "Legacy")
    assert set(current.pools["deck"]) == {first.title, "Field notes"}
    assert {"duel", "advantage", "deck"} <= {t.role for t in current.local.tracks}
    assert "unrelated" not in {t.title for t in current.local.tracks}

    current = service.assign(album, second, "menu", install, CTX)
    assert current.assignments["menu"] == second.title
    assert not install.path("sound/BGM/menu/edopal-test-morning-sun.ogg").exists()
    assert service.assign(album, second, "menu", install, CTX) == current

    # The registry is committed with the files, so a fresh app session can restore everything.
    fresh = MusicService(make_core(install.root, service.core.packs.local_dir, service.core.paths.data.parent))
    current = fresh.restore(install, CTX)
    assert not current.can_restore and not current.conflicts
    assert current.assignments["deck"] == "Field notes"
    assert not tuple(install.path("sound/BGM").rglob("edopal-*"))
    assert all(path.read_bytes() == b"original" for path in (original, loose, special, extra))
    assert "music_volume = 34" in install.path("config/system.conf").read_text()


def test_restore_and_reassign_preserve_external_edits(service):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    for role in ("menu", "deck", "win"):
        service.assign(album, album.tracks[0], role, install, CTX)
    menu = _put(install, "menu/edopal-test-morning-sun.ogg", b"user edit")
    deck = _put(install, "deck/edopal-test-morning-sun.ogg", b"user edit")
    with pytest.raises(FileExistsError, match="existing track"):
        service.assign(album, album.tracks[0], "menu", install, CTX)
    result = service.assign(album, album.tracks[1], "menu", install, CTX)
    assert result.conflicts == (menu.relative_to(install.root).as_posix(),)
    result = service.restore(install, CTX)
    assert result.conflicts == (deck.relative_to(install.root).as_posix(),)
    assert not result.can_restore
    assert menu.read_bytes() == deck.read_bytes() == b"user edit"
    assert not list(install.path("sound/BGM/win").iterdir())


def test_unowned_and_case_collisions_are_never_overwritten(service):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    exact = _put(install, "deck/edopal-test-morning-sun.ogg")
    different_case = _put(install, "win/EDOPAL-test-morning-sun.ogg")
    for role in ("deck", "win"):
        with pytest.raises(FileExistsError):
            service.assign(album, album.tracks[0], role, install, CTX)
    assert exact.read_bytes() == different_case.read_bytes() == b"original"
    assert not service.game_state().can_restore


def test_local_preview_assignment_same_role_and_stale_install(service, tmp_path):
    install = service.core.installs.current()
    local_path = _put(install, "Field.wav")
    source = service.game_state().local
    (track,) = source.tracks
    assert service.preview(source, track, CTX) == (local_path, False)
    assert not service.assign(source, track, "duel", install, CTX).can_restore
    state = service.assign(source, track, "lose", install, CTX)
    assert state.assignments["lose"] == "Field"
    assert next(install.path("sound/BGM/lose").iterdir()).read_bytes() == local_path.read_bytes()
    service.core.installs.select(tmp_path)
    with pytest.raises(ValueError, match="game folder changed"):
        service.preview(source, track, CTX)
    with pytest.raises(ValueError, match="EDOPro folder"):
        service.assign(source, track, "menu", install, CTX)


def test_selecting_an_original_in_role_removes_only_managed_copy(service):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    original = _put(install, "menu/Prelude.ogg")
    state = service.assign(album, album.tracks[0], "menu", install, CTX)
    track = next(track for track in state.local.tracks if track.title == "Prelude")
    assert service.assign(state.local, track, "menu", install, CTX).pools["menu"] == ("Prelude",)
    assert original.exists() and not service.game_state().can_restore


@pytest.mark.parametrize("action", ["assign", "restore", "enable_music"])
def test_running_and_unknown_gates(service, monkeypatch, action):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    operation = getattr(service, action)
    args = (album, album.tracks[0], "deck", install, CTX) if action == "assign" else (install, CTX)
    monkeypatch.setattr(service.core.installs, "run_state", lambda: RunState.RUNNING)
    with pytest.raises(GameRunningError, match="Close EDOPro"):
        operation(*args, confirmed_closed=True)
    monkeypatch.setattr(service.core.installs, "run_state", lambda: RunState.UNKNOWN)
    with pytest.raises(ConfirmClosedError):
        operation(*args)
    assert service.game_state().music_off and not install.path("sound").exists()
    state = operation(*args, confirmed_closed=True)
    assert state.music_off == (action != "enable_music")
    assert "music_volume = 34" in install.path("config/system.conf").read_text()


def test_concurrent_role_edits_preserve_registry(service):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    # Precache before workers mimic two app instances with separate service objects.
    service.core.packs.ensure(album.pack)

    def run(role):
        MusicService(service.core).assign(album, album.tracks[0], role, install, CTX)

    with ThreadPoolExecutor(5) as pool:
        list(pool.map(run, ROLES))
    state = service.game_state()
    assert all(title == "Morning Sun" for title in state.assignments.values())
    assert not service.restore(install, CTX).local.tracks


def test_linked_folder_and_manifest_traversal(service, tmp_path):
    install = service.core.installs.current()
    (install.path("sound/BGM")).mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    try:
        install.path("sound/BGM/deck").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    (album,) = service.catalog(CTX)
    with pytest.raises(ValueError, match="Linked music path"):
        service.assign(album, album.tracks[0], "deck", install, CTX)
    assert list(outside.iterdir()) == []
    folder = service.core.packs.local_dir / "music/test"
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["tracks"][0]["path"] = "../outside.ogg"
    (folder / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Invalid relative music path"):
        service.assign(album, album.tracks[0], "menu", install, CTX)


def test_cancel_and_no_install(service):
    ctx = JobContext()
    ctx._cancel.set()
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    with pytest.raises(CancelledError):
        service.assign(album, album.tracks[0], "win", install, ctx)
    assert service.core.packs.cached(album.id) is None
    no_game = MusicService(make_core(None, service.core.packs.local_dir, service.core.paths.data.parent))
    assert not no_game.game_state().can_restore
    assert not no_game.game_state().local.tracks


def test_offline_release_flow_with_production_services(service, monkeypatch):
    import io
    from zipfile import ZipFile

    from edopal.core.pack_client import CATALOG_URL
    from edopal.core.safe_storage import SafeStorage

    class Response(io.BytesIO):
        headers = {}

    root = service.core.packs.local_dir
    folder = root / "music/test"
    archive = io.BytesIO()
    with ZipFile(archive, "w") as z:
        for path in folder.rglob("*"):
            if path.is_file():
                z.write(path, path.relative_to(folder))
    data = archive.getvalue()
    (row,) = json.loads((root / "catalog.json").read_text())["packs"]
    row.update(
        url="https://example.test/pack.zip",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        preview="https://example.test/preview.ogg",
        thumbnail="https://example.test/cover.webp",
    )
    assets = {
        CATALOG_URL: json.dumps({"schema": 1, "packs": [row]}).encode(),
        row["url"]: data,
        row["preview"]: b"preview",
        row["thumbnail"]: b"thumbnail",
    }
    downloads = []

    def fetch(url, headers=None):
        downloads.append(url)
        return Response(assets[url])

    core = service.core
    core.packs = GitHubPacks(core.paths)
    core.storage = SafeStorage(core.paths, core.installs.run_state)
    monkeypatch.setattr(core.packs, "_fetch", fetch)
    service = MusicService(core)
    (album,) = service.catalog(CTX)
    assert service.preview(album, album.tracks[0], CTX)[0].read_bytes() == b"preview"
    assert row["url"] not in downloads
    install = core.installs.current()
    service.assign(album, album.tracks[1], "menu", install, CTX)
    assert downloads.count(row["url"]) == 1

    monkeypatch.setattr(core.packs, "_fetch", lambda *args: pytest.fail("unexpected network access"))
    offline = MusicService(core)
    (album,) = offline.catalog(CTX)
    assert album.cached
    assert offline.preview(album, album.tracks[0], CTX)[0].read_bytes() == b"sample full track 0"
    assert not offline.enable_music(install, CTX).music_off
    assert core.storage.last_receipt(install, "music-settings").changed == ("config/system.conf",)
    assert not offline.restore(install, CTX).can_restore
    assert b"enable_music = 1 # off" in install.path("config/system.conf").read_bytes()


def test_local_platform_filenames(service):
    import os

    if os.name == "nt":
        pytest.skip("Colon is not a valid filename on Windows")
    install = service.core.installs.current()
    original = _put(install, "deck/Intro: côté A.ogg")
    album = service.game_state().local
    (track,) = album.tracks
    assert service.preview(album, track, CTX) == (original, False)
    assert service.assign(album, track, "menu", install, CTX).assignments["menu"] == "Intro: côté A"


def test_restore_one_role(service):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    for role in ("deck", "duel"):
        state = service.assign(album, album.tracks[0], role, install, CTX)
    assert set(state.managed_roles) == {"deck", "duel"}
    state = service.restore(install, CTX, role="deck")
    assert state.managed_roles == ("duel",) and state.can_restore
    assert state.pools["deck"] == () and state.pools["duel"] == ("Morning Sun",)
    assert not service.restore(install, CTX, role="duel").can_restore


def test_preserve_unrelated_file_at_assignment_registry_path(service):
    (album,) = service.catalog(CTX)
    install = service.core.installs.current()
    path = install.path("sound/.edopal-music.json")
    path.parent.mkdir()
    payload = b'{"schema": 1, "roles": {}}'
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="assignments are unreadable"):
        service.assign(album, album.tracks[0], "deck", install, CTX)
    assert path.read_bytes() == payload
    assert not install.path("sound/BGM/deck").exists()


def test_local_checkout_rejects_escape_before_ensure(service):
    root = service.core.packs.local_dir
    catalog = root / "catalog.json"
    data = json.loads(catalog.read_text())
    data["packs"][0]["url"] = "../external"
    catalog.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsafe pack path"):
        service.catalog(CTX)
    assert not service.core.installs.current().path("sound").exists()
