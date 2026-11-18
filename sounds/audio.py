"""Bounded decoding and immutable audio for effect sets."""

import hashlib
import math
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import miniaudio

from .catalogue import FORMATS

MAX_CLIP_BYTES = 4 * 1024 * 1024
MAX_CLIP_SECONDS = 10
_CHECK_RATE = 44100
_GET_INFO = {
    "wav": miniaudio.wav_get_info,
    "mp3": miniaudio.mp3_get_info,
    "ogg": miniaudio.vorbis_get_info,
    "flac": miniaudio.flac_get_info,
}


@dataclass(frozen=True, slots=True)
class Credits:
    author: str = ""
    license: str = ""
    attribution: str = ""


@dataclass(frozen=True, slots=True)
class Clip:
    format: str
    data: bytes
    duration: float
    sha256: str
    credits: Credits = Credits()

    @property
    def size(self) -> int:
        return len(self.data)


def read_bounded(path: Path, limit: int) -> bytes:
    if not path.is_file():
        raise OSError(f"Expected a regular file: {path.name}.")
    with path.open("rb") as stream:
        if stream.seek(0, 2) > limit:
            raise ValueError(f"{path.name} exceeds the {limit:,}-byte limit.")
        stream.seek(0)
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{path.name} exceeds the {limit:,}-byte limit.")
    return data


def decode_clip(data: bytes, format: str, credits: Credits = Credits()) -> Clip:
    """Verify declared container, metadata and playable samples before accepting bytes."""
    if format not in FORMATS:
        raise ValueError("Choose a WAV, MP3, Ogg Vorbis, or FLAC audio file.")
    if not data or len(data) > MAX_CLIP_BYTES:
        raise ValueError("Sound effects must contain audio and be at most 4 MiB.")
    signatures = {
        "wav": data[:4] in (b"RIFF", b"RF64") and data[8:12] == b"WAVE",
        "mp3": data.startswith(b"ID3")
        or (len(data) >= 2 and data[0] == 255 and data[1] & 224 == 224),
        "ogg": data.startswith(b"OggS"),
        "flac": data.startswith(b"fLaC"),
    }
    if not signatures[format]:
        raise ValueError(f"The file is not decodable {format.upper()} audio.")
    # The format-specific readers run before automatic decoding so an extension
    # mismatch, or a different codec inside an Ogg container, is rejected.
    try:
        info = _GET_INFO[format](data)
        if info.nchannels not in (1, 2) or not 8000 <= info.sample_rate <= 192000:
            raise ValueError("Use mono or stereo audio with a sample rate of 8–192 kHz.")
        if not math.isfinite(info.duration) or info.duration <= 0:
            raise ValueError("The file contains no playable audio.")
        if info.duration > MAX_CLIP_SECONDS:
            raise ValueError(f"Sound effects must be at most {MAX_CLIP_SECONDS} seconds.")
        frames = 0
        with closing(miniaudio.stream_memory(data, nchannels=2, sample_rate=_CHECK_RATE)) as stream:
            for samples in stream:
                frames += len(samples) // 2
                if frames > MAX_CLIP_SECONDS * _CHECK_RATE:
                    raise ValueError(f"Sound effects must be at most {MAX_CLIP_SECONDS} seconds.")
    except (miniaudio.MiniaudioError, ArithmeticError) as exc:
        raise ValueError(f"The file is not decodable {format.upper()} audio.") from exc
    if not frames or frames / _CHECK_RATE < info.duration - 0.05:
        raise ValueError("The file contains no complete, playable audio.")
    return Clip(format, data, info.duration, hashlib.sha256(data).hexdigest(), credits)


def load_clip(path: Path, *, credits: Credits = Credits()) -> Clip:
    """Copy and validate a player-selected audio file."""
    try:
        return decode_clip(read_bounded(path, MAX_CLIP_BYTES), path.suffix[1:].lower(), credits)
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
