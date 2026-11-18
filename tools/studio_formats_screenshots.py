"""Capture Formats through its real controls against the renderer's disposable installation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from unittest.mock import patch

from formats import installation as engine
from formats.catalogue import marked
from music.settings import GameProcessStatus
from studio import theme
from studio.context import Installation
from studio.pages.formats import FormatsPage
from studio.pages.formats_views import DATA
from studio.shell import StudioWindow
from studio.widgets import Button


def render_formats(
    window: StudioWindow,
    selected: Installation,
    shot: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
    *,
    overview: str | None = None,
) -> None:
    window.navigate("formats")
    page: FormatsPage = window.pages["formats"]

    def ready() -> None:
        settle(lambda: page.loaded and not window.ctx.tasks.jobs)

    def capture(name: str) -> None:
        page.slide.setCurrentTime(theme.MOTION_MS)
        shot("formats-" + name)

    def press(name: str) -> None:
        page.scroll.widget().findChild(Button, name).click()

    def pick(name: str) -> None:
        for i in range(page.list.count()):
            item = page.list.item(i)
            if (row := item.data(DATA)) and row["name"] == name:
                page.list.setCurrentItem(item)
                return
        raise ValueError(name)

    ready()
    if overview:
        shot(overview)
        return
    capture("01-catalogue")
    if window.width() <= theme.MIN_SIZE[0]:
        return
    press("host")
    capture("02-host")
    page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
    capture("03-host-switches")
    page.escape()
    ready()
    with patch.object(
        engine.settings, "game_process_status", return_value=GameProcessStatus.CLOSED
    ) as process:
        press("single")
        ready()
        capture("04-installed")
        path = Path(selected.path) / "lflists" / page.row["file"]
        data = path.read_bytes()
        path.write_bytes(marked(page.row["id"], b"!Previous list\n$whitelist\n1 3\n"))
        page.receipt = ""
        page.refresh()
        ready()
        capture("05-update")
        path.write_bytes(data + b"# My changes\n")
        page.refresh()
        ready()
        capture("06-edited")
        path.write_bytes(data)
        pick("Goat")
        page.refresh()
        ready()
        capture("07-goat")
        page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
        capture("08-historical-versions")
        for name, status in (
            ("09-game-open", GameProcessStatus.RUNNING),
            ("10-game-unknown", GameProcessStatus.UNKNOWN),
        ):
            process.return_value = status
            press("single")
            settle(lambda: page.mode == "error")
            capture(name)
            page.escape()
            ready()
        process.return_value = GameProcessStatus.CLOSED
        with patch.object(
            engine, "_publish", side_effect=PermissionError("The list folder is read-only.")
        ):
            press("single")
            settle(lambda: page.mode == "error")
            capture("11-write-error")
        page.escape()
        ready()
        page.manage.click()
        capture("12-manage")
        gate = Event()
        real = engine._publish
        calls = 0

        def pause(*args: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                gate.wait(10)
            real(*args)

        with patch.object(engine, "_publish", pause):
            press("all")
            try:
                settle(lambda: page.progress.bar.value() >= 3)
                capture("13-progress")
                page.progress.cancel.click()
            finally:
                gate.set()
            ready()
        capture("14-cancelled")
        press("undo")
        ready()
        capture("24-undo")
        page.escape()
        ready()
    pick("Genesys")
    capture("15-unsupported")
    pick("Secaucus")
    press("evidence")
    capture("20-deck-evidence")
    page.escape()
    ready()
    pick("Despia")
    capture("21-no-samples")
    pick("TeleDAD")
    page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
    capture("22-pool-boundary")
    pick("Traditional")
    capture("23-rolling-format")
    page.search.setText("no matching format")
    capture("16-no-results")
    page.search.clear()
    page.region_buttons["OCG"].click()
    pick("Tokyo Dome")
    capture("17-ocg")
    for name, installation in (
        ("18-no-installation", Installation()),
        ("19-awaiting-databases", Installation(selected.path)),
    ):
        window.ctx.set_installation(installation)
        ready()
        capture(name)
