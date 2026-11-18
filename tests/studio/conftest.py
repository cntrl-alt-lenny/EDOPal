import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

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
        ctx.set_installation(Installation.inspect(game.edopro_path))
        qtbot.addWidget(window)
        window.show()
        window.activateWindow()
        qapp.processEvents()
        windows.append(window)
        return window

    windows: list[StudioWindow] = []
    yield factory
    # HD Cards measures the installation in the background when it is selected.
    # Let that finish before the temporary game folder is removed, so one test's
    # worker can never fail the next test.
    for window in windows:
        qtbot.waitUntil(lambda w=window: not w.ctx.tasks.jobs, timeout=15000)
