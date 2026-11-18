from collections.abc import Callable
from pathlib import Path
from threading import Event, current_thread, main_thread

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from doctor import DoctorReport, ListConflict, inspect_installation, support_summary
from doctor.model import finding
from studio.context import Installation
from studio.pages import health
from studio.pages.health_report import byte_size, evidence, groups, heading, route
from studio.shell import StudioWindow
from studio.widgets import Button


def button(window: StudioWindow, name: str) -> Button:
    return next(
        b
        for b in window.current.scroll.widget().findChildren(Button)
        if b.text() == name or b.accessibleName() == name
    )


def test_inspect_support_storage_and_navigation(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    game = Path(window.ctx.installation.path)
    (game / "config").mkdir()
    (game / "config/system.conf").write_text("enable_music = 0\nnickname = private-test-player\n")
    observed: list[Path] = []

    def inspect(path: str, *, config_path: str) -> DoctorReport:
        assert current_thread() is not main_thread()
        assert config_path == window.ctx.cfg.config_path
        observed.append(Path(path))
        return inspect_installation(path, config_path=config_path)

    monkeypatch.setattr(health, "inspect_installation", inspect)
    monkeypatch.setattr("doctor.report._game_running", lambda _: False)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    assert observed == [game]
    page.support.click()
    assert page.preview.toPlainText() == support_summary(page.report)
    assert str(game) not in page.preview.toPlainText()
    assert "private-test-player" not in page.preview.toPlainText()
    button(window, "Copy support summary").click()
    assert QApplication.clipboard().text() == page.preview.toPlainText()
    qtbot.keyClick(page.preview, Qt.Key.Key_Escape)
    page.disk.click()
    qtbot.waitUntil(lambda: page.state == "storage")
    assert page.storage.total_bytes > 0
    revealed = []
    monkeypatch.setattr(page, "open_folder", revealed.append)
    button(window, "Open folder for Artwork").click()
    assert revealed == [game / "pics"]
    qtbot.keyClick(button(window, "Back"), Qt.Key.Key_Return)
    page.rerun.click()
    qtbot.waitUntil(lambda: page.state == "overview")
    assert observed == [game, game]
    item = button(window, "Game music is switched off")
    qtbot.keyClick(item, Qt.Key.Key_Return)
    assert item.isChecked()
    button(window, "Open Music").click()
    assert window.current.id == "music"
    qtbot.waitUntil(lambda: window.current.state == "home")
    assert QApplication.focusWidget() is window.current.toggle_music


def test_selection_errors_and_stale_results(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    window = make_window()
    ctx = window.ctx
    first = ctx.installation
    ctx.set_installation(Installation())
    window.nav["health"].click()
    page = window.current
    assert page.state == "no_game"
    gate, started = Event(), Event()

    def scan(path: str, *, config_path: str) -> DoctorReport:
        if path == first.path:
            started.set()
            assert gate.wait(4)
        return DoctorReport(Path(path), "1.0", None, (finding("databases.missing"),))

    monkeypatch.setattr(health, "inspect_installation", scan)
    ctx.set_installation(first)
    assert page.state == "loading"
    qtbot.waitUntil(started.is_set)
    fresh = tmp_path / "Fresh"
    fresh.mkdir()
    try:
        ctx.set_installation(Installation(str(fresh)))
        qtbot.waitUntil(lambda: page.state == "overview")
    finally:
        gate.set()
    qtbot.waitUntil(lambda: not ctx.tasks.jobs)
    assert page.report.installation == fresh
    assert any("Card data is still missing" in line.text() for line in page.findChildren(QLabel))

    def failed(*args: object, **kwargs: object) -> None:
        raise PermissionError("Cannot open game folder")

    monkeypatch.setattr(health, "inspect_installation", failed)
    page.rerun.click()
    qtbot.waitUntil(lambda: page.state == "error")
    monkeypatch.setattr(health, "inspect_installation", scan)
    button(window, "Retry").click()
    qtbot.waitUntil(lambda: page.state == "overview")
    ctx.set_installation(Installation())
    assert page.state == "no_game"


def test_storage_back_cancels_read_without_publishing_old_result(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    monkeypatch.setattr("doctor.report._game_running", lambda _: False)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    started = Event()

    def measure(game: str, *, data_dir: Path, cancelled: Callable[[], bool]) -> None:
        from time import monotonic, sleep

        started.set()
        end = monotonic() + 3
        while not cancelled() and monotonic() < end:
            sleep(0.01)
        assert cancelled()
        raise RuntimeError("Cancelled")

    monkeypatch.setattr(health, "storage_breakdown", measure)
    page.disk.click()
    qtbot.waitUntil(started.is_set)
    button(window, "Back").click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert page.state == "overview" and page.storage is None


def test_finding_order_routes_and_bounded_evidence() -> None:
    items = tuple(
        finding(key)
        for key in ("skin.available", "music.disabled", "fonts.missing", "skin.missing")
    )
    assert [status for status, _, _ in groups(items)] == ["problem", "warning", "info", "ok"]
    assert route(finding("state.interrupted", category="effects")) == "sounds"
    assert route(finding("state.interrupted", category="sleeves")) == "sleeves"
    assert route(finding("artwork.coverage")) == "cards"
    assert ("Images", "0 · 1 · 2 · 3 · and 9,996 more") in list(
        evidence(finding("skin.images", images=tuple(range(10000))))
    )
    assert byte_size(1024**3) == "1.0 GB"


def test_full_evidence_pages_and_keyboard_back(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    item = finding("skin.images", images=tuple(f"Texture {n}" for n in range(25)))
    report = DoctorReport(Path(window.ctx.installation.path), "1.0", None, (item,))
    monkeypatch.setattr(health, "inspect_installation", lambda *a, **k: report)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    button(window, item.title).click()
    button(window, "Images · See all 25").click()
    button(window, "Next").click()
    from PySide6.QtWidgets import QPlainTextEdit

    preview = page.scroll.widget().findChild(QPlainTextEdit)
    assert "Texture 24" in preview.toPlainText() and "Texture 19" not in preview.toPlainText()
    assert not button(window, "Next").isEnabled()
    qtbot.keyClick(preview, Qt.Key.Key_Escape)
    toggle = button(window, item.title)
    assert page.state == "overview" and toggle.isChecked()
    qtbot.keyClick(toggle, Qt.Key.Key_Escape)
    assert not toggle.isChecked() and window.isVisible()


@pytest.mark.parametrize(
    ("key", "extra", "destination"),
    [
        ("decks.unknown_cards", {}, "My decks"),
        ("banlists.missing", {}, "My decks"),
        ("artwork.coverage", {"cards_have": 10, "cards_total": 128}, "HD Cards"),
        ("music.missing", {"missing": ("deck",)}, "Music"),
        ("state.interrupted", {"category": "music"}, "Music"),
        ("state.interrupted", {"category": "effects"}, "Sounds"),
        ("state.interrupted", {"category": "sleeves"}, "Sleeves"),
        ("storage.low", {}, "HD Cards"),
        ("skin.missing", {}, "Skins"),
    ],
)
def test_open_the_relevant_view(
    make_window: Callable,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    extra: dict[str, object],
    destination: str,
) -> None:
    window = make_window()
    game = Path(window.ctx.installation.path)
    deck = game / "deck" / "Another folder" / "Spellcasters.ydk"
    deck.parent.mkdir()
    deck.write_text("#main\n999999\n", encoding="utf-8")
    path = game / "sound/.edopro-hd-sync/deck/pending.json" if key == "state.interrupted" else deck
    item = finding(key, path=path, **extra)
    report = DoctorReport(game, "1.0", None, (item,))
    monkeypatch.setattr(health, "inspect_installation", lambda *a, **k: report)
    window.nav["health"].click()
    qtbot.waitUntil(lambda: window.current.state == "overview")
    if item.severity == "info":
        button(window, "Notes").click()
    button(window, heading(item)).click()
    button(window, f"Open {destination}").click()
    page = window.current
    assert page.id == route(item)
    if key == "decks.unknown_cards":
        qtbot.waitUntil(lambda: len(page.history) == 2)
        assert any(str(deck) == label.toolTip() for label in page.view.findChildren(QLabel))
    elif key == "banlists.missing":
        qtbot.waitUntil(lambda: len(page.history) == 2)
        assert page.view.summary.text() == "No forbidden lists installed"
    elif destination == "Music":
        qtbot.waitUntil(lambda: page.state == "playlist")
        assert page.scene == "deck"
    elif key == "artwork.coverage":
        qtbot.waitUntil(lambda: page.state == "results")
    elif key == "storage.low":
        assert page.state == "options"
        assert QApplication.focusWidget() is page.options.scope.buttons[1]
    elif destination == "Sounds":
        assert page.state == "overview"
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs, timeout=15000)


def test_notes_stay_collapsed_and_explain_practice_matches(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    game = Path(window.ctx.installation.path)
    sample = ListConflict(game / "AI_Dragon.ydk", game / "lflist.conf", "Cup", 7, "Dragon", 2, 1)
    practice = finding(
        "windbot.list_conflicts", decks=8, lists=10, conflicts=2400, details=(sample,) * 2400
    )
    missing = finding("skin.missing")
    passed = finding("installation.found")
    report = DoctorReport(game, "1.0", None, (practice, passed))
    monkeypatch.setattr(health, "inspect_installation", lambda *a, **k: report)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    notes = button(window, "Notes")
    assert not notes.isChecked()
    assert not button(window, "Passed checks").isChecked()
    assert any(label.text() == "Checks passed" for label in page.findChildren(QLabel))
    notes.click()
    button(window, heading(practice)).click()
    visible = "\n".join(label.text() for label in page.scroll.widget().findChildren(QLabel))
    assert "8 bundled practice decks" in visible and "10 installed lists" in visible
    assert "2,400 matches count each card limit separately" in visible
    button(window, "Review card limit matches").click()
    assert page.state == "evidence"
    page.escape()
    assert button(window, "Notes").isChecked()
    report = DoctorReport(game, "1.0", None, (practice, passed, missing))
    page.rerun.click()
    qtbot.waitUntil(lambda: page.state == "overview")
    qtbot.waitUntil(lambda: button(window, missing.title).isVisible())
    assert not button(window, "Notes").isChecked()
