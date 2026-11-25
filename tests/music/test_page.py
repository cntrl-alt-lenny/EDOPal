import hashlib
import json
import shutil
import wave
from pathlib import Path

import miniaudio
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt

from edopal.dev.preview import make_core
from edopal.features.music.page import MusicPage
from edopal.features.music.player import PreviewPlayer


def _catalog(root, audio):
    rows = []
    for index in range(12):
        playable = index < 2
        name = ("Sample Day", "Sample Night")[index] if playable else f"Game {index:02d}"
        slug = name.lower().replace(" ", "-")
        folder = root / "music" / slug
        folder.mkdir(parents=True)
        Image.new("RGB", (40, 50), (60, 80, 100 + index)).save(folder / "cover.webp")
        tracks = []
        if playable:
            for number, title in enumerate(("Opening", "Overture"), start=1):
                filename = f"track-{number}.wav"
                shutil.copyfile(audio, folder / filename)
                tracks.append({"id": f"track-{number}", "title": title, "path": filename, "duration_seconds": 8})
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
        common = {"id": f"music-{slug}", "kind": "music", "name": name, "version": "1.0.0"}
        meta = {"game": name, "year": 2000 + index, "platform": "Test console", "tracks": tracks}
        manifest = {**common, **meta, "schema": 1, "cover": "cover.webp", "files": hashes, "credits": []}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rows.append(
            {
                **common,
                "url": f"music/{slug}",
                "thumbnail": f"music/{slug}/cover.webp",
                "preview": f"music/{slug}/track-1.wav" if playable else "",
                "meta": {**meta, "track_count": len(tracks)},
                "size": sum(p.stat().st_size for p in folder.iterdir()),
                "sha256": "",
            }
        )
    (root / "catalog.json").write_text(json.dumps({"schema": 1, "packs": rows}), encoding="utf-8")


@pytest.fixture
def library(tmp_path):
    game = tmp_path / "game"
    shutil.copytree(Path(__file__).resolve().parents[1] / "fixtures/game", game)
    deck = game / "sound/BGM/deck"
    deck.mkdir(exist_ok=True, parents=True)
    audio = deck / "Installed Theme.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", ""))
        wav.writeframes(b"\x00\x02" * 64_000)
    packs = tmp_path / "packs"
    _catalog(packs, audio)
    return make_core(game, packs, tmp_path / "home")


@pytest.fixture
def page(qtbot, library):
    widget = MusicPage(library, player=PreviewPlayer(backends=[miniaudio.Backend.NULL]))
    qtbot.addWidget(widget)
    widget.resize(1280, 800)
    widget.show()
    qtbot.waitUntil(lambda: len(_tiles(widget)) == 8)
    yield widget
    widget.hide()


def _tiles(page):
    return [tile for tile in page.tiles if tile.isVisible() and tile.album is not None]


def test_existing_track_pool_uses_readable_names(page, library):
    original = library.installs.current().path("sound/BGM/deck", "Installed Theme.wav")
    spirit = original.rename(original.with_name("spirit-caller-deck-construction.wav"))
    shutil.copyfile(spirit, spirit.with_name("world-championship-2011.wav"))
    page.refresh_game()
    role = page.roles["deck"]
    assert role.assignment == "2 local tracks"
    assert "Spirit Caller Deck Construction" in role.toolTip()
    assert "World Championship 2011" in role.accessibleDescription()
    page.local_tab.click()
    page.search.setText("Spirit Caller")
    assert page.track_rows[0].title == "Spirit Caller Deck Construction"
    assert page.track.title == "spirit-caller-deck-construction"


def _sample(page):
    return next(tile for tile in _tiles(page) if tile.album.track_count)


def _check_layout(page, columns):
    tiles = _tiles(page)
    origins = [tile.mapTo(page, QPoint()) for tile in tiles]
    assert len({p.x() for p in origins}) == columns
    assert len({p.y() for p in origins}) == 2
    for item in [*tiles, *page.roles.values()]:
        assert item.isVisible()
        assert page.rect().contains(QRect(item.mapTo(page, QPoint()), item.size()))


def test_idle_grid_paging_search_and_resize(page, qtbot):
    assert not page.player.playing
    assert page.columns == 4
    _check_layout(page, 4)
    first_ids = {tile.album.id for tile in _tiles(page)}
    qtbot.mouseClick(page.next_page, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(_tiles(page)) == 4)
    assert first_ids.isdisjoint(tile.album.id for tile in _tiles(page))
    qtbot.mouseClick(page.prev_page, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: {tile.album.id for tile in _tiles(page)} == first_ids)

    page.resize(1920, 1080)
    qtbot.waitUntil(lambda: page.columns == 6 and len(_tiles(page)) == 12)
    _check_layout(page, 6)
    page.search.setText("Sample Night")
    qtbot.waitUntil(lambda: len(_tiles(page)) == 1)
    assert _tiles(page)[0].album.name == "Sample Night"
    page.search.setText("unlisted name")
    qtbot.waitUntil(lambda: not _tiles(page))
    page.search.clear()
    qtbot.waitUntil(lambda: len(_tiles(page)) == 12)
    stub = next(tile for tile in _tiles(page) if not tile.album.track_count)
    stub.setFocus()
    qtbot.keyClick(stub, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.album is not None and page.album.id == stub.album.id)
    assert not page.player.playing and all(not button.isEnabled() for button in page.roles.values())


def test_preview_pause_resume_and_hide(page, qtbot, library):
    game = library.installs.current().root
    sample = _sample(page)
    qtbot.mouseClick(sample, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.player.playing)
    assert page.track.title == "Opening"
    assert library.packs.cached(sample.album.id) is None
    assert not list(game.rglob("edopal-*.wav"))
    qtbot.mouseClick(page.play_button, Qt.MouseButton.LeftButton)
    assert page.player.paused and not page.player.playing
    qtbot.mouseClick(page.play_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.player.playing)
    qtbot.mouseClick(page.mute_button, Qt.MouseButton.LeftButton)
    assert page.player.muted
    qtbot.mouseClick(page.track_rows[1], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.track.title == "Overture" and page.player.playing)
    assert library.packs.cached(sample.album.id)
    assert not list(game.rglob("edopal-*.wav"))
    page.hide()
    assert not page.player.playing and not page.player.paused
    page.show()
    assert not page.player.playing


def test_preview_finishing_after_page_hides_is_discarded(page, qtbot, library, monkeypatch):
    pending = []
    run = library.jobs.run

    def hold_preview(fn, *, key, on_done, **options):
        if key == "music.preview":
            return run(fn, key=key, on_done=lambda value: pending.append((on_done, value)), **options)
        return run(fn, key=key, on_done=on_done, **options)

    monkeypatch.setattr(library.jobs, "run", hold_preview)
    qtbot.mouseClick(_sample(page), Qt.MouseButton.LeftButton)
    assert len(pending) == 1
    done, prepared = pending.pop()
    page.hide()
    page.show()
    done(prepared)
    assert prepared[0].closed and not page.player.playing


def test_enable_assign_view_local_and_restore(page, qtbot, library):
    game = library.installs.current().root
    config = game / "config/system.conf"
    original = game / "sound/BGM/deck/Installed Theme.wav"
    original_bytes = original.read_bytes()
    assert page.enable_button.isVisible()
    qtbot.mouseClick(page.enable_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "enable_music = 1" in config.read_text(encoding="utf-8"))
    assert not page.enable_button.isVisible()
    sample = _sample(page)
    qtbot.mouseClick(sample, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.player.playing)
    title = page.track.title
    for role in ("menu", "win"):
        qtbot.mouseClick(page.roles[role], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda key=role: title in page.roles[key].accessibleName())
        managed = list((game / "sound/BGM" / role).glob("edopal-*.wav"))
        assert len(managed) == 1 and managed[0].read_bytes() == original_bytes
    assert library.packs.cached(sample.album.id)
    qtbot.mouseClick(page.local_tab, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: any(tile.album.pack is None for tile in _tiles(page)))
    deck = next(tile for tile in _tiles(page) if any(track.title == "Installed Theme" for track in tile.album.tracks))
    qtbot.mouseClick(deck, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.track is not None and page.track.title == "Installed Theme" and page.player.playing)
    assert page.restore_button.isEnabled()
    qtbot.mouseClick(page.restore_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not list(game.rglob("edopal-*.wav")))
    assert original.read_bytes() == original_bytes
    assert "enable_music = 1" in config.read_text(encoding="utf-8")
    assert title not in page.roles["menu"].accessibleName()
