"""EDOPro music settings and process checks."""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from music.discovery import _sound_path
from state_lock import locked

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


def _game_running(game: Path) -> bool:
    """Match the executable path so an unrelated portable installation can remain open."""
    root = game.resolve()
    if sys.platform.startswith("linux"):
        executables = []
        for proc in Path("/proc").iterdir():
            if proc.name.isdigit():
                with contextlib.suppress(OSError):
                    executables.append((proc / "exe").readlink())
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
                "Could not check whether EDOPro is open. Change Music in EDOPro’s audio settings instead."
            ) from exc
        executables = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return any(
        exe.stem.casefold() in {"edopro", "ygopro"}
        and (not exe.is_absolute() or exe.resolve().is_relative_to(root))
        for exe in executables
    )


def write_music_enabled(game: Path, enabled: bool) -> None:
    """Change only the existing enable_music bit, preserving EDOPro's volume and file bytes."""
    root = game.resolve(strict=True)
    conf = root / "config" / "system.conf"
    if not conf.resolve().is_relative_to(root) or conf.is_symlink():
        raise OSError("The EDOPro settings file points outside the selected installation.")
    with locked(_sound_path(game, ".edopro-hd-sync"), ".audio-settings.lock"):
        original = conf.read_bytes()
        matches = list(_ENABLE_MUSIC.finditer(original))
        if not matches:
            raise ValueError(
                "Set Music in EDOPro’s audio settings. This configuration has no recognized enable_music setting."
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
