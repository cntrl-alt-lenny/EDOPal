from collections.abc import Callable
from gc import collect
from pathlib import Path
from threading import Event, current_thread, main_thread
from weakref import ref

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit
from pytestqt.qtbot import QtBot

import artwork_coverage
import backup
import config
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

    def inspect(path: str, *, config_path: str, cancelled: Callable[[], bool]) -> DoctorReport:
        assert current_thread() is not main_thread()
        assert config_path == window.ctx.cfg.config_path
        observed.append(Path(path))
        return inspect_installation(path, config_path=config_path, cancelled=cancelled)

    monkeypatch.setattr(health, "inspect_installation", inspect)
    monkeypatch.setattr("doctor.report._game_running", lambda _: False)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    assert observed == [game]
    page.support.click()
    expected = support_summary(page.report).splitlines(keepends=True)
    expected[0] = f"{config.APP_NAME} support summary\n"
    assert page.preview.toPlainText() == "".join(expected)
    assert str(game) not in page.preview.toPlainText()
    assert "private-test-player" not in page.preview.toPlainText()
    button(window, "Copy support summary").click()
    assert QApplication.clipboard().text() == page.preview.toPlainText()
    qtbot.keyClick(page.preview, Qt.Key.Key_Escape)
    page.disk.click()
    qtbot.waitUntil(lambda: page.state == "storage")
    assert page.storage.total_bytes > 0
    revealed = []

    def open_folder(path: Path) -> bool:
        assert current_thread() is not main_thread()
        revealed.append(path)
        return True

    monkeypatch.setattr("desktop_helpers.open_link", open_folder)
    button(window, "Open folder for Artwork").click()
    qtbot.waitUntil(lambda: bool(revealed))
    assert revealed == [game / "pics"]
    qtbot.keyClick(button(window, "Health check"), Qt.Key.Key_Return)
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


def test_revisit_reuses_report_until_another_page_changes_the_game(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    game = Path(window.ctx.installation.path)
    (game / "config").mkdir()
    (game / "config/system.conf").write_text("enable_music = 0\n", encoding="utf-8")
    monkeypatch.setattr("doctor.report._game_running", lambda _: False)
    now = 100.0
    monkeypatch.setattr(health, "time", lambda: now)

    assert window.navigate("health")
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview" and page.scan_job is None)
    original = page.report
    assert any(item.id == "music.disabled" for item in original.findings)
    fresh_labels = {item.text() for item in page.scroll.widget().findChildren(QLabel)}

    now += 180
    assert window.navigate("cards") and window.navigate("health")
    assert page.report is original and page.scan_job is None
    cached_labels = {item.text() for item in page.scroll.widget().findChildren(QLabel)}

    assert window.navigate("music")
    music = window.current
    qtbot.waitUntil(lambda: music.state == "home")
    music.toggle_music.click()
    qtbot.waitUntil(lambda: music.state == "home" and music.data.enabled is True)

    assert window.navigate("health")
    qtbot.waitUntil(lambda: page.state == "overview" and page.scan_job is None)
    assert page.report is not original
    assert all(item.id != "music.disabled" for item in page.report.findings)
    assert "Checked just now" in fresh_labels
    assert "Checked 3 minutes ago" in cached_labels
    assert any(
        item.text() == "Checked just now" for item in page.scroll.widget().findChildren(QLabel)
    )


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

    def scan(path: str, *, config_path: str, cancelled: Callable[[], bool]) -> DoctorReport:
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
        assert page.scan_job.cancel_event.is_set()
        assert page.state == "loading"
        gate.set()
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


def test_scan_stops_reading_on_leave_and_close_and_cached_visit_reads_nothing(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    ctx = window.ctx
    qtbot.waitUntil(lambda: not ctx.tasks.jobs)
    entered, release = Event(), Event()
    reads: list[list[Path]] = []
    real_sample = artwork_coverage.image_sample

    def scan(game: str, *, config_path: str, cancelled: Callable[[], bool]) -> DoctorReport:
        reads.append([])
        return inspect_installation(game, config_path=config_path, cancelled=cancelled)

    def sample(path: str) -> bytes:
        current = Path(path)
        reads[-1].append(current)
        if len(reads) == 1 or (len(reads) == 3 and current.parent.name == "field"):
            entered.set()
            assert release.wait(8)
        return real_sample(path)

    monkeypatch.setattr(health, "inspect_installation", scan)
    monkeypatch.setattr(artwork_coverage, "image_sample", sample)
    monkeypatch.setattr("doctor.report._game_running", lambda _: False)
    window.nav["health"].click()
    page = window.current
    try:
        qtbot.waitUntil(entered.is_set)
        first = page.scan_job
        assert window.navigate("cards") and window.navigate("health")
        assert page.scan_job is first and first.cancel_event.is_set()
        assert ctx.tasks.jobs == {first} and len(reads) == 1
    finally:
        release.set()
    qtbot.waitUntil(lambda: page.state == "overview" and page.scan_job is None)
    assert [len(batch) for batch in reads] == [1, 106]

    report = page.report
    assert window.navigate("cards") and window.navigate("health")
    assert page.report is report and not ctx.tasks.jobs
    assert [len(batch) for batch in reads] == [1, 106]

    entered.clear()
    release.clear()
    page.rerun.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert len(reads) == 3 and len(reads[2]) == 97
        job = page.scan_job
        assert ctx.tasks.jobs == {job}
        window.close()
        assert job.cancel_event.is_set()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible() and not ctx.tasks.jobs)
    assert page.scan_job is None and [len(batch) for batch in reads] == [1, 106, 97]


def test_reports_interrupted_backup_and_skin_journals(tmp_path: Path) -> None:
    from themes.transaction import STATE

    game = tmp_path / "Game"
    game.mkdir()
    (game / "EDOPro.exe").touch()
    skin = game / STATE / "pending.json"
    skin.parent.mkdir(parents=True)
    skin.write_text("{}")
    source = tmp_path / "Source"
    (source / "deck").mkdir(parents=True)
    (source / "deck/Test.ydk").write_bytes(b"#main\n1000\n")
    (source / "deck/Second.ydk").write_bytes(b"#main\n1001\n")
    archive = tmp_path / "transfer.zip"
    backup.create(backup.Installation(source, tmp_path / "source-data"), archive)
    install = backup.Installation(game, tmp_path / "profile")
    plan = backup.plan_restore(archive, install)
    stopped = Event()

    def cancel_after_publish(done: int, total: int, path: str) -> None:
        if done > len(plan.manifest.entries):
            stopped.set()

    with pytest.raises(backup.Cancelled):
        backup.execute(plan, progress=cancel_after_publish, cancel=stopped.is_set)
    report = inspect_installation(game, config_path=install.data / "config.json")
    pending = {route(item): item for item in report.findings if item.id == "state.interrupted"}
    assert pending["skins"].evidence["path"] == skin
    assert pending["backup"].evidence["path"].name == "journal.json"
    assert not any(item.id == "state.clean" for item in report.findings)


@pytest.mark.parametrize("page_id", ["health", "backup", "replays"])
def test_completed_page_reads_release_jobs_and_captured_data(
    make_window: Callable, qtbot: QtBot, page_id: str
) -> None:
    window = make_window()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    page = window.pages[page_id]
    submit = page._start if page_id == "backup" else page.read
    jobs, payloads = [], []

    class Payload:
        def __init__(self) -> None:
            self.data = bytes(256 * 1024)

    for _ in range(4):
        payload = Payload()
        payloads.append(ref(payload))
        submit(lambda _: None, lambda _, capture=payload: capture.data, pytest.fail)
        jobs.append(ref(next(iter(window.ctx.tasks.jobs))))
        del payload
        qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)

    def released() -> bool:
        collect()
        return all(reference() is None for reference in (*jobs, *payloads))

    qtbot.waitUntil(released)


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
    button(window, "Health check").click()
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
    window.resize(960, 600)
    game = Path(window.ctx.installation.path)
    sample = ListConflict(game / "AI_Dragon.ydk", game / "lflist.conf", "Cup", 7, "Dragon", 2, 1)
    practice = finding(
        "windbot.list_conflicts", decks=8, lists=10, conflicts=2400, details=(sample,) * 2400
    )
    missing = finding("skin.missing")
    warning = finding("banlists.missing")
    passed = finding("installation.found")
    report = DoctorReport(game, "1.0", None, (practice, passed))
    monkeypatch.setattr(health, "inspect_installation", lambda *a, **k: report)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    notes = button(window, "Notes")
    assert not notes.isChecked()
    assert not button(window, "Passed checks").isChecked()
    assert any(label.text() == "No problems found" for label in page.findChildren(QLabel))
    notes.click()
    button(window, heading(practice)).click()
    visible = "\n".join(label.text() for label in page.scroll.widget().findChildren(QLabel))
    assert "8 bundled practice decks" in visible and "10 installed lists" in visible
    assert "2,400 matches count each card limit separately" in visible
    button(window, "Review card limit matches").click()
    assert page.state == "evidence"
    preview = page.scroll.widget().findChild(QPlainTextEdit)
    qtbot.waitUntil(
        lambda: (
            page.scroll.verticalScrollBar().maximum() == 0
            and preview.verticalScrollBar().maximum() > 0
        )
    )
    next_page = button(window, "Next")
    qtbot.waitUntil(next_page.isVisible)
    next_page.click()
    assert any(
        line.text() == "21–40 of 2,400" for line in page.scroll.widget().findChildren(QLabel)
    )
    page.escape()
    assert button(window, "Notes").isChecked()
    report = DoctorReport(game, "1.0", None, (practice, passed, missing, warning))
    page.rerun.click()
    qtbot.waitUntil(lambda: page.state == "overview")
    qtbot.waitUntil(lambda: button(window, missing.title).isVisible())
    assert any(label.text() == "1 problem, 1 warning" for label in page.findChildren(QLabel))
    assert button(window, missing.title).accessibleDescription() == "Problem"
    assert button(window, warning.title).accessibleDescription() == "Warning"
    assert not button(window, "Notes").isChecked()


def test_idle_lock_keeps_technical_wording_in_support_summary(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    game = Path(window.ctx.installation.path)
    marker = game / "private-lock-name"
    idle = finding("state.lock_idle", path=marker)
    report = DoctorReport(game, "1.0", None, (idle, finding("state.clean")))
    monkeypatch.setattr(health, "inspect_installation", lambda *a, **k: report)
    window.nav["health"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "overview")
    button(window, "Notes").click()
    button(window, "An earlier update left a marker").click()
    words = [line.text() for line in page.findChildren(QLabel)]
    assert "You can leave it in place and continue using the update tools." in words
    assert all(marker.name not in line for line in words)
    page.support.click()
    support = page.preview.toPlainText()
    assert f"Inactive {config.APP_NAME} lock file found" in support
    assert f"No interrupted {config.APP_NAME} changes found" in support
    assert marker.name not in support
