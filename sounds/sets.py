"""Bundled, installed and player-mixed sound sets."""

import math
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from zipfile import ZipFile

import miniaudio

import extras

from .audio import Clip, Credits, decode_clip, load_clip
from .catalogue import CHANT_TYPES, EVENT_BY_STEM, FORMATS, chant_key, cue_filename, event_for

_THONK_CREDITS = Credits(
    "Kenney",
    "CC0 1.0",
    "Adapted from Kenney's Casino Audio, Interface Sounds and UI Audio. "
    "https://kenney.nl/assets/category:Audio",
)
_SYNTH_CREDITS = Credits("Unknown", "Undocumented", "Bundled with EDOPro HD Sync.")
_PASSCODE_PREFIX = re.compile(r"^[ \t\r\n\v\f]*([+-]?[0-9]+)")
_ULONG_MAX = 0xFFFFFFFF if sys.platform == "win32" else 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True, slots=True)
class SoundSet:
    """An absent key keeps the installed cue; a present None explicitly silences it."""

    name: str
    assignments: Mapping[str, Clip | None] = field(default_factory=dict)
    credits: Credits = Credits()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Give this sound set a name.")
        assignments = dict(self.assignments)
        for key, clip in assignments.items():
            event = event_for(key)
            if clip is not None and (
                not isinstance(clip, Clip) or clip.format not in event.formats
            ):
                raise ValueError(f"Invalid sound for {event.name}.")
        object.__setattr__(self, "assignments", MappingProxyType(assignments))

    def with_assignment(self, key: str, clip: Clip | None) -> "SoundSet":
        return replace(self, assignments={**self.assignments, key: clip})

    def without_assignment(self, key: str) -> "SoundSet":
        event_for(key)
        assignments = dict(self.assignments)
        assignments.pop(key, None)
        return replace(self, assignments=assignments)

    def pick(self, key: str) -> Clip | None:
        """Copy one choice, retaining the source set's credits when inherited."""
        clip = self.assignments[key]
        if clip is not None and clip.credits == Credits():
            clip = replace(clip, credits=self.credits)
        return clip


@dataclass(frozen=True, slots=True)
class InstalledCue:
    key: str
    paths: tuple[Path, ...]
    clip: Clip | None = None
    error: str = ""
    size: int | None = None
    duration: float | None = None

    @property
    def path(self) -> Path | None:
        """The first file EDOPro will try, even if it cannot be decoded."""
        return self.paths[0] if self.paths else None


def bundled_files(name: str) -> dict[str, bytes]:
    """Read only the root audio members, including the shared ZIP's aliases."""
    if name == "Thonk":
        with ZipFile(extras.PACKS / "shared.zip") as archive:
            return {
                key: archive.read(member)
                for key, member in extras._pack_sources(archive, "sound/").items()
            }
    if name == "Synth":
        return {
            path.relative_to(extras.ASSETS).as_posix(): path.read_bytes()
            for path in (extras.ASSETS / "sound").glob("*.wav")
        }
    raise ValueError(f"Unknown bundled sound set: {name!r}.")


def bundled_sets() -> tuple[SoundSet, ...]:
    return tuple(
        SoundSet(
            name,
            {
                Path(filename).stem: decode_clip(data, Path(filename).suffix[1:])
                for filename, data in bundled_files(name).items()
            },
            credits,
        )
        for name, credits in (("Thonk", _THONK_CREDITS), ("Synth", _SYNTH_CREDITS))
    )


def chant_candidates(sound: Path) -> Iterator[tuple[str, Path]]:
    """Read the client's sorted chant candidates, including hand-named aliases."""
    for kind in CHANT_TYPES:
        folder = sound / kind
        if folder.resolve() != folder or not folder.is_dir():
            continue
        for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
            if path.suffix[1:].lower() not in FORMATS or not path.is_file():
                continue
            match = _PASSCODE_PREFIX.match(path.stem)
            if match:
                value = int(match[1])
                if abs(value) <= _ULONG_MAX and (code := value & 0xFFFFFFFF):
                    yield chant_key(kind, code), path


def scan_installation(game: Path) -> tuple[InstalledCue, ...]:
    """Read every root cue slot and the discovered per-card chant slots."""
    root = game.resolve(strict=True)
    sound = root / "sound"
    if sound.resolve() != sound:
        raise OSError("The installation's sound folder is a symbolic link.")
    names = {item.name: item for item in sound.iterdir()} if sound.is_dir() else {}
    folded = {name.casefold(): path for name, path in names.items()}
    candidates: dict[str, list[Path]] = {}
    for key in EVENT_BY_STEM:
        candidates[key] = []
        for format in FORMATS:
            name = cue_filename(key, format)
            if (sound / name).is_file():
                candidates[key].append(names.get(name, folded.get(name, sound / name)))
    for key, path in chant_candidates(sound):
        candidates.setdefault(key, []).append(path)
    rows = []
    for key, paths in candidates.items():
        if not paths:
            rows.append(InstalledCue(key, ()))
            continue
        primary, *_ = paths
        size = duration = None
        try:
            extras._regular_path(root, primary.relative_to(root))
            size = primary.stat().st_size
            clip = load_clip(primary)
        except (OSError, ValueError) as exc:
            if size:
                try:
                    reported = miniaudio.get_file_info(str(primary)).duration
                    if math.isfinite(reported) and reported > 0:
                        duration = reported
                except (miniaudio.MiniaudioError, OSError, ValueError, ArithmeticError):
                    pass
            rows.append(
                InstalledCue(key, tuple(paths), error=str(exc), size=size, duration=duration)
            )
        else:
            rows.append(
                InstalledCue(key, tuple(paths), clip, size=clip.size, duration=clip.duration)
            )
    return tuple(rows)


def installed_set(rows: tuple[InstalledCue, ...]) -> SoundSet:
    """Create an auditionable snapshot from a scan, retaining known bundled credits."""
    known = {
        (key, clip.sha256): sound_set.pick(key)
        for sound_set in bundled_sets()
        for key, clip in sound_set.assignments.items()
        if clip is not None
    }
    return SoundSet(
        "What you have now",
        {row.key: known.get((row.key, row.clip.sha256), row.clip) for row in rows if row.clip},
        Credits(attribution="Audio from the selected installation. See sound/CREDITS.md.txt."),
    )
