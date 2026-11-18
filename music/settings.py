"""EDOPro music settings and process checks."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from state_lock import appearance_lock

_ENABLE_MUSIC = re.compile(
    rb"(?m)^(?:\xef\xbb\xbf)?[ \t]*enable_music[ \t]*=[ \t]*([01])(?=[ \t]*(?:[#;][^\r\n]*)?\r?$)"
)


def music_enabled(game: Path) -> bool | None:
    try:
        conf = (game / "config" / "system.conf").read_bytes()
    except OSError:
        return None
    matches = list(_ENABLE_MUSIC.finditer(conf))
    return matches[-1][1] == b"1" if matches else None


def music_disabled(game: Path) -> bool:
    return music_enabled(game) is False


def _linux_game_running(root: Path, processes: Path) -> bool:
    for proc in processes.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            exe = (proc / "exe").readlink()
        except OSError:
            # A sandbox may hide the path while still reporting the process name.
            with contextlib.suppress(OSError):
                if (proc / "comm").read_bytes().strip().lower() in {
                    b"edopro",
                    b"ygopro",
                    b"edopro.exe",
                    b"ygopro.exe",
                }:
                    return True
            continue
        if exe.stem.casefold() in {"edopro", "ygopro"} and exe.resolve().is_relative_to(root):
            return True
        if exe.name.startswith("wine"):
            # Proton may expose the Wine loader as /proc/PID/exe. Its Windows
            # drive letters cannot reliably be compared with a native game path.
            with contextlib.suppress(OSError), (proc / "cmdline").open("rb") as stream:
                for arg in stream.read(8192).split(b"\0"):
                    if PureWindowsPath(os.fsdecode(arg)).name.lower() in {
                        "edopro.exe",
                        "ygopro.exe",
                    }:
                        return True
    return False


def _game_running(game: Path) -> bool:
    """Match the executable path so an unrelated portable installation can remain open."""
    root = game.resolve()
    if sys.platform.startswith("linux"):
        return _linux_game_running(root, Path("/proc"))
    else:
        if sys.platform == "win32":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
                "Get-Process -Name EDOPro,ygopro -ErrorAction SilentlyContinue | "
                "ForEach-Object {if ($_.Path) {$_.Path} else {$_.ProcessName}}; exit 0",
            ]
        else:
            command = ["ps", "-axo", "comm="]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError(
                "Could not check whether EDOPro is open. Close the game and try again."
            ) from exc
        executables = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return any(
        exe.stem.casefold() in {"edopro", "ygopro"}
        and (not exe.is_absolute() or exe.resolve().is_relative_to(root))
        for exe in executables
    )


def write_music_enabled(game: Path, enabled: bool) -> None:
    """Change only the existing enable_music bit, preserving EDOPro's volume and file bytes."""
    write_audio_enabled(game, enabled, _ENABLE_MUSIC, "enable_music")


def write_audio_enabled(game: Path, enabled: bool, pattern: re.Pattern[bytes], key: str) -> None:
    """Flip one existing 0/1 audio switch in system.conf, leaving every other byte alone."""
    root = game.resolve(strict=True)
    conf = root / "config" / "system.conf"
    if not conf.resolve().is_relative_to(root) or conf.is_symlink():
        raise OSError("The EDOPro settings file points outside the selected installation.")
    with appearance_lock(root):
        original = conf.read_bytes()
        matches = list(pattern.finditer(original))
        if not matches or matches[-1][1] not in (b"0", b"1"):
            raise ValueError(
                f"This configuration has no recognized {key} switch. Use EDOPro’s audio settings."
            )
        match = matches[-1]
        value = b"1" if enabled else b"0"
        if match[1] == value:
            return
        with tempfile.NamedTemporaryFile(
            dir=conf.parent, prefix=".audio-setting-", delete=False
        ) as temp:
            staged = Path(temp.name)
        try:
            staged.write_bytes(original[: match.start(1)] + value + original[match.end(1) :])
            shutil.copymode(conf, staged)
            if conf.read_bytes() != original:
                raise OSError("EDOPro settings changed during the update. Please try again.")
            staged.replace(conf)
        finally:
            staged.unlink(missing_ok=True)
