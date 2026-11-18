"""The Sounds page through the real Studio shell and synthetic installations."""

import asyncio
import math
import threading
import wave
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog
from pytestqt.qtbot import QtBot

import sounds
from content.catalogue import Catalogue, CatalogueSource, PackState, PackStatus
from content.download import DownloadError, DownloadFailure, Progress
from content.manifest import Manifest, Pack
from studio.context import Installation
from studio.pages import sounds_collections, sounds_model
from studio.pages.sounds import SoundsPage
from studio.shell import StudioWindow
from studio.widgets import Button


def audio_file(path: Path, pitch: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(
            b"".join(
                int(900 * math.sin(index * pitch * math.tau / 8000)).to_bytes(
                    2, "little", signed=True
                )
                for index in range(800)
            )
        )
    return path


class Player:
    def __init__(self) -> None:
        self.calls = []
        self.active = None
        self.stops = 0

    def preview(self, sound_set: sounds.SoundSet, key: str) -> Future:
        self.calls.append((key, sound_set.assignments[key]))
        assert self.active is None
        self.active = Future()
        return self.active

    def sampler(self, sound_set: sounds.SoundSet) -> Future:
        self.calls.append(("sampler", sound_set.name))
        assert self.active is None
        self.active = Future()
        return self.active

    def stop(self) -> None:
        self.stops += 1
        self.active = None

    def close(self) -> None:
        self.stop()


def click_text(parent, text: str) -> None:
    button = next(b for b in parent.findChildren(Button) if b.text() == text and b.isVisible())
    button.click()


def idle(qtbot, page: SoundsPage) -> None:
    qtbot.waitUntil(lambda: not page.ctx.tasks.jobs, timeout=6000)


@pytest.fixture
def sound_window(make_window, game, qtbot, monkeypatch):
    root = Path(game.edopro_path)
    audio_file(root / "sound/draw.wav")
    audio_file(root / "sound/summon.wav", 220)
    settings = root / "config/system.conf"
    settings.parent.mkdir()
    settings.write_bytes(b"enable_sound = 0\nsound_volume = 0\nsound_backend = miniaudio\n")
    monkeypatch.setattr(sounds, "AuditionPlayer", Player)
    monkeypatch.setattr("music_library._game_running", lambda _: False)
    window = make_window()
    window.nav["sounds"].click()
    page = window.current
    idle(qtbot, page)
    return window, page, root


def test_select_mix_audition_and_use_keyboard(sound_window, qtbot):
    window, page, root = sound_window
    assert page.overview.installed.text() == "Installed mix"
    assert "Effects off" in page.overview.settings.text()
    assert "Raise the sound effects volume" in page.overview.volume_warning.text()
    assert not page.bar.apply.isEnabled()
    card = page.cards["builtin:Thonk"]
    card.play.click()
    assert card.play.text() == "Stop sampler"
    page.cards["builtin:Synth"].play.click()
    assert card.play.text() == "Play sampler"
    assert page.audio.player.calls == [("sampler", "Thonk"), ("sampler", "Synth")]
    page.overview.mix.click()
    assert page.audio.player.active is None
    assert len(page.mixer.rows) == 24
    first = page.mixer.rows["summon"]
    first.play.setFocus()
    qtbot.keyClick(first.play, Qt.Key.Key_Space)
    assert page.audio.player.calls[-1][0] == "summon"
    qtbot.keyClick(first.play, Qt.Key.Key_Down)
    second = page.mixer.rows["specialsummon"]
    assert QApplication.focusWidget() is second.edit
    qtbot.keyClick(second.edit, Qt.Key.Key_Return)
    assert page.state == "event"
    page.picker.silence.click()
    assert second.choice.text() == "Silent"
    assert second.duration.text() == "—"
    assert page.draft.assignments == {"specialsummon": None}
    assert page.audio.player.active is None
    assert not (root / "sound/specialsummon.wav").exists()
    assert (root / "sound/summon.wav").is_file()
    qtbot.keyClick(second.edit, Qt.Key.Key_Escape)
    assert page.state == "overview"
    page.cards["builtin:Thonk"].play.click()
    assert window.navigate("music")
    assert page.audio.player.active is None


def test_installation_states_and_stale_scan(sound_window, qtbot, monkeypatch, tmp_path):
    window, page, root = sound_window
    begin, release = threading.Event(), threading.Event()
    original = sounds.scan_installation

    def read(game: Path):
        if game == root:
            begin.set()
            assert release.wait(5)
        return original(game)

    monkeypatch.setattr(sounds, "scan_installation", read)
    page.refresh()
    try:
        qtbot.waitUntil(begin.is_set)
        page.ctx.set_installation(Installation())
        assert page.overview.installed.toolTip() == "Choose an installation"
        assert page.overview.choose.isVisible()
        page.ctx.set_installation(Installation(str(root), False))
        assert "Finish setting up" in page.overview.installed.toolTip()
        assert not page.bar.apply.isEnabled()
        missing = tmp_path / "unavailable"
        page.ctx.set_installation(Installation(str(missing), True))
        qtbot.waitUntil(lambda: page.scan_error)
        assert page.overview.rescan.isVisible()
        assert "Could not read" in page.feedback.copy.text()
    finally:
        release.set()
        idle(qtbot, page)
    assert page.snapshot is None
    assert window.current is page
    missing.mkdir()
    page.overview.rescan.click()
    idle(qtbot, page)
    assert page.overview.installed.toolTip() == "No sounds installed"


def test_overview_reflows_from_compact_to_wide(sound_window, qtbot):
    window, page, _ = sound_window
    window.resize(960, 600)
    qtbot.waitUntil(lambda: page.overview.compact_tools)
    assert page.views["overview"].horizontalScrollBar().maximum() == 0
    window.resize(3840, 2160)
    qtbot.waitUntil(lambda: page.bar.parentWidget() is page.overview)
    assert page.bar.isVisible()
    page.overview.mix.click()
    assert page.bar.parentWidget() is page
    assert page.bar.isVisible()


def test_apply_retry_busy_drift_undo_and_restore(sound_window, qtbot, monkeypatch):
    window, page, root = sound_window
    draw = root / "sound/draw.wav"
    original = draw.read_bytes()
    page.overview.mix.click()
    page.mixer.rows["draw"].edit.click()
    click_text(page.picker, "Use sound")  # Current game is the first source.
    page.mixer.rows["draw"].edit.click()
    available = [
        b
        for b in page.picker.findChildren(Button)
        if b.isVisible() and b.accessibleName() == "Use sound from Thonk"
    ]
    available[0].click()
    draft = page.draft.assignments["draw"].data

    monkeypatch.setattr("music.settings._game_running", lambda _: True)
    page.bar.apply.click()
    idle(qtbot, page)
    assert page.closed.retry.isVisible()
    assert "EDOPro is open" in page.closed.detail.text()
    assert draw.read_bytes() == original
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    started, release = threading.Event(), threading.Event()
    real_apply = sounds.apply_set

    def blocking_apply(*args):
        started.set()
        assert release.wait(5)
        real_apply(*args)

    monkeypatch.setattr(sounds, "apply_set", blocking_apply)
    page.closed.retry.click()
    try:
        qtbot.waitUntil(started.is_set)
        assert not window.change.isEnabled()
        assert not window.navigate("music")
        assert not page.bar.apply.isEnabled()
        assert "switch folders" in page.feedback.copy.text()
    finally:
        release.set()
        idle(qtbot, page)
    assert draw.read_bytes() == draft
    assert page.bar.undo.isVisible() and page.bar.restore.isVisible()
    assert window.change.isEnabled()

    audio_file(draw, 660)
    changed = draw.read_bytes()
    page.bar.undo.click()
    idle(qtbot, page)
    assert "changed outside" in page.feedback.copy.text()
    assert draw.read_bytes() == changed
    draw.write_bytes(draft)
    page.bar.undo.click()
    idle(qtbot, page)
    assert draw.read_bytes() == original

    page.bar.apply.click()
    idle(qtbot, page)
    monkeypatch.setattr(window, "confirm", lambda *_: True)
    page.bar.restore.click()
    idle(qtbot, page)
    assert draw.read_bytes() == original
    assert "Original sounds restored" in page.feedback.copy.text()
    assert page.bar.undo.isVisible()


def test_first_apply_checks_new_external_files(sound_window, qtbot):
    _, page, root = sound_window
    page.cards["builtin:Thonk"].use.click()
    path = audio_file(root / "sound/attack.wav")
    prior = path.read_bytes()
    page.bar.apply.click()
    idle(qtbot, page)
    assert "changed outside the app" in page.feedback.copy.text()
    assert path.read_bytes() == prior
    assert not page.snapshot.undo


def test_import_clip_and_chant_then_enable_effects(sound_window, qtbot, monkeypatch, tmp_path):
    _, page, root = sound_window
    page.overview.toggle.click()
    idle(qtbot, page)
    data = (root / "config/system.conf").read_bytes()
    assert data == b"enable_sound = 1\nsound_volume = 0\nsound_backend = miniaudio\n"
    assert page.overview.toggle.text() == "Turn off effects"
    page.overview.chants.click()
    assert page.chants.status.text() == "No card chants are installed."
    page.chants.forms["summon"].click()
    assert "number printed" in page.feedback.copy.text()
    page.chants.number.setText("0012345")
    page.chants.forms["summon"].click()
    assert page.state == "event"
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"bad audio")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(bad), ""))
    page.picker.import_clip.click()
    idle(qtbot, page)
    assert page.state == "event"
    assert "Could not import" in page.feedback.copy.text()
    source = audio_file(tmp_path / "Summon voice.wav")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(source), ""))
    page.picker.import_clip.click()
    idle(qtbot, page)
    assert page.state == "chants"
    assert page.chants.rows["chant/summon/12345"].choice.toolTip() == "Summon voice.wav"
    assert not (root / "sound/summon/12345.wav").exists()
    assert not page.audio.player.calls
    page.bar.apply.click()
    idle(qtbot, page)
    assert (root / "sound/summon/12345.wav").read_bytes() == source.read_bytes()
    assert page.chants.status.text() == "1 installed."


def test_save_rename_duplicate_export_import_and_delete(sound_window, qtbot, monkeypatch, tmp_path):
    window, page, _ = sound_window
    page.cards["builtin:Synth"].use.click()
    page.bar.save.click()
    names = page.collections.name
    names.submit.click()
    assert "Enter a name" in page.feedback.copy.text()
    names.name.setText("Weekend")
    qtbot.keyClick(names.name, Qt.Key.Key_Return)
    idle(qtbot, page)
    library = page.collections.library
    assert page.state == "saved"
    click_text(library, "Rename")
    names.name.setText("My sounds")
    names.submit.click()
    idle(qtbot, page)
    assert page.collections.saved[0].sound_set.name == "My sounds"
    click_text(library, "Duplicate")
    names.submit.click()
    idle(qtbot, page)
    assert {r.sound_set.name for r in page.collections.saved} == {"My sounds", "My sounds copy"}
    path = tmp_path / "Share"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
    click_text(library, "Export")
    idle(qtbot, page)
    exported = path.with_suffix(".edosounds")
    assert sounds.read_set(exported).assignments
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(exported), ""))
    library.import_button.click()
    idle(qtbot, page)
    assert len(page.collections.saved) == 3
    bad = tmp_path / "bad.edosounds"
    bad.write_bytes(b"not a valid set")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(bad), ""))
    library.import_button.click()
    idle(qtbot, page)
    assert "fresh export" in page.feedback.copy.text()
    monkeypatch.setattr(window, "confirm", lambda *_: True)
    click_text(library, "Delete")
    idle(qtbot, page)
    assert len(page.collections.saved) == 2


def test_offline_catalogue_download_cancel_retry_and_use(sound_window, qtbot, monkeypatch):
    _, page, _ = sound_window
    pack = Pack(
        "night",
        "sound-effects",
        "Night duel",
        "A sampler for late duels.",
        "Creator",
        "CC0",
        "Creator Demo",
        "https://example.test",
        "1.0.0",
        "https://example.test/sounds",
        800,
        "a" * 64,
        (),
        "5.5.0",
    )
    online = Catalogue(Manifest(1, (pack,)), CatalogueSource.LIVE)
    available = Catalogue(Manifest(1, ()), CatalogueSource.BUNDLED, "Offline")

    async def catalogue(*_, **__):
        return available

    async def transfer(_cache, packet, _version, report, cancel):
        report(Progress(400, 800, 100))
        if not attempts:
            attempts.append(packet)
            while not cancel.is_set():
                await asyncio.sleep(0.005)
            raise DownloadError(DownloadFailure.CANCELLED, "Cancelled")
        sample = replace(page.bundled[0], name=packet.title)
        return sounds_model.Downloadable(PackStatus(packet, PackState.DOWNLOADED, None), sample)

    attempts = []
    monkeypatch.setattr(sounds_collections, "fetch_catalogue", catalogue)
    monkeypatch.setattr(sounds_collections, "get_pack", transfer)
    page.overview.downloads.click()
    idle(qtbot, page)
    assert page.collections.downloads.empty.headline.text() == "Catalogue offline"
    available = Catalogue(Manifest(1, ()), CatalogueSource.LIVE)
    page.collections.downloads.refresh.click()
    idle(qtbot, page)
    assert page.collections.downloads.empty.headline.text() == "No sets available yet"
    available = online
    page.collections.downloads.refresh.click()
    idle(qtbot, page)
    card = page.collections.pack_cards["night"]
    card.download.click()
    qtbot.waitUntil(lambda: card.progress.percent.text() == "50%")
    card.progress.cancel.click()
    idle(qtbot, page)
    assert card.download.text() == "Resume download"
    card.download.click()
    idle(qtbot, page)
    card = page.collections.pack_cards["night"]
    assert "Downloaded" in card.status.text()
    click_text(card, "Play sampler")
    assert page.audio.player.calls[-1] == ("sampler", "Night duel")
    click_text(card, "Use set")
    assert page.state == "overview"
    assert page.draft.name == "Night duel"


def test_mixer_keeps_edit_column_and_stops_replaced_preview(
    sound_window: tuple[StudioWindow, SoundsPage, Path], qtbot: QtBot
) -> None:
    _, page, _ = sound_window
    page.cards["builtin:Thonk"].use.click()
    page.open_mixer()
    first, second, *_, last = page.mixer.rows.values()
    first.edit.setFocus()
    qtbot.keyClick(first.edit, Qt.Key.Key_Down)
    assert QApplication.focusWidget() is second.edit
    qtbot.keyClick(second.edit, Qt.Key.Key_End)
    assert QApplication.focusWidget() is last.edit
    assert page.views["mix"].verticalScrollBar().value() > 0
    qtbot.keyClick(last.edit, Qt.Key.Key_Home)
    assert QApplication.focusWidget() is first.edit
    first.edit.click()
    click_text(page.picker, "Play")
    assert page.audio.player.active is not None
    page.events.fill_picker()
    assert page.audio.player.active is None


@pytest.mark.parametrize("size", [(960, 600), (1280, 800)])
def test_overview_controls_fit_when_selected(
    sound_window: tuple[StudioWindow, SoundsPage, Path], qtbot: QtBot, size: tuple[int, int]
) -> None:
    window, page, _ = sound_window
    window.resize(*size)
    card = page.cards["builtin:Synth"]
    card.use.click()
    qtbot.waitUntil(lambda: page.overview.width() <= size[0])
    for button in (page.overview.downloads, page.overview.chants, card.play, card.use):
        assert button.width() >= button.sizeHint().width(), button.text()
    assert page.views["overview"].horizontalScrollBar().maximum() == 0


def test_escape_during_clip_read_keeps_event_unchanged(
    sound_window: tuple[StudioWindow, SoundsPage, Path],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, page, _ = sound_window
    file = audio_file(tmp_path / "New.wav")
    clip = sounds.load_clip(file)
    entered, release = threading.Event(), threading.Event()

    def read(_: Path) -> sounds.Clip:
        entered.set()
        assert release.wait(5)
        return clip

    monkeypatch.setattr(sounds, "load_clip", read)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(file), ""))
    page.open_mixer()
    page.mixer.rows["summon"].edit.click()
    page.picker.import_clip.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert page.escape() and page.state == "mix"
        assert not page.feedback.isVisible()
        page.mixer.rows["summon"].edit.click()
    finally:
        release.set()
    idle(qtbot, page)
    assert page.state == "event" and "summon" not in page.draft.assignments
