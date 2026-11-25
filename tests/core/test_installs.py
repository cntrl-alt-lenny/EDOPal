from pathlib import Path
from unittest.mock import Mock

import pytest

from edopal.core import discovery, launch, process
from edopal.core.install import Install, LaunchTarget, RunState
from edopal.core.installs import RealInstalls
from edopal.core.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "profile" / "settings.json")


@pytest.fixture
def game(tmp_path, monkeypatch):
    root = tmp_path / "Project Ignis"
    root.mkdir()
    target = LaunchTarget(cwd=root, uri="steam://rungameid/13633050659459694592")
    install = Install(root, "steam-shortcut", "41.0.2", target, "Project Ignis (Steam)")

    def lookup(path):
        if Path(path).resolve() == root and root.exists():
            return install
        raise ValueError("No EDOPro installation found")

    monkeypatch.setattr(discovery, "inspect_selected", lookup)
    return install


def test_restore_selection_and_notify_only_on_change(settings, game, tmp_path):
    service = RealInstalls(settings)
    observed = []
    service.subscribe(observed.append)
    assert service.current() is None
    assert service.select(game.root) == game
    assert service.select(game.root / ".") == game
    assert observed == [game]
    assert settings.section("install") == {"root": str(game.root)}
    restored = RealInstalls(Settings(settings.path))
    assert restored.current().launch == game.launch
    with pytest.raises(ValueError):
        service.select(tmp_path / "another")
    assert service.current() == game
    assert observed == [game]


def test_unmounted_saved_root_can_return(settings, game):
    settings.set("install", "root", str(game.root))
    game.root.rmdir()
    service = RealInstalls(settings)
    assert service.current() is None
    assert settings.get("install", "root") == str(game.root)
    game.root.mkdir()
    assert service.select(game.root) == game


def test_failed_save_can_be_retried_without_publishing_the_failed_selection(settings, game, monkeypatch):
    service = RealInstalls(settings)
    changed = Mock()
    service.subscribe(changed)
    with monkeypatch.context() as patch:
        patch.setattr(settings, "_save", Mock(side_effect=OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            service.select(game.root)
    assert service.current() is None
    changed.assert_not_called()
    service.select(game.root)
    changed.assert_called_once_with(game)
    assert RealInstalls(Settings(settings.path)).current() == game


def test_worker_discovery_does_not_change_an_ambiguous_selection(settings, game, tmp_path, monkeypatch):
    other = Install(tmp_path / "Second")
    scan = Mock(return_value=[game, other])
    monkeypatch.setattr(discovery, "discover", scan)
    service = RealInstalls(settings)
    assert service.discover() == [game, other]
    scan.assert_called_once_with([])
    assert service.current() is None
    service.select(game.root)
    assert service.discover() == [game, other]
    scan.assert_called_with([game.root])
    assert service.current() == game


def test_state_when_nothing_selected(settings):
    service = RealInstalls(settings)
    assert service.run_state() is RunState.UNKNOWN
    with pytest.raises(ValueError, match="Choose"):
        service.launch()


def test_running_copy_is_not_dispatched(settings, game, monkeypatch):
    service = RealInstalls(settings)
    service.select(game.root)
    state = Mock(return_value=RunState.RUNNING)
    start = Mock()
    monkeypatch.setattr(process, "run_state", state)
    monkeypatch.setattr(launch, "launch_command", lambda _: ("steam", game.launch.uri))
    monkeypatch.setattr(launch, "_spawn", start)
    assert service.launch() == "EDOPro is already running."
    state.assert_called_once_with(game)
    start.assert_not_called()


@pytest.mark.parametrize(
    ("after", "message"),
    [(RunState.RUNNING, "EDOPro is running."), (RunState.UNKNOWN, "Launch requested through Steam.")],
)
def test_launch_reprobes_selected_copy(settings, game, monkeypatch, after, message):
    service = RealInstalls(settings)
    service.select(game.root)
    query = Mock(side_effect=[RunState.CLOSED, after])
    start = Mock()
    monkeypatch.setattr(process, "run_state", query)
    monkeypatch.setattr(launch, "launch_command", lambda _: ("steam", game.launch.uri))
    monkeypatch.setattr(launch, "_spawn", start)
    assert service.launch() == message
    start.assert_called_once_with(("steam", game.launch.uri), game.root, game.launch.description)
    assert query.call_count == 2


def test_database_only_selection_can_sync(settings, tmp_path, monkeypatch):
    folder = tmp_path / "DB only"
    folder.mkdir()
    (folder / "cards.cdb").write_bytes(b"fixture")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    service = RealInstalls(settings)
    install = service.select(folder)
    assert install.ready
    assert install.launch is None
    with pytest.raises(ValueError, match="no EDOPro launcher"):
        service.launch()
