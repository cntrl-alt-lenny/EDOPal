import json
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from edopal.dev.preview import make_core
from edopal.features.sounds.releases import GAMES
from edopal.features.sounds.service import stock_previews
from edopal.ui import theme
from edopal.ui.sounds_page import SoundsPage


def test_catalogue_order_and_sources():
    assert len({game.slug for game in GAMES}) == len(GAMES) == 62
    assert GAMES == tuple(sorted(GAMES, key=lambda game: (game.first_release, game.name.casefold())))
    for game in GAMES:
        first = date.fromisoformat(game.first_release)
        assert game.source.startswith("https://")
        assert set(game.dates) <= {"JP", "NA", "EU"}
        for day, source in game.dates.values():
            assert first <= date.fromisoformat(day)
            assert (source or game.source).startswith("https://")


def test_sound_picker_displays_regional_dates_sorts_and_filters(qapp, qtbot, tmp_path):
    theme.apply(qapp)
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    page = SoundsPage(core)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()

    assert page.state.text() == "Not Yet Available"
    assert not page.download.isEnabled()
    assert {region: cell.text() for region, cell in page.date_labels.items()} == {
        "JP": "30 Nov 2006",
        "NA": "2 Jan 2007",
        "EU": "23 Mar 2007",
    }
    assert "GameFAQs" in page.source_links.text()
    assert all(tile.cover is None for tile in page.ambient.values())
    page.game_tile.widget.click()
    picker = page.picker
    assert picker.count.text() == "62 Games"
    selected = picker.list.currentItem()
    assert selected.data(Qt.ItemDataRole.UserRole) == "gx-spirit-caller"
    row = picker.list.itemWidget(selected)
    dates = {column: row.findChild(QLabel, "soundGame" + column).text() for column in ("Release", "JP", "NA", "EU")}
    assert dates == {"Release": "30 Nov 2006", "JP": "30 Nov 2006", "NA": "2 Jan 2007", "EU": "23 Mar 2007"}
    assert row.findChild(QLabel, "soundGameStatus").text() == "Not Yet Available"
    assert picker.list.item(0).data(Qt.ItemDataRole.UserRole) == GAMES[0].slug
    picker.release.click()
    assert picker.list.item(0).data(Qt.ItemDataRole.UserRole) == GAMES[-1].slug
    assert picker.list.itemWidget(selected) is row
    picker.query.setText("breed")
    assert picker.count.text() == "1 Game"
    picker.release.click()
    item = picker.list.item(0)
    assert not item.isHidden()
    oldest = picker.list.itemWidget(item)
    assert oldest.findChild(QLabel, "soundGameNA").text() == "—"
    assert oldest.findChild(QLabel, "soundGameEU").text() == "—"
    QTest.mouseClick(picker.list.viewport(), Qt.MouseButton.LeftButton, pos=picker.list.visualItemRect(item).center())

    assert page.picker is None
    assert page.game.slug == "monster-capsule-breed-and-battle"
    assert page.date_labels["JP"].text() == "23 Jul 1998"
    assert page.date_labels["NA"].text() == page.date_labels["EU"].text() == "—"
    assert page.state.text() == "Not Yet Available"


def test_pack_art_arriving_while_picker_is_open(qtbot, tmp_path):
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    page = SoundsPage(core)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    page.game_tile.widget.click()
    game = page.game.slug
    (tmp_path / "banners").mkdir()
    (tmp_path / "logos").mkdir()
    path = tmp_path / "banners" / f"{game}.png"
    background = QImage(160, 50, QImage.Format.Format_RGB32)
    background.fill(QColor("#123456"))
    assert background.save(str(path))
    logo = QImage(40, 20, QImage.Format.Format_RGB32)
    logo.fill(QColor("#ebc987"))
    assert logo.save(str(tmp_path / "logos" / f"{game}.png"))
    page._banners_ready({"music-" + game: path})

    for widget in (page.details_art, page.picker.covers[game]):
        rendered = widget.grab().toImage()
        mid = rendered.height() // 2
        assert rendered.pixelColor(rendered.width() // 8, mid).name() == "#ebc987"
        assert rendered.pixelColor(rendered.width() * 3 // 4, mid).name() == "#123456"
    page.picker.reject()
    page._select_ambient("rain-room")
    assert page.details_art.isHidden()
    assert page.details_art.logo.isNull()


def test_sound_download_progress_failure_retry_and_cache(qtbot, monkeypatch, tmp_path):
    root = tmp_path / "packs"
    slug = "gx-spirit-caller"
    pack_id = f"sound-{slug}"
    source = root / "sound" / slug
    source.mkdir(parents=True)
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "draw.wav").write_bytes(b"pack payload")
    entry = {
        "id": pack_id,
        "kind": "sound",
        "name": "Spirit Caller",
        "version": "1.0.0",
        "url": source.relative_to(root).as_posix(),
        "size": 100,
        "sha256": "",
    }
    (root / "catalog.json").write_text(json.dumps({"packs": [entry]}), encoding="utf-8")
    core = make_core(None, root, tmp_path / "home")
    page = SoundsPage(core)
    qtbot.addWidget(page)
    assert page.state.text() == "Not Downloaded"
    assert page.download.isEnabled()
    ensure = core.packs.ensure
    observations = []

    def transfer(pack, progress, cancel):
        assert pack.kind == "sound"
        progress(40, 100)
        observations.append((page.state.text(), page.progress.value(), not page.progress.isHidden()))
        if len(observations) == 1:
            raise OSError("download interrupted")
        return ensure(pack, progress, cancel)

    monkeypatch.setattr(core.packs, "ensure", transfer)
    page.download.click()
    assert page.state.text() == "Download Failed"
    assert page.progress.isHidden() and page.download.isEnabled()
    page.download.click()
    assert observations == [("Downloading…", 40, True)] * 2
    assert page.state.text() == "Downloaded"
    assert page.progress.isHidden() and not page.download.isEnabled()
    assert (core.packs.cached(pack_id) / "draw.wav").read_bytes() == b"pack payload"
    reopened = SoundsPage(core)
    qtbot.addWidget(reopened)
    assert reopened.state.text() == "Downloaded"

    # A cached package discovered through the picker still counts if the catalog is offline.
    other = "monster-capsule-breed-and-battle"
    offline_cache = core.packs.cache / "packs" / f"sound-{other}"
    offline_cache.mkdir(parents=True)
    (offline_cache / "manifest.json").write_text("{}", encoding="utf-8")
    reopened.resize(1280, 800)
    reopened.show()
    reopened.game_tile.widget.click()
    assert reopened.picker.statuses[slug].text() == "Downloaded"
    assert reopened.picker.statuses[other].text() == "Downloaded"
    assert reopened.picker.statuses["duel-monsters"].text() == "Not Yet Available"
    reopened.picker.reject()


def test_stock_previews_use_the_installed_slot_files(tmp_path):
    sound = tmp_path / "sound"
    sound.mkdir()
    (sound / "summon").mkdir()
    (sound / "summon.wav").touch()
    alternate = sound / "summon" / "alternate.ogg"
    alternate.touch()
    draw = sound / "draw.wav"
    draw.touch()

    assert stock_previews(None) == {}
    assert stock_previews(tmp_path) == {"summon.wav": alternate, "draw.wav": draw}
