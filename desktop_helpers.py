"""Launch system desktop handlers. Call from a worker, since desktop requests may block."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def desktop_environment() -> dict[str, str]:
    """Give Linux system programs their pre-PyInstaller library search path."""
    env = os.environ.copy()
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def _run(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            env=desktop_environment(),
            timeout=8,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_link(target: str | Path) -> bool:
    """Ask the desktop to open a local path or a web/file URL."""
    if isinstance(target, Path):
        address = str(target.resolve(strict=True))
    elif urlsplit(target).scheme.lower() in {"http", "https", "file"}:
        address = target
    else:
        raise ValueError("Choose a local path or a web link.")
    if sys.platform == "win32":
        try:
            os.startfile(address)
            return True
        except OSError:
            return False
    return _run(["open" if sys.platform == "darwin" else "xdg-open", address])


def reveal_file(path: Path) -> bool:
    """Select a file when possible, otherwise ask the desktop to open its folder."""
    path = path.absolute()
    if not path.is_file():
        raise FileNotFoundError(path)
    if sys.platform == "win32":
        try:
            # Explorer can hand off the request to its existing process and exit with 1.
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
            return True
        except OSError:
            pass
    elif sys.platform == "darwin":
        if _run(["open", "-R", str(path)]):
            return True
    elif _run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.FileManager1",
            "--object-path",
            "/org/freedesktop/FileManager1",
            "--method",
            "org.freedesktop.FileManager1.ShowItems",
            str([path.as_uri()]),
            "",
        ]
    ):
        return True
    return open_link(path.parent)
