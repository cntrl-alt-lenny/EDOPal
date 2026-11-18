import gc
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

import main
import tournament_decks
from config import Config
from studio import theme
from studio.context import AppContext, Installation
from studio.shell import StudioWindow
from studio.tasks import RunCoro
from tools.studio_screenshots import DemoSync, create_demo


@pytest.fixture
def game(tmp_path: Path) -> Config:
    return create_demo(tmp_path)


@pytest.fixture
def make_window(
    qapp: QApplication, qtbot: QtBot, game: Config, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable]:
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    theme.apply(qapp)

    def offline(url: str, *, limit: int) -> bytes:
        raise tournament_decks.DeckError("No Format Library response configured for this test.")

    # Other page tests can navigate through Tournament decks on the way to Cards.
    monkeypatch.setattr(tournament_decks, "_fetch", offline)

    def factory(run: RunCoro | None = None) -> StudioWindow:
        ctx = AppContext(game, main.VERSION, run or DemoSync(), main._apply_gui_choices)
        window = StudioWindow(ctx, startup=False)
        windows.append(window)
        ctx.set_installation(Installation.inspect(game.edopro_path))
        window.show()
        window.activateWindow()
        qapp.processEvents()
        # Give tests a settled installation before they edit the synthetic fixture.
        # On Windows an artwork snapshot can still have one of its images open.
        qtbot.waitUntil(lambda: not ctx.tasks.jobs)
        return window

    windows: list[StudioWindow] = []
    yield factory
    # One owner controls shutdown, including any final jobs scheduled by a result.
    while windows:
        window = windows.pop()
        # A failed assertion may leave an edited draft behind.
        monkeypatch.setattr(window.ctx, "confirm", lambda *_: True)
        runner = window.ctx.tasks
        runner.cancel_all()
        if isValid(window):
            window.close()
        qtbot.waitUntil(lambda runner=runner: not runner.jobs, timeout=15000)
        assert runner.pool.waitForDone(15000)
        if isValid(window):
            window.close()
            window.deleteLater()
        window.ctx.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        del runner, window
    # Collect Python cycles containing layout wrappers here, while all workers are idle.
    gc.collect()
