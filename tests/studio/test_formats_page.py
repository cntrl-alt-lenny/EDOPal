from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from formats import installation as engine
from music.settings import GameProcessStatus
from studio.context import Installation
from studio.pages.formats import FormatsPage
from studio.widgets import Button


def ready(p: FormatsPage, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: p.loaded and not p.ctx.tasks.jobs)


def button(p: FormatsPage, name: str) -> Button:
    control = p.scroll.widget().findChild(Button, name)
    assert control is not None
    return control


@pytest.fixture
def page(make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> FormatsPage:
    monkeypatch.setattr(engine.settings, "game_process_status", lambda _: GameProcessStatus.CLOSED)
    win = make_window()
    win.nav["formats"].click()
    p = win.current
    ready(p, qtbot)
    return p


def test_search_host_copy_install_and_remove(page: FormatsPage, qtbot: QtBot) -> None:
    page.search.setText("edison")
    assert page.row["name"] == "Edison"
    button(page, "host").click()
    qtbot.keyClick(button(page, "copy"), Qt.Key.Key_Return)
    assert page.row["display"] in QApplication.clipboard().text()
    qtbot.keyClick(button(page, "copy"), Qt.Key.Key_Escape)
    ready(page, qtbot)
    button(page, "single").click()
    ready(page, qtbot)
    path = Path(page.ctx.installation.path) / "lflists" / page.row["file"]
    assert path.exists() and button(page, "single").text() == "Remove list"
    button(page, "single").click()
    ready(page, qtbot)
    assert not path.exists()
    page.search.setText("no such format")
    assert not page.list.currentItem()
    page.search.clear()
    page.region_buttons["OCG"].click()
    assert page.row["category"] == "OCG"


def test_unsupported_and_installation_changes(page: FormatsPage, qtbot: QtBot) -> None:
    page.search.setText("genesys")
    assert not page.scroll.widget().findChild(Button, "single")
    assert any("points" in text.text() for text in page.scroll.widget().findChildren(QLabel))
    page.search.setText("goat")
    path = page.ctx.installation.path
    for installation in (Installation(), Installation(path)):
        page.ctx.set_installation(installation)
        ready(page, qtbot)
        assert button(page, "choose").isEnabled()
        assert not page.manage.isEnabled()
        assert not page.scroll.widget().findChild(Button, "single")


def test_bulk_region_and_remove_all_keep_edits(
    page: FormatsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(page.ctx, "confirm", lambda *_: True)
    page.region_buttons["OCG"].click()
    page.manage.click()
    button(page, "region").click()
    ready(page, qtbot)
    rows = page.catalogue.select("OCG")
    assert len(rows) == 35 and all(page.states[r["id"]] == engine.State.INSTALLED for r in rows)
    path = Path(page.ctx.installation.path) / "lflists" / rows[0]["file"]
    edited = path.read_bytes() + b"# Keep my notes\n"
    path.write_bytes(edited)
    button(page, "removeAll").click()
    ready(page, qtbot)
    assert path.read_bytes() == edited and page.states[rows[0]["id"]] == engine.State.MODIFIED
    assert all(page.states[r["id"]] == engine.State.ABSENT for r in rows[1:])
    button(page, "undo").click()
    ready(page, qtbot)
    assert path.read_bytes() == edited
    assert all(page.states[r["id"]] == engine.State.INSTALLED for r in rows[1:])


def test_tournament_evidence_shows_rejected_slot_and_reason(page: FormatsPage) -> None:
    page.search.setText("Secaucus")
    button(page, "evidence").click()
    text = " ".join(widget.text() for widget in page.scroll.widget().findChildren(QLabel))
    assert (
        "Living Fossil" in text and "2018-06-28" in text and "1 sampled card slot rejected" in text
    )


@pytest.mark.parametrize("status", [GameProcessStatus.RUNNING, GameProcessStatus.UNKNOWN])
def test_open_and_unknown_retry_same_format(
    page: FormatsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, status: GameProcessStatus
) -> None:
    monkeypatch.setattr(engine.settings, "game_process_status", lambda _: status)
    button(page, "single").click()
    qtbot.waitUntil(lambda: page.mode == "error")
    assert not page.ctx.busy
    monkeypatch.setattr(engine.settings, "game_process_status", lambda _: GameProcessStatus.CLOSED)
    button(page, "retry").click()
    ready(page, qtbot)
    assert button(page, "single").text() == "Remove list"


def test_bulk_busy_cancel_and_failed_write_retry(
    page: FormatsPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = engine._publish
    entered, release = Event(), Event()

    def slow(*args: object) -> None:
        entered.set()
        assert release.wait(5)
        real(*args)

    page.manage.click()
    monkeypatch.setattr(engine, "_publish", slow)
    button(page, "era").click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert not page.hidden() and not page.ctx.navigate("cards")
        assert page.ctx.busy
        page.progress.cancel.click()
    finally:
        release.set()
    ready(page, qtbot)
    assert sum(s == engine.State.INSTALLED for s in page.states.values()) == 1
    assert page.hidden() and not page.ctx.busy

    def fail(*_: object) -> None:
        raise PermissionError("Read-only folder")

    monkeypatch.setattr(engine, "_publish", fail)
    button(page, "era").click()
    qtbot.waitUntil(lambda: page.mode == "error")
    monkeypatch.setattr(engine, "_publish", real)
    button(page, "retry").click()
    ready(page, qtbot)
    assert all(
        page.states[r["id"]] == engine.State.INSTALLED
        for r in page.catalogue.select(page.region, page.row["era"])
        if "file" in r
    )
