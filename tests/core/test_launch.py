import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import Mock

import pytest

from edopal.core import launch
from edopal.core.install import Install, LaunchTarget, RunState


@pytest.fixture(autouse=True)
def closed(monkeypatch):
    monkeypatch.setattr(launch.process, "run_state", lambda _: RunState.CLOSED)


@pytest.fixture
def steam_install(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "linux")
    monkeypatch.setattr(launch, "_CONTAINER", tmp_path / ".containerenv")
    monkeypatch.setattr(launch, "_FLATPAK", tmp_path / ".flatpak-info")
    root = tmp_path / "Game Files"
    root.mkdir()
    target = LaunchTarget(cwd=root, uri="steam://rungameid/13633050659459694592", description="Steam")
    return Install(root, "steam-shortcut", launch=target)


def test_native_argv_detached_and_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "linux")
    root = tmp_path / "EDOPro ($1)"
    root.mkdir()
    exe = root / "EDOPro"
    argv = (str(exe), "-C", str(root))
    child = Mock()
    child.wait.return_value = 0
    popen = Mock(return_value=child)
    monkeypatch.setattr(subprocess, "Popen", popen)
    install = Install(root, "native", launch=LaunchTarget(argv, root))
    assert launch.launch(install) == "EDOPro launch requested."
    popen.assert_called_once_with(
        argv,
        cwd=root,
        shell=False,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX session and file descriptor check")
def test_detached_child_observes_its_own_session_and_game_cwd(tmp_path):
    root = tmp_path / "test child ($1)"
    root.mkdir()
    script = tmp_path / "record_child.py"
    script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "print('stdout', flush=True)\n"
        "print('stderr', file=sys.stderr, flush=True)\n"
        "detached = os.getpid() == os.getsid(0)\n"
        "silent = all(os.path.samestat(os.fstat(fd), os.stat(os.devnull)) for fd in (0, 1, 2))\n"
        "Path('pending.json').write_text(json.dumps([sys.argv[1:], detached, silent]))\n"
        "Path('pending.json').replace('observed.json')\n"
    )
    args = (sys.executable, str(script), "-C", str(root))
    # Exercise Popen using a harmless recorder, with no game client or desktop handler.
    launch._spawn(args, root, "test child")
    # _spawn can hand off before Python finishes starting on a loaded CI worker.
    deadline = monotonic() + 5
    observed = root / "observed.json"
    while not observed.exists() and monotonic() < deadline:
        sleep(0.01)
    assert json.loads(observed.read_text()) == [["-C", str(root)], True, True]


@pytest.mark.parametrize(
    ("binaries", "flatpak", "expected"),
    [
        ({"steam"}, False, ("/bin/steam",)),
        ({"xdg-open"}, False, ("/bin/xdg-open",)),
        ({"flatpak", "steam"}, True, ("flatpak", "run", "com.valvesoftware.Steam")),
    ],
)
def test_steam_client_on_linux(steam_install, binaries, flatpak, expected, monkeypatch):
    steam_install.extra["flatpak_steam"] = flatpak
    monkeypatch.setattr(launch.shutil, "which", lambda program: "/bin/" + program if program in binaries else None)
    assert launch.launch_command(steam_install) == (*expected, steam_install.launch.uri)


@pytest.mark.parametrize(
    ("available", "flatpak", "expected"),
    [
        ("distrobox-host-exec", False, ("distrobox-host-exec", "steam")),
        ("host-spawn", False, ("host-spawn", "--no-pty", "steam")),
        ("host-spawn", True, ("host-spawn", "--no-pty", "flatpak", "run", "com.valvesoftware.Steam")),
    ],
)
def test_steam_client_on_host(steam_install, available, flatpak, expected, monkeypatch):
    launch._CONTAINER.touch()
    steam_install.extra["flatpak_steam"] = flatpak
    monkeypatch.setattr(launch.shutil, "which", lambda program: available if program == available else None)
    assert launch.launch_command(steam_install) == (*expected, steam_install.launch.uri)


def test_flatpak_host_portal_and_standalone(steam_install, monkeypatch):
    launch._FLATPAK.touch()
    monkeypatch.setattr(launch.shutil, "which", lambda program: "/bin/flatpak-spawn" if program == "flatpak-spawn" else None)
    assert launch.launch_command(steam_install) == ("/bin/flatpak-spawn", "--host", "steam", steam_install.launch.uri)
    root = steam_install.root
    argv = ("flatpak", "run", "example.EDOPro", "-C", str(root))
    assert launch.launch_command(Install(root, "flatpak", launch=LaunchTarget(argv, root))) == (
        "/bin/flatpak-spawn",
        "--host",
        *argv,
    )


def test_missing_bridge_and_unlaunchable_folder(steam_install, monkeypatch):
    launch._CONTAINER.touch()
    monkeypatch.setattr(launch.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="host-spawn"):
        launch.launch_command(steam_install)
    with pytest.raises(ValueError, match="Steam shortcut"):
        launch.launch_command(Install(steam_install.root, "proton"))
    with pytest.raises(ValueError, match="no EDOPro launcher"):
        launch.launch_command(Install(steam_install.root))


def test_mac_steam_handler_and_bundle_args(steam_install, monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "darwin")
    assert launch.launch_command(steam_install) == (
        "/usr/bin/open",
        "-b",
        "com.valvesoftware.steam",
        steam_install.launch.uri,
    )
    root = steam_install.root
    argv = ("/usr/bin/open", "-a", "/Applications/Project Ignis.app", "--args", "-C", str(root))
    assert launch.launch_command(Install(root, "macos-app", launch=LaunchTarget(argv, root))) == argv


def test_windows_uses_owning_steam_and_detaches(steam_install, monkeypatch):
    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    owner = steam_install.root / "Steam Client"
    owner.mkdir()
    steam = owner / "steam.exe"
    steam.touch()
    steam_install.extra["steam_root"] = str(owner)
    fake = Mock()
    fake.return_value.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", fake)
    assert launch.launch(steam_install) == "Launch requested through Steam."
    args, kwargs = fake.call_args
    assert args == ((str(steam), steam_install.launch.uri),)
    assert kwargs["creationflags"] == 520
    assert kwargs["shell"] is False
    assert "start_new_session" not in kwargs


def test_failed_handoff_reports_exit_status(steam_install, monkeypatch):
    launch._CONTAINER.touch()
    monkeypatch.setattr(launch.shutil, "which", lambda name: name)
    child = Mock()
    child.wait.return_value = 127
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=child))
    with pytest.raises(RuntimeError, match="Steam exited with status 127"):
        launch.launch(steam_install)


@pytest.mark.parametrize("uri", ["file:///tmp/game", "steam://rungameid/123 --options", f"steam://rungameid/{2**64}"])
def test_only_dispatch_steam_game_id_uris(uri):
    with pytest.raises(ValueError, match="Invalid Steam"):
        launch.launch_command(Install(Path("unused"), launch=LaunchTarget(uri=uri)))
