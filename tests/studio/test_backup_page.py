import json
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import sleep
from zipfile import ZIP_STORED, ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QLabel
from pytestqt.qtbot import QtBot

import backup
from backup.core import Cancel, Progress
from studio.context import Installation
from studio.pages.backup import BackupPage
from studio.pages.backup_data import comparison_rows
from studio.pages.backup_screens import (
    ArchiveScreen,
    CompareScreen,
    HomeScreen,
    PreviewScreen,
    SaveScreen,
)
from studio.widgets import Button


def put(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def snapshot(root: Path, *, preferences: bool = False) -> Path:
    source = root / "From PC"
    conflict = put(source, "deck/BLUE.ydk", b"incoming deck")
    os.utime(conflict, (1_700_000_000, 1_700_000_000))
    put(source, "deck/Identical.ydk", b"same deck")
    put(source, "deck/New arrival.ydk", b"new deck")
    if preferences:
        put(source, "config/system.conf", b"nickname = Incoming\ndpi = 200\n")
    items = (*backup.DEFAULTS, "preferences") if preferences else backup.DEFAULTS
    path = root / "PC backup.zip"
    backup.create(backup.Installation(source, root / "source-data"), path, items)
    return path


def words(page: BackupPage) -> str:
    return "\n".join(
        label.text() for label in page.screen.findChildren(QLabel) if label.isVisible()
    )


def button(page: BackupPage, title: str) -> Button:
    return next(
        control
        for control in page.screen.findChildren(Button)
        if control.isVisible() and title in {control.text(), control.accessibleName()}
    )


def choose(monkeypatch: pytest.MonkeyPatch, filename: Path) -> None:
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(filename), ""))


@pytest.fixture
def page(make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> BackupPage:
    monkeypatch.setattr("studio.pages.backup._game_running", lambda _: False)
    monkeypatch.setattr("backup.restore._game_running", lambda _: False)
    window = make_window()
    monkeypatch.setattr("config.find_edopro_folders", lambda *_: [window.ctx.installation.path])
    window.nav["backup"].click()
    page = window.current
    qtbot.waitUntil(lambda: isinstance(page.screen, HomeScreen) and page.screen.save.isEnabled())
    return page


def open_preview(page: BackupPage, qtbot: QtBot) -> None:
    button(page, "Restore").click()
    qtbot.waitUntil(lambda: isinstance(page.screen, ArchiveScreen))
    page.screen.review.click()
    qtbot.waitUntil(lambda: page.plan is not None)
    assert isinstance(page.screen, PreviewScreen)


def test_installation_states_and_keyboard(page: BackupPage, qtbot: QtBot) -> None:
    selected = page.ctx.installation
    page.ctx.set_installation(Installation())
    assert "Choose your EDOPro folder" in words(page)
    qtbot.keyClick(button(page, "Choose folder"), Qt.Key.Key_Return)
    assert page.window().content.currentWidget() is page.window().picker
    page.window().picker.close_picker()
    page.ctx.set_installation(Installation(selected.path))
    qtbot.waitUntil(lambda: page.screen.save.isEnabled())
    assert "still setting up" in words(page)
    qtbot.keyClick(page.screen.save, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: isinstance(page.screen, SaveScreen))
    page.screen.choices["decks"].setFocus()
    assert page.window().focusWidget() is page.screen.choices["decks"]
    qtbot.keyClick(page.screen.choices["decks"], Qt.Key.Key_Right)
    assert page.window().focusWidget() is page.screen.choices["replays"]
    qtbot.keyClick(page.screen.choices["decks"], Qt.Key.Key_Escape)
    assert isinstance(page.screen, HomeScreen)


def test_save_counts_retry_and_open_folder(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = Path(page.ctx.installation.path)
    put(root, "replay/Last.yrpX", b"x" * 1536)
    put(root, "screenshots/Prize.png", b"x" * 2048)
    page.screen.save.click()
    qtbot.waitUntil(lambda: isinstance(page.screen, SaveScreen))
    assert page.screen.selected() == backup.DEFAULTS
    assert "2 items  ·  1.5 KB" in page.screen.total.text()
    page.screen.choices["screenshots"].click()
    assert "3 items  ·  3.5 KB" in page.screen.total.text()

    old = put(tmp_path, "Existing.zip", b"keep original")
    new = tmp_path / "From this device"
    picks = iter((old, new))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(next(picks)), "")
    )
    page.screen.save.click()
    qtbot.waitUntil(lambda: "That name is already in use" in words(page))
    assert old.read_bytes() == b"keep original"
    button(page, "Retry").click()
    qtbot.waitUntil(lambda: "Backup saved" in words(page))
    manifest = backup.inspect(new.with_suffix(".zip"))
    assert set(manifest.categories) == {"decks", "replays", "screenshots"}
    assert manifest.created_at is not None
    assert all(entry.modified_at is not None for entry in manifest.entries)
    assert "Copy this file to your other device" in words(page)
    opened = []
    monkeypatch.setattr(
        "studio.pages.backup.QDesktopServices.openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )
    button(page, "Open backup folder").click()
    assert opened == [tmp_path]


def test_preview_case_policy_confirmation_and_undo(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = Path(page.ctx.installation.path)
    local = put(root, "deck/Blue.ydk", b"mine")
    put(root, "deck/Identical.ydk", b"same deck")
    unrelated = put(root, "deck/Only here.ydk", b"unrelated")
    choose(monkeypatch, snapshot(tmp_path))
    answers = iter((False, True, True))
    confirmations = []
    monkeypatch.setattr(
        page.ctx, "confirm", lambda *args: confirmations.append(args) or next(answers)
    )
    open_preview(page, qtbot)
    assert page.plan.policy == "keep_mine"
    assert "letter case" in words(page)
    assert "Nothing has been changed yet" in words(page)
    groups = page.screen.grouped
    conflict = groups.list.rows.rows[0]
    assert "Here · 4 B" in conflict.detail and "Backup · 13 B" in conflict.detail
    assert "date unavailable" not in conflict.detail
    assert "Blue.ydk" in conflict.tooltip and "BLUE.ydk" in conflict.tooltip
    groups.details_button.click()
    assert groups.details.isVisible() and "BLUE.ydk" in groups.details.text()
    qtbot.keyClick(groups.details_button, Qt.Key.Key_Escape)
    assert page.screen.grouped is groups and groups.details.isHidden()
    groups.tabs["identical"].click()
    assert groups.list.rows.rows[0].title == "Decks / Identical"
    groups.tabs["add"].click()
    assert groups.list.rows.rows[0].title == "Decks / New arrival"
    groups.tabs["conflict"].click()
    page.screen.policy.buttons[1].click()
    qtbot.waitUntil(lambda: page.plan is not None and page.plan.policy == "take_theirs")
    assert "Replace the current version" in page.screen.grouped.list.rows.rows[0].action
    page.screen.policy.buttons[2].click()
    qtbot.waitUntil(lambda: page.plan is not None and page.plan.policy == "keep_both")
    new_copy = root / "deck/Blue (incoming).ydk"
    page.screen.restore.click()
    assert local.read_bytes() == b"mine" and not new_copy.exists()
    assert "reviewed changes" in confirmations[0][1]
    assert "Nothing has been changed yet" in confirmations[0][1]
    page.screen.restore.click()
    qtbot.waitUntil(lambda: "Restore complete" in words(page))
    assert local.read_bytes() == b"mine"
    assert new_copy.read_bytes() == b"incoming deck" and unrelated.exists()
    button(page, "Undo this restore").click()
    qtbot.waitUntil(lambda: "Previous files restored" in words(page))
    assert local.read_bytes() == b"mine" and unrelated.exists()
    assert not new_copy.exists() and not (root / "deck/New arrival.ydk").exists()
    button(page, "Back to backups").click()
    qtbot.waitUntil(
        lambda: any(
            control.text() == "Undo selected restore" and control.isVisible()
            for control in page.screen.findChildren(Button)
        )
    )
    assert not button(page, "Undo selected restore").isEnabled()


def test_restore_preview_without_conflicts(
    page: BackupPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_game = tmp_path / "Steam Deck"
    empty_game.mkdir()
    page.ctx.set_installation(Installation(str(empty_game)))
    qtbot.waitUntil(lambda: isinstance(page.screen, HomeScreen) and page.screen.restore.isEnabled())
    choose(monkeypatch, snapshot(tmp_path))
    open_preview(page, qtbot)
    assert page.screen.policy.isHidden()
    assert page.screen.restore.isEnabled()
    assert "Restore to Steam Deck" in words(page)
    assert "Nothing has been changed yet" in words(page) and not list(empty_game.iterdir())
    assert page.escape()
    assert isinstance(page.screen, ArchiveScreen)


def test_leaving_while_opening_an_archive_discards_the_previous_selection(
    page: BackupPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = snapshot(tmp_path)
    choose(monkeypatch, archive)
    inspect = backup.inspect
    started, release = Event(), Event()

    def delayed(path: Path) -> backup.Manifest:
        started.set()
        assert release.wait(5)
        return inspect(path)

    monkeypatch.setattr(backup, "inspect", delayed)
    button(page, "Restore").click()
    try:
        qtbot.waitUntil(started.is_set)
        assert page.ctx.navigate("cards")
        game = tmp_path / "Other installation"
        game.mkdir()
        page.ctx.set_installation(Installation(str(game)))
        assert page.ctx.navigate("backup")
        qtbot.waitUntil(lambda: isinstance(page.screen, HomeScreen) and page.job is None)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not page.ctx.tasks.jobs)
    assert isinstance(page.screen, HomeScreen) and page.archive is None


def test_compare_and_unknown_format(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = Path(page.ctx.installation.path)
    put(root, "deck/Blue.ydk", b"mine")
    put(root, "deck/Identical.ydk", b"same deck")
    archive = snapshot(tmp_path)
    choose(monkeypatch, archive)
    button(page, "Compare with a backup").click()
    qtbot.waitUntil(lambda: isinstance(page.screen, ArchiveScreen))
    page.screen.review.click()
    qtbot.waitUntil(lambda: isinstance(page.screen, CompareScreen))
    assert len(page.screen.grouped.groups["case conflict"]) == 2
    assert len(page.screen.grouped.groups["right only"]) == 1
    assert len(page.screen.grouped.groups["left only"]) == 1
    assert not (root / "deck/New arrival.ydk").exists()
    button(page, "Back").click()
    changed = tmp_path / "New format.zip"
    with ZipFile(archive) as original, ZipFile(changed, "w", ZIP_STORED) as new:
        for name in original.namelist():
            content = original.read(name)
            if name == "manifest.json":
                doc = json.loads(content)
                doc["format_version"] = 500
                content = json.dumps(doc).encode()
            new.writestr(name, content)
    choose(monkeypatch, changed)
    button(page, "Choose another backup").click()
    qtbot.waitUntil(lambda: "This backup needs another version" in words(page))
    assert "newer version" in words(page)


@pytest.mark.parametrize("choice", ["Finish restore", "Roll back"])
def test_busy_cancel_after_first_write_and_recover(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    choice: str,
) -> None:
    source = snapshot(tmp_path)
    choose(monkeypatch, source)
    monkeypatch.setattr(page.ctx, "confirm", lambda *_: True)
    open_preview(page, qtbot)
    real_execute = backup.execute
    published = Event()

    def interrupted(
        plan: backup.Plan,
        *,
        progress: Progress,
        cancel: Cancel,
        require_closed: bool,
    ) -> backup.Transaction:
        def pause(done: int, total: int, path: str) -> None:
            assert progress is not None and cancel is not None
            progress(done, total, path)
            if done == len(plan.manifest.entries) + 1:
                published.set()
                for _ in range(500):
                    if cancel():
                        return
                    sleep(0.01)
                raise RuntimeError("The UI did not cancel the restore")

        return real_execute(plan, progress=pause, cancel=cancel, require_closed=require_closed)

    monkeypatch.setattr(backup, "execute", interrupted)
    page.screen.restore.click()
    qtbot.waitUntil(published.is_set)
    window = page.window()
    assert page.ctx.busy and not window.change.isEnabled()
    window.nav["cards"].click()
    assert window.current is page
    qtbot.keyClick(button(page, "Cancel"), Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: "Restore was interrupted" in words(page))
    assert not page.ctx.busy and window.change.isEnabled()
    button(page, "Back").click()
    window.nav["cards"].click()
    window.nav["backup"].click()
    qtbot.waitUntil(lambda: "Restore was interrupted" in words(page))
    source.unlink()
    if choice == "Roll back":
        monkeypatch.setattr("studio.pages.backup._game_running", lambda _: True)
    button(page, choice).click()
    if choice == "Roll back":
        qtbot.waitUntil(lambda: "Close EDOPro to continue" in words(page))
        monkeypatch.setattr("studio.pages.backup._game_running", lambda _: False)
        button(page, "Retry").click()
    result = "Restore complete" if choice == "Finish restore" else "Previous files restored"
    qtbot.waitUntil(lambda: result in words(page))
    root = Path(page.ctx.installation.path)
    assert (root / "deck/New arrival.ydk").exists() == (choice == "Finish restore")
    assert (root / "deck/BLUE.ydk").exists() == (choice == "Finish restore")


def test_close_game_and_retry_preferences(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = Path(page.ctx.installation.path)
    local = put(root, "config/system.conf", b"nickname = Local\ndpi = 144\n")
    choose(monkeypatch, snapshot(tmp_path, preferences=True))
    monkeypatch.setattr(page.ctx, "confirm", lambda *_: True)
    monkeypatch.setattr("studio.pages.backup._game_running", lambda _: True)
    open_preview(page, qtbot)
    page.screen.policy.buttons[1].click()
    qtbot.waitUntil(lambda: page.plan is not None and page.plan.policy == "take_theirs")
    page.screen.restore.click()
    qtbot.waitUntil(lambda: "Close EDOPro to continue" in words(page))
    assert local.read_bytes() == b"nickname = Local\ndpi = 144\n"
    assert not (root / "deck/New arrival.ydk").exists()
    monkeypatch.setattr("studio.pages.backup._game_running", lambda _: False)
    button(page, "Retry").click()
    qtbot.waitUntil(lambda: "Restore complete" in words(page))
    assert local.read_bytes() == b"nickname = Incoming\ndpi = 144\n"
    assert "the next time you start EDOPro" in words(page)


def test_stale_preview_requires_new_confirmation(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    choose(monkeypatch, snapshot(tmp_path))
    monkeypatch.setattr(page.ctx, "confirm", lambda *_: True)
    open_preview(page, qtbot)
    root = Path(page.ctx.installation.path)
    later = put(root, "deck/New arrival.ydk", b"later edit")
    page.screen.restore.click()
    qtbot.waitUntil(lambda: "The restore preview is out of date" in words(page))
    assert later.read_bytes() == b"later edit"
    assert not (root / "deck/BLUE.ydk").exists()
    button(page, "Refresh preview").click()
    qtbot.waitUntil(lambda: page.plan is not None and isinstance(page.screen, PreviewScreen))
    assert len(page.screen.grouped.groups["conflict"]) == 1
    assert not (root / "deck/BLUE.ydk").exists()


def test_preview_reflows_and_lists_thousands(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    window = page.window()
    window.resize(960, 600)
    assert page.screen.width() <= page.scroll.viewport().width()
    page.screen.save.click()
    qtbot.waitUntil(lambda: isinstance(page.screen, SaveScreen))
    assert page.screen.width() <= page.scroll.viewport().width()
    qtbot.keyClick(page.screen.choices["decks"], Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: page.screen.restore.isEnabled())
    choose(monkeypatch, snapshot(tmp_path))
    open_preview(page, qtbot)
    assert page.screen.width() <= page.scroll.viewport().width()
    large_review = comparison_rows(
        tuple(
            backup.Difference(f"game/deck/Deck {i:04}.ydk", None, "incoming", "right only")
            for i in range(5000)
        )
    )
    compare = CompareScreen("Backup.zip", large_review, page.show_home)
    page._show(compare)
    view = compare.grouped.list
    assert view.model().rowCount() == 5000
    last = view.model().index(4999, 0)
    view.scrollTo(last)
    qtbot.waitUntil(lambda: view.visualRect(last).intersects(view.viewport().rect()))
    window.resize(3840, 2160)
    assert page.screen.width() <= page.scroll.viewport().width()
