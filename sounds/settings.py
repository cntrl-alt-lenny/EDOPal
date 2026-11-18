"""Read the installed client's effects settings and change its existing switch."""

import re
from dataclasses import dataclass
from pathlib import Path

from music_library import _set_audio_enabled

_ENABLE = re.compile(rb"(?m)^enable_sound[ \t]=[^\r\n][ \t]*([+-]?[0-9]+)")
_VOLUME = re.compile(rb"(?m)^sound_volume[ \t]=[^\r\n][ \t]*([+-]?[0-9]+)")
_BACKEND = re.compile(rb"(?m)^sound_backend[ \t]=[^\r\n]([^\r\n]*)")


@dataclass(frozen=True, slots=True)
class SoundSettings:
    enabled: bool | None
    volume: int | None
    backend: str | None


def _setting(content: bytes, pattern: re.Pattern[bytes], default: int) -> int:
    for match in pattern.finditer(content):
        try:
            value = int(match[1])
        except ValueError:
            continue
        if -(2**31) <= value < 2**31:
            default = value
    return default


def sound_settings(game: Path) -> SoundSettings:
    """Read the values as EDOPro 41.0.2 loads them, including its defaults."""
    try:
        content = (game / "config/system.conf").read_bytes()
    except FileNotFoundError:
        content = b""
    except OSError:
        return SoundSettings(None, None, None)
    enabled = bool(_setting(content, _ENABLE, 1))
    volume = min(100, max(0, _setting(content, _VOLUME, 20)))
    backends = _BACKEND.findall(content)
    backend = backends[-1].lower().decode("ascii", errors="replace") if backends else "default"
    if backend not in {"miniaudio", "sfml", "sdl", "irrklang", "none"}:
        backend = "default"
    return SoundSettings(enabled, volume, backend)


def set_sound_enabled(game: Path, enabled: bool) -> None:
    """Flip the existing 0/1 switch while EDOPro is closed, preserving volume."""
    _set_audio_enabled(game, enabled, _ENABLE, "sound effects", "enable_sound")
