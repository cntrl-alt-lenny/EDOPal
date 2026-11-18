import json
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event, current_thread, main_thread
from time import sleep
from zipfile import ZIP_STORED, ZipFile

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

import backup
import config
from backup.core import Cancel, Progress
from studio.context import Installation
from studio.pages.backup import BackupPage
from studio.pages.backup_data import comparison_rows
from studio.pages.backup_review import CompareScreen, PreviewScreen
from studio.pages.backup_screens import (
    ArchiveScreen,
    HomeScreen,
    SaveScreen,
)
from studio.pages.health_layout import confirm_action
from studio.widgets import Button, GameClosedBlock


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
    button(page, "Restore from backup").click()
    qtbot.waitUntil(lambda: isinstance(page.screen, ArchiveScreen))
    page.screen.review.click()
    qtbot.waitUntil(lambda: page.plan is not None)
    assert isinstance(page.screen, PreviewScreen)


def test_installation_states_and_keyboard(page: BackupPage, qtbot: QtBot) -> None:
    selected = page.ctx.installation
    put(Path(selected.path), "replay/A duel.yrpX", b"duel")
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
    assert f"Open {config.APP_NAME} on the other device" in words(page)
    opened = []

    def open_link(path: Path) -> bool:
        assert current_thread() is not main_thread()
        opened.append(path)
        return True

    monkeypatch.setattr("desktop_helpers.open_link", open_link)
    button(page, "Open backup folder").click()
    qtbot.waitUntil(lambda: bool(opened))
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
        "studio.pages.backup.confirm_action",
        lambda _parent, *args: confirmations.append(args) or next(answers),
    )
    open_preview(page, qtbot)
    assert page.plan.policy == "keep_mine"
    assert "letter case" in words(page)
    assert "Nothing has been changed yet" in words(page)
    groups = page.screen.grouped
    conflict = groups.list.rows.rows[0]
    assert "In this EDOPro folder · 4 B" in conflict.detail and "Backup · 13 B" in conflict.detail
    assert "date unavailable" not in conflict.detail
    assert "Blue.ydk" in conflict.tooltip and "BLUE.ydk" in conflict.tooltip
    groups.details_button.click()
    assert groups.list.itemDelegate().expanded == groups.list.currentIndex()
    assert "BLUE.ydk" in conflict.names
    qtbot.keyClick(groups.details_button, Qt.Key.Key_Escape)
    assert page.screen.grouped is groups and not groups.list.itemDelegate().expanded.isValid()
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
    assert confirmations[0][0].startswith("Apply ") and confirmations[0][0].endswith(" changes?")
    assert "Nothing has been changed yet" in confirmations[0][1]
    assert confirmations[0][2] == f"Restore to {root.name}"
    page.screen.restore.click()
    qtbot.waitUntil(lambda: "Restore complete" in words(page) and page.job is None)
    assert local.read_bytes() == b"mine"
    assert new_copy.read_bytes() == b"incoming deck" and unrelated.exists()
    button(page, "Undo this restore").click()
    assert confirmations[-1][2] == "Undo this restore"
    qtbot.waitUntil(lambda: "Previous files restored" in words(page))
    assert local.read_bytes() == b"mine" and unrelated.exists()
    assert not new_copy.exists() and not (root / "deck/New arrival.ydk").exists()
    button(page, "Done").click()
    qtbot.waitUntil(lambda: "This restore has already been undone." in words(page))
    assert not any(
        control.text() == "Undo this restore" and control.isVisible()
        for control in page.screen.findChildren(Button)
    )


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


def test_undo_preflights_the_completion_and_history_on_a_worker(
    page: BackupPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(page.ctx.installation.path)
    affected = root / "deck/New arrival.ydk"
    choose(monkeypatch, snapshot(tmp_path))
    monkeypatch.setattr("studio.pages.backup.confirm_action", lambda *_: True)
    started, release = Event(), Event()
    preview = backup.preview_undo

    def blocked(install: backup.Installation, token: str, **options: object) -> backup.UndoPreview:
        assert current_thread() is not main_thread()
        started.set()
        assert release.wait(8)
        return preview(install, token, **options)

    monkeypatch.setattr(backup, "preview_undo", blocked)
    open_preview(page, qtbot)
    page.screen.restore.click()
    try:
        qtbot.waitUntil(started.is_set)
        assert page.screen.undo.isHidden()
        affected.write_bytes(b"Player edited this restored deck")
    finally:
        release.set()
    qtbot.waitUntil(lambda: "A file was edited after the restore." in words(page))
    assert page.screen.status.path.accessibleDescription() == str(affected)
    assert page.screen.undo.isHidden()
    button(page, "Done").click()
    qtbot.waitUntil(lambda: "A file was edited after the restore." in words(page))
    assert any(
        label.isVisible() and label.accessibleDescription() == str(affected)
        for label in page.screen.findChildren(QLabel)
    )
    assert not any(
        control.text() == "Undo this restore" and control.isVisible()
        for control in page.screen.findChildren(Button)
    )


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
    button(page, "Restore from backup").click()
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
    button(page, "Compare with backup").click()
    qtbot.waitUntil(lambda: isinstance(page.screen, ArchiveScreen))
    page.screen.review.click()
    qtbot.waitUntil(lambda: isinstance(page.screen, CompareScreen))
    assert len(page.screen.grouped.groups["case conflict"]) == 2
    assert len(page.screen.grouped.groups["right only"]) == 1
    assert len(page.screen.grouped.groups["left only"]) == 1
    assert not (root / "deck/New arrival.ydk").exists()
    button(page, "Backup contents").click()
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


@pytest.mark.parametrize("choice", ["Finish restore", "Undo interrupted restore"])
def test_busy_cancel_after_first_write_and_recover(
    page: BackupPage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    choice: str,
) -> None:
    source = snapshot(tmp_path)
    choose(monkeypatch, source)
    monkeypatch.setattr("studio.pages.backup.confirm_action", lambda *_: True)
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
    cancel_button = button(page, "Cancel backup operation")
    qtbot.keyClick(cancel_button, Qt.Key.Key_Escape)
    assert page.job and not page.job.cancel_event.is_set()
    cancel_button.click()
    qtbot.waitUntil(lambda: "Restore was interrupted" in words(page))
    assert not page.ctx.busy and window.change.isEnabled()
    button(page, "Backup & transfer").click()
    window.nav["cards"].click()
    window.nav["backup"].click()
    qtbot.waitUntil(lambda: "Restore was interrupted" in words(page))
    source.unlink()
    if choice == "Undo interrupted restore":
        monkeypatch.setattr("studio.pages.backup._game_running", lambda _: True)
    button(page, choice).click()
    if choice == "Undo interrupted restore":
        qtbot.waitUntil(lambda: "Close EDOPro to continue" in words(page))
        assert page.screen.findChild(GameClosedBlock).retry.text() == "Retry"
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
    monkeypatch.setattr("studio.pages.backup.confirm_action", lambda *_: True)
    monkeypatch.setattr("studio.pages.backup._game_running", lambda _: True)
    open_preview(page, qtbot)
    page.screen.policy.buttons[1].click()
    qtbot.waitUntil(lambda: page.plan is not None and page.plan.policy == "take_theirs")
    page.screen.restore.click()
    qtbot.waitUntil(lambda: "Close EDOPro to continue" in words(page))
    assert page.screen.findChild(GameClosedBlock).retry.text() == "Retry"
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
    monkeypatch.setattr("studio.pages.backup.confirm_action", lambda *_: True)
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
    qtbot.waitUntil(lambda: page.scroll.widget().width() == page.scroll.viewport().width())

    page.screen.save.click()
    qtbot.waitUntil(lambda: isinstance(page.screen, SaveScreen))
    qtbot.waitUntil(lambda: page.scroll.widget().width() == page.scroll.viewport().width())
    guidance = next(
        item for item in page.screen.findChildren(QLabel) if "Export library" in item.text()
    )
    qtbot.waitUntil(
        lambda: (
            guidance.mapTo(page.scroll.viewport(), guidance.rect().bottomLeft()).y()
            < page.scroll.viewport().height()
        )
    )
    qtbot.keyClick(page.screen.choices["decks"], Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: page.screen.restore.isEnabled())
    root = Path(page.ctx.installation.path)
    put(root, "deck/Blue.ydk", b"local")
    put(root, "deck/Identical.ydk", b"edited")
    put(root, "deck/New arrival.ydk", b"another deck")
    choose(monkeypatch, snapshot(tmp_path))
    open_preview(page, qtbot)
    assert page.scroll.widget().width() == page.scroll.viewport().width()
    view = page.screen.grouped.list

    def three_conflicts_visible() -> bool:
        third = view.visualRect(view.model().index(2, 0))
        bottom = view.viewport().mapTo(page.screen, third.bottomLeft()).y()
        button = page.screen.restore
        footer = button.mapTo(page.screen, button.rect().bottomLeft()).y()
        return (
            view.viewport().rect().contains(third)
            and bottom < footer < page.screen.height()
            and page.scroll.verticalScrollBar().maximum() == 0
        )

    qtbot.waitUntil(three_conflicts_visible)
    page.screen.grouped.details_button.click()
    qtbot.waitUntil(three_conflicts_visible)
    large_review = comparison_rows(
        tuple(
            backup.Difference(f"game/deck/Deck {i:04}.ydk", None, "incoming", "right only")
            for i in range(5000)
        )
    )
    compare = CompareScreen("Backup.zip", large_review, page.show_home)
    page._show(compare)
    view = compare.grouped.list
    qtbot.waitUntil(
        lambda: (
            page.scroll.widget().width() == page.scroll.viewport().width()
            and compare.grouped.details_button.isVisible()
        )
    )
    assert view.model().rowCount() == 5000
    last = view.model().index(4999, 0)
    view.scrollTo(last)
    qtbot.waitUntil(lambda: view.visualRect(last).intersects(view.viewport().rect()))
    window.resize(3840, 2160)
    assert page.scroll.widget().width() == page.scroll.viewport().width()


def test_empty_backup_categories_remain_unselected(
    page: BackupPage, qtbot: QtBot, tmp_path: Path
) -> None:
    game = tmp_path / "Empty game"
    game.mkdir()
    put(tmp_path, "sleeves/Personal.png", b"original")
    page.ctx.set_installation(Installation(str(game)))
    qtbot.waitUntil(lambda: page.screen.save.isEnabled())
    page.screen.save.click()
    qtbot.waitUntil(lambda: isinstance(page.screen, SaveScreen))
    screen = page.screen
    assert screen.selected() == ()
    for category in backup.DEFAULTS:
        assert not screen.choices[category].isEnabled()
    assert f"Select {config.APP_NAME} collection to continue" == screen.total.text()
    assert not screen.save.isEnabled()
    screen.choices["studio"].click()
    assert screen.save.isEnabled()


def test_named_confirmation_defaults_to_cancel(page: BackupPage, qtbot: QtBot) -> None:
    roles = []

    def escape() -> None:
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QMessageBox):
            return
        roles.extend((button.text(), dialog.buttonRole(button)) for button in dialog.buttons())
        roles.append(dialog.defaultButton().text())
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)

    QTimer.singleShot(0, escape)
    assert not confirm_action(page, "Apply 1 change?", "Review changes.", "Restore to ProjectIgnis")
    assert ("Restore to ProjectIgnis", QMessageBox.ButtonRole.AcceptRole) in roles
    assert ("Cancel", QMessageBox.ButtonRole.RejectRole) in roles
    assert roles[-1] == "Cancel"
