import os
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from edopal.core import process
from edopal.core.install import Install, LaunchTarget, RunState


@pytest.fixture
def proc(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Synthetic /proc uses POSIX paths and symlinks")
    table = tmp_path / "proc"
    (table / "self").mkdir(parents=True)
    (table / "self/mountinfo").write_text(f"28 1 0:4 / {table} rw - proc proc rw\n")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(process, "_PROC", table)
    monkeypatch.setattr(process, "_CONTAINER", tmp_path / "containerenv")
    monkeypatch.setattr(process, "_HOST_PROC", tmp_path / "host-proc")
    return table


def pid(table, comm, *, image=None, cwd=None, args=()):
    directory = table / str(len(list(table.iterdir())) + 10)
    directory.mkdir(parents=True)
    (directory / "comm").write_text(comm + "\n")
    (directory / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in args) + b"\0")
    if image:
        (directory / "exe").symlink_to(image)
    if cwd:
        (directory / "cwd").symlink_to(cwd)
    return directory


@pytest.mark.parametrize(
    ("comm", "image", "cwd", "args", "expected"),
    [
        ("EDOPro", "/games/My Game/EDOPro (deleted)", None, (), RunState.RUNNING),
        ("EDOPro", "/games/other/EDOPro", "/games/My Game", (), RunState.RUNNING),
        ("EDOPro", "/games/other/EDOPro", "/games/other", (), RunState.CLOSED),
        ("EDOPro", "/games/My Game/backup/EDOPro", "/games/My Game/backup", (), RunState.CLOSED),
        ("EDOPro", "/games/other/EDOPro", None, (), RunState.UNKNOWN),
        ("EDOPro", "/games/other/EDOPro", None, ("./EDOPro", "-C", "/games/My Game"), RunState.RUNNING),
        ("EDOPro", "/games/My Game/EDOPro", "/games/other", ("EDOPro", "-C", "/games/other"), RunState.CLOSED),
        ("wine64", "/bin/wine64", None, ("wine", r"Z:\games\My Game\EDOPro.exe"), RunState.RUNNING),
        ("EDOPro.exe", "/bin/loader", None, ("/games/My Game/EDOPro.exe",), RunState.RUNNING),
        ("wine64", "/bin/wine64", None, ("wine", r"Z:\games\other\EDOPro.exe"), RunState.CLOSED),
        ("EDOPro.exe", "/bin/wine64", None, (r"C:\unmapped\EDOPro.exe",), RunState.UNKNOWN),
        ("EDOPro.exe", None, None, ("./EDOPro.exe",), RunState.UNKNOWN),
        ("EDOPro", "/app/bin/EDOPro", None, (), RunState.UNKNOWN),
        ("EDOPro", None, None, (), RunState.UNKNOWN),
        ("nano", "/bin/nano", "/games/My Game", ("/games/My Game/EDOPro",), RunState.CLOSED),
    ],
)
def test_linux_identity(proc, comm, image, cwd, args, expected):
    pid(proc, comm, image=image, cwd=cwd, args=args)
    assert process.run_state(Install(Path("/games/My Game"))) is expected


def test_linux_matches_split_executable_when_cwd_hidden(proc):
    pid(proc, "EDOPro", image="/opt/Engine/EDOPro")
    game = Install(Path("/games/data"), "native", launch=LaunchTarget(("/opt/Engine/EDOPro", "-C", "/games/data")))
    assert process.run_state(game) is RunState.RUNNING


def test_wine_editor_argument_is_not_a_running_client(proc):
    pid(proc, "notepad.exe", image="/bin/wine64", args=("wine", "notepad.exe", r"Z:\Games\EDOPro.exe"))
    assert process.run_state(Install(Path("/Games"))) is RunState.CLOSED


def test_opaque_unrelated_wine_guest(proc):
    guest = pid(proc, "services.exe", image="/usr/bin/wine64-preloader")
    (guest / "cmdline").unlink()
    assert process.run_state(Install(Path("/Games"))) is RunState.CLOSED
    (guest / "comm").write_text("wine64")
    assert process.run_state(Install(Path("/Games"))) is RunState.UNKNOWN


def test_exited_game_waiting_for_parent_is_closed(proc):
    process_dir = pid(proc, "EDOPro", args=("./EDOPro",))
    (process_dir / "stat").write_bytes(b"15 (EDOPro) Z 1 15 1\n")
    assert process.run_state(Install(Path("/Games"))) is RunState.CLOSED


@pytest.mark.parametrize("source", ["WINEPREFIX", "STEAM_COMPAT_DATA_PATH"])
def test_wine_prefix_drive_mapping(proc, tmp_path, source):
    root = tmp_path / "second drive/Game"
    root.mkdir(parents=True)
    prefix = tmp_path / "compatdata/123/pfx"
    mappings = prefix / "dosdevices"
    mappings.mkdir(parents=True)
    (mappings / "d:").symlink_to(root.parent)
    process_dir = pid(proc, "wine64-preloader", image="/bin/wine", args=("wine", r"D:\Game\EDOPro.exe"))
    value = prefix.parent if source == "STEAM_COMPAT_DATA_PATH" else prefix
    (process_dir / "environ").write_bytes(os.fsencode(f"{source}={value}\0"))
    assert process.run_state(Install(root)) is RunState.RUNNING
    assert process.run_state(Install(tmp_path / "other")) is RunState.CLOSED


def test_wine_data_root_with_different_case_and_unreadable_cwd(proc, tmp_path):
    root = tmp_path / "drive_c/ProjectIgnis"
    root.mkdir(parents=True)
    mappings = tmp_path / "dosdevices"
    mappings.mkdir()
    (mappings / "c:").symlink_to(root.parent)
    directory = pid(proc, "wine", image="/bin/wine", args=("wine", r"C:\Bin\EDOPro.exe", "-C", r"C:\projectignis"))
    (directory / "environ").write_bytes(os.fsencode(f"WINEPREFIX={tmp_path}\0"))
    assert process.run_state(Install(root)) is RunState.UNKNOWN


def test_incomplete_namespace_requires_host_visibility(proc):
    pid(proc, "other", image="/bin/other")
    game = Install(Path("/games/EDOPro"))
    process._CONTAINER.touch()
    assert process.run_state(game) is RunState.UNKNOWN
    table = process._HOST_PROC
    table.mkdir()
    with (proc / "self/mountinfo").open("a") as stream:
        stream.write(f"29 1 0:5 / {table} rw - proc proc rw\n")
    pid(table, "other", image="/usr/bin/other")
    assert process.run_state(game) is RunState.CLOSED
    pid(table, "EDOPro", image=game.root / "EDOPro")
    assert process.run_state(game) is RunState.RUNNING


def test_restricted_proc_mount(proc):
    pid(proc, "other", image="/bin/other")
    (proc / "self/mountinfo").write_text(f"28 1 0:4 / {proc} rw - proc proc rw,hidepid=2\n")
    assert process.run_state(Install(Path("/games/EDOPro"))) is RunState.UNKNOWN


def row(image, pid):
    return f'"{image}","{pid}","Services","1","3,432 K"\r\n'.encode("ascii")


def test_windows_tasklist_and_full_image(monkeypatch):
    windows = Path("C:/Games/selected")
    executable = Path("C:/Program Files/Ignis/EDOPro.exe")
    game = Install(windows, "windows", extra={"executable": str(executable)})
    own = os.getpid()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    images = {own + 1: str(windows.parent / "other/EDOPro.exe"), own + 3: str(executable)}

    def image(pid):
        if pid not in images:
            raise PermissionError
        return images[pid]

    monkeypatch.setattr(process, "_windows_image", image)
    closed = row("python.exe", own) + row("EDOPro.exe", own + 1)
    unknown = closed + row("YGOPro.exe", own + 2)
    for listing, expected in [
        (b"", RunState.UNKNOWN),
        (closed, RunState.CLOSED),
        (closed[:-2], RunState.UNKNOWN),
        (closed + b"INFO: Enumeration failed\r\n", RunState.UNKNOWN),
        (closed + row("x\ny.exe", own + 2), RunState.UNKNOWN),
        (row("EDOPro.exe", own + 1), RunState.UNKNOWN),
        (unknown, RunState.UNKNOWN),
        (unknown + row("EDOPro.exe", own + 3), RunState.RUNNING),
    ]:

        def tasklist(argv, *, _listing=listing, **kwargs):
            assert argv[0] == r"C:\Windows\System32\tasklist.exe"
            return CompletedProcess(argv, 0, _listing, b"")

        monkeypatch.setattr(subprocess, "run", tasklist)
        assert process.run_state(game) is expected

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: CompletedProcess(argv, 0, closed, b""))
    assert process.run_state(Install(windows)) is RunState.UNKNOWN


@pytest.mark.parametrize(
    ("executable", "image"),
    [
        ("C:/Games/One/EDOPro.exe", r"\\?\C:\Games\ONE\EDOPro.exe"),
        ("//Server/share/Project Ignis/EDOPro.exe", r"\\?\UNC\SERVER\share\Project Ignis\EDOPro.exe"),
    ],
)
def test_windows_image_aliases(monkeypatch, executable, image):
    monkeypatch.setattr(sys, "platform", "win32")
    # Exercise path spellings offline, including on Windows without the UNC share.
    monkeypatch.setattr(os.path, "realpath", os.fspath)
    own_pid = os.getpid()
    listing = row("python.exe", own_pid) + row("EDOPro.exe", own_pid + 1)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: CompletedProcess(argv, 0, listing, b""))
    monkeypatch.setattr(process, "_windows_image", lambda _: image)
    game = Install(Path("C:/data"), "windows", extra={"executable": executable})
    assert process.run_state(game) is RunState.RUNNING


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_system_probe_timeout(monkeypatch, platform, tmp_path):
    monkeypatch.setattr(sys, "platform", platform)

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    assert process.run_state(Install(tmp_path)) is RunState.UNKNOWN


@pytest.mark.skipif(os.name == "nt", reason="macOS ps emits POSIX paths")
def test_mac_bundle_uses_executable_from_plist(monkeypatch):
    root = Path("/Users/One/Library/Application Support/ProjectIgnis")
    bundle = Path("/Applications/Games/Project Ignis.app")
    executable = bundle / "Contents/MacOS/Ignis Client"
    selected = Install(root, "macos-app", extra={"executable": str(executable), "bundle": str(bundle)})
    monkeypatch.setattr(sys, "platform", "darwin")
    for listing, expected in [
        ("", RunState.UNKNOWN),
        ("/bin/ps\n/elsewhere/Project Ignis.app/Contents/MacOS/Ignis Client", RunState.CLOSED),
        ("Ignis Client\n", RunState.UNKNOWN),
        (f"/bin/ps\n{executable}\n", RunState.RUNNING),
    ]:
        monkeypatch.setattr(
            subprocess, "run", lambda argv, *, _listing=listing, **kw: CompletedProcess(argv, 0, _listing, "")
        )
        assert process.run_state(selected) is expected
