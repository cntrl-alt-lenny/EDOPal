import os
from collections.abc import Callable
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
def make_window(qapp: QApplication, qtbot: QtBot, game: Config, monkeypatch) -> Callable:
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
        return window

    return factory
