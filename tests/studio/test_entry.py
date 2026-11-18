import sys
from unittest.mock import Mock

import pytest

import main
from config import Config
from studio import app


@pytest.mark.parametrize("flags, frozen", [(["--gui"], False), ([], True)])
def test_source_and_double_click_use_studio(
    flags: list[str], frozen: bool, game: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["Studio", "--config", game.config_path, "--edopro-path", game.edopro_path, *flags],
    )
    launch = Mock(return_value=7)
    monkeypatch.setattr(app, "run_app", launch)
    assert main.main() == 7
    cfg, version, run, apply = launch.call_args.args
    assert cfg.edopro_path == game.edopro_path
    assert (version, run, apply) == (main.VERSION, main.run, main._apply_gui_choices)


def test_missing_qt_reports_startup_error(game: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    import desktop_dialogs

    monkeypatch.setattr(
        sys,
        "argv",
        ["Studio", "--gui", "--config", game.config_path, "--edopro-path", game.edopro_path],
    )
    monkeypatch.setitem(sys.modules, "studio.app", None)
    error = Mock()
    monkeypatch.setattr(desktop_dialogs, "show_startup_error", error)
    assert main.main() == 1
    assert "PySide6-Essentials" in error.call_args.args[0]


def test_legacy_flag_keeps_original_window(game: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    import gui

    monkeypatch.setattr(
        sys,
        "argv",
        ["Studio", "--legacy-gui", "--config", game.config_path, "--edopro-path", game.edopro_path],
    )
    launch = Mock(return_value=3)
    monkeypatch.setattr(gui, "run_app", launch)
    assert main.main() == 3
    launch.assert_called_once()


def test_unavailable_legacy_window_reports_error(
    game: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import desktop_dialogs
    import gui

    monkeypatch.setattr(
        sys,
        "argv",
        ["Studio", "--legacy-gui", "--config", game.config_path, "--edopro-path", game.edopro_path],
    )
    monkeypatch.setattr(gui, "run_app", Mock(side_effect=gui.GuiUnavailable("No Tcl runtime")))
    error = Mock()
    monkeypatch.setattr(desktop_dialogs, "show_startup_error", error)
    assert main.main() == 1
    assert "No Tcl runtime" in error.call_args.args[0]
