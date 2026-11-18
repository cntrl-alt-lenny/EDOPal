"""EDOPro music settings and process checks."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from music.processes import (
    GameProcessStatus,
    GameProcessUnknown,
    _linux_game_running,
    game_process_status,
    is_game_running,
)
from state_lock import appearance_lock

__all__ = [
    "GameProcessStatus",
    "GameProcessUnknown",
    "_linux_game_running",
    "game_process_status",
    "is_game_running",
    "music_enabled",
    "music_disabled",
    "write_music_enabled",
    "write_audio_enabled",
]

_game_running = is_game_running

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
