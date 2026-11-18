import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

import pytest

from music import processes, settings


@pytest.mark.parametrize(
    ("image", "comm", "command", "expected"),
    [
        ("/selected/EDOPro (deleted)", "EDOPro", "", True),
        ("/other/EDOPro", "EDOPro", "", False),
        ("/usr/bin/wine64", "wine64", r"Z:\other\EDOPro.exe", False),
        ("/usr/bin/loader", "EDOPro.exe", r"Z:\selected\EDOPro.exe", True),
        ("/app/bin/EDOPro", "EDOPro", "", None),
        ("/usr/bin/wine64", "EDOPro.exe", r"C:\Games\EDOPro.exe", None),
    ],
)
def test_linux_process_listing(
    tmp_path: Path, image: str, comm: str, command: str, expected: bool | None
) -> None:
    pid = tmp_path / "431"
    pid.mkdir()
    (pid / "comm").write_bytes(comm.encode() + b"\n")
    (tmp_path / "self").mkdir()
    (tmp_path / "self/mountinfo").write_bytes(b"28 1 0:4 / /proc rw - proc proc rw\n")
    (pid / "cmdline").write_bytes(command.encode() + b"\0")
    root = Path("/selected").resolve()
    with (
        patch.object(Path, "readlink", lambda p: Path(image) if p.name == "exe" else missing()),
        patch.object(processes.sys, "platform", "linux"),
        patch.object(processes, "_PROC", tmp_path),
    ):
        if expected is None:
            with pytest.raises(OSError, match="whether EDOPro"):
                settings._game_running(root)
        else:
            assert settings._game_running(root) is expected


def missing() -> Path:
    raise FileNotFoundError


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process image query")
def test_windows_image_before_client_startup(tmp_path: Path) -> None:
    image = tmp_path / "EDOPro.exe"
    shutil.copyfile(sys.executable, image)
    # Expose a real process image without running the fixture executable.
    child = subprocess.Popen([str(image)], creationflags=0x4 | subprocess.CREATE_NO_WINDOW)
    try:
        listing = str(child.pid)
        assert (
            processes._listed_processes(tmp_path, listing, "win32")
            == settings.GameProcessStatus.RUNNING
        )
        assert (
            processes._listed_processes(tmp_path / "other", listing, "win32")
            == settings.GameProcessStatus.CLOSED
        )
    finally:
        child.kill()
        child.wait(timeout=5)


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_system_listings(platform: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "selected"
    other = tmp_path / "other"
    if platform == "win32":
        from pathlib import PureWindowsPath

        # On a POSIX test host the native Windows parser is exercised with drive paths.
        if not PureWindowsPath(root).is_absolute():
            root, other = Path("C:/selected"), Path("C:/other")
        paths = [str(other / "EDOPro.exe"), "EDOPro", str(root / "EDOPro.exe")]
        images = dict(enumerate(paths, 100))

        def query_image(pid: int) -> str:
            if pid == 101:
                raise PermissionError("process path denied")
            return images[pid]

        monkeypatch.setattr(processes, "_windows_image", query_image)
        paths = [str(pid) for pid in images]
    else:
        root = Path("/Users/Player/Selected")
        paths = [
            "/Other/YGOPro",
            "/Applications/EDOPro.app/Contents/MacOS/EDOPro",
            str(root / "EDOPro").replace("\\", "/"),
        ]
    monkeypatch.setattr(processes.sys, "platform", platform)
    for listing, expected in [
        (
            [],
            settings.GameProcessStatus.UNKNOWN
            if platform == "darwin"
            else settings.GameProcessStatus.CLOSED,
        ),
        (paths[:1], settings.GameProcessStatus.CLOSED),
        (paths[:2], settings.GameProcessStatus.UNKNOWN),
        (paths, settings.GameProcessStatus.RUNNING),
    ]:
        with patch.object(
            processes.subprocess, "run", return_value=CompletedProcess([], 0, "\n".join(listing))
        ):
            assert settings.game_process_status(root) == expected
            if expected == settings.GameProcessStatus.UNKNOWN:
                with pytest.raises(settings.GameProcessUnknown):
                    settings.is_game_running(root)
    with (
        patch.object(processes.subprocess, "run", side_effect=TimeoutExpired("probe", 8)),
        pytest.raises(settings.GameProcessUnknown),
    ):
        settings._game_running(root)


def test_hidden_process_and_restricted_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = tmp_path / "41"
    pid.mkdir()
    monkeypatch.setattr(processes, "_PROC", tmp_path)
    monkeypatch.setattr(processes.sys, "platform", "linux")
    monkeypatch.setattr(Path, "readlink", lambda _: missing())
    (pid / "comm").write_bytes(b"EDOPro.exe\n")
    with pytest.raises(settings.GameProcessUnknown):
        settings._game_running(tmp_path)
    (pid / "comm").write_bytes(b"other\n")
    (tmp_path / "self").mkdir()
    (tmp_path / "self/mountinfo").write_bytes(b"28 1 0:4 / /proc rw - proc proc rw,hidepid=2\n")
    with pytest.raises(settings.GameProcessUnknown):
        settings._game_running(tmp_path)


def test_flatpak_matching_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "45").mkdir()
    monkeypatch.setattr(processes, "_PROC", tmp_path)
    monkeypatch.setattr(processes.sys, "platform", "linux")
    monkeypatch.setattr(
        Path, "readlink", lambda p: Path("/app/bin/EDOPro") if p.name == "exe" else tmp_path
    )
    assert settings.is_game_running(tmp_path)


def test_proton_prefix_drive_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = tmp_path / "45"
    pid.mkdir()
    (pid / "comm").write_bytes(b"EDOPro.exe\n")
    (pid / "cmdline").write_bytes(b"wine64\0C:\\ProjectIgnis\\EDOPro.exe\0")
    (pid / "environ").write_bytes(b"STEAM_COMPAT_DATA_PATH=/steam/compatdata/42\0")
    drive = Path("/steam/compatdata/42/pfx") / "dosdevices" / "c:"
    native = tmp_path / "drive_c"
    resolve = Path.resolve
    monkeypatch.setattr(
        Path, "resolve", lambda path, **kw: native if path == drive else resolve(path, **kw)
    )
    monkeypatch.setattr(
        Path, "readlink", lambda path: Path("/usr/bin/wine64") if path.name == "exe" else missing()
    )
    assert (
        processes._linux_game_running(native / "ProjectIgnis", tmp_path)
        == settings.GameProcessStatus.RUNNING
    )
    assert (
        processes._linux_game_running(tmp_path / "other", tmp_path)
        == settings.GameProcessStatus.CLOSED
    )
