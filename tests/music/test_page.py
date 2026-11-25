import hashlib
import json
import shutil
import wave
from pathlib import Path

import miniaudio
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from edopal.core.settings import Settings
from edopal.dev.preview import make_core
from edopal.features.music.library import ALL, FAVORITES
from edopal.features.music.page import MusicPage
from edopal.features.music.player import PreviewPlayer


def _catalog(root, audio):
    rows = []
    for index in range(30):
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
        meta = {
            "game": "Yu-Gi-Oh! " + name,
            "series": "GX" if index % 2 else "Duel Monsters",
            "year": 2000 + index,
            "platform": "Test console",
            "tracks": tracks,
        }
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
    qtbot.waitUntil(lambda: len(_tiles(widget)) == 30)
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
    assert not _tiles(page)
    page.search.clear()
    assert page.track_rows[0].title == "Spirit Caller Deck Construction"
    assert page.track.title == "spirit-caller-deck-construction"


def _sample(page):
    return next(tile for tile in _tiles(page) if tile.album.track_count)


def test_carousel_drag_wheel_tabs_search_and_resize(page, qtbot):
    assert not page.player.playing
    tiles = _tiles(page)
    assert len({tile.y() for tile in tiles}) == 2
    assert len({tile.x() for tile in tiles}) == 15
    for role in page.roles.values():
        assert page.rect().contains(QRect(role.mapTo(page, QPoint()), role.size()))
    bar = page.carousel.horizontalScrollBar()
    assert bar.maximum() > 0
    tile = tiles[2]
    wheel = QWheelEvent(
        QPointF(20, 20),
        QPointF(tile.mapToGlobal(QPoint(20, 20))),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ShiftModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(tile, wheel)
    assert bar.value() == 120
    bar.setValue(0)
    qtbot.mousePress(tile, Qt.MouseButton.LeftButton, pos=QPoint(55, 35))
    qtbot.mouseMove(tile, QPoint(15, 35))
    qtbot.mouseRelease(tile, Qt.MouseButton.LeftButton, pos=QPoint(15, 35))
    assert bar.value() > 0 and not page.player.playing
    page.resize(1920, 1080)
    qtbot.waitUntil(lambda: page.wide)
    assert _tiles(page)[0].height() > 150
    page.tabs["GX"].click()
    assert len(_tiles(page)) == 15 and all(t.album.series == "GX" for t in _tiles(page))
    page.search.setText("sAmPle nIGHt")
    assert len(_tiles(page)) == 1 and _tiles(page)[0].album.name == "Sample Night"
    for query in ("Opening", "Test console", "2001"):
        page.search.setText(query)
        assert not _tiles(page)
    page.tabs[ALL].click()
    stub = next(tile for tile in _tiles(page) if not tile.album.track_count)
    qtbot.keyClick(stub, Qt.Key.Key_Return)
    assert page.track_hint.text() == "Audio coming soon"
    assert not page.transport.isVisible() and all(not button.isEnabled() for button in page.roles.values())


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


def test_pins_full_game_search_scene_and_reopen(page, qtbot, library):
    page.stars[0].click()
    assert page.stars[0].text() == "★"
    page.tabs[FAVORITES].click()
    assert page.summary.text() == "1 Track"
    labels = [label.text() for label in page.fav_host.findChildren(QLabel)]
    assert "Yu-Gi-Oh! Sample Day" in labels
    page.search.setText("Opening")
    assert not page.fav_plays
    page.search.setText("Sample Day")
    next(iter(page.fav_plays.values())).click()
    qtbot.waitUntil(lambda: page.player.playing)
    page.hide()
    library.settings = Settings(library.settings.path)
    fresh = MusicPage(library)
    qtbot.addWidget(fresh)
    fresh.show()
    fresh.tabs[FAVORITES].click()
    assert len(fresh.fav_plays) == 1
    row = fresh.fav_list.itemAt(0).widget()
    from PySide6.QtWidgets import QPushButton

    buttons = row.findChildren(QPushButton)
    assign = next(b for b in buttons if b.menu())
    assign.menu().actions()[1].trigger()
    assert fresh.game.assignments["deck"] == "Opening"
    assert fresh.roles["deck"].game == "Sample Day" and fresh.roles["deck"].year == "2000"
    star = next(b for b in fresh.fav_list.itemAt(0).widget().findChildren(QPushButton) if b.text() == "★")
    star.click()
    assert not fresh.fav_plays


def test_setups_apply_autosave_rename_delete_and_restart(page, qtbot, library, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    install = library.installs.current()
    page.roles["menu"].click()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Retro", True))
    page._setup_actions()
    next(a for a in page.setup_menu.actions() if a.text().startswith("Save Current")).trigger()
    assert page.library.setups(install)["active"] == "Retro"
    page.track_rows[1].click()
    page.roles["menu"].click()
    page.roles["win"].click()
    page._switch_setup("Default")
    assert page.game.assignments["menu"] == "Opening" and not page.game.pools["win"]
    library.settings = Settings(library.settings.path)
    fresh = MusicPage(library)
    qtbot.addWidget(fresh)
    fresh.show()
    fresh._switch_setup("Retro")
    assert fresh.game.assignments["win"] == fresh.game.assignments["menu"] == "Overture"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Night", True))
    fresh._rename_setup()
    assert set(fresh.library.setups(install)["choices"]) == {"Default", "Night"}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    fresh._delete_setup()
    assert set(fresh.library.setups(install)["choices"]) == {"Default"}
    assert fresh.game.assignments["menu"] == "Opening"


def test_installed_soundtrack_matches_cover_and_can_be_assigned(page, library, qtbot):
    install = library.installs.current()
    source = install.path("sound/BGM/deck/Installed Theme.wav")
    source.rename(source.with_name("game-02-deck-construction.wav"))
    page.refresh_game()
    page.search.setText("Game 02")
    _tiles(page)[0].click()
    qtbot.waitUntil(lambda: page.player.playing)
    assert page.track.title == "Deck Construction"
    page.roles["menu"].click()
    assert page.game.assignments["menu"] == "Deck Construction"
    assert page.roles["menu"].game == "Game 02"
    assert not library.packs.cached(page.album.id)


def test_existing_track_in_native_pool_is_remembered_and_restored(page, library, monkeypatch):
    install = library.installs.current()
    page.local_tab.click()
    page.roles["deck"].click()
    saved = page.library.setups(install)
    assert saved["choices"]["Default"]["deck"] == [page.album.id, page.track.id]
    assert not list(install.root.rglob("edopal-*.wav"))
    assert page.roles["deck"].active and page.restore_button.isVisible()
    monkeypatch.setattr(page, "_request_name", lambda *args: "Defaults Copy")
    page._new_setup()
    page._restore("deck")
    assert not page.roles["deck"].active
    page._switch_setup("Default")
    assert page.roles["deck"].active
    assert not list(install.root.rglob("edopal-*.wav"))
