"""The versioned sound-effects ZIP format and the player's local set library."""

import json
import re
import stat
import struct
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile
from zlib import error as DeflateError

import config
import extras

from .audio import MAX_CLIP_BYTES, Credits, decode_clip, read_bounded
from .catalogue import event_for
from .sets import SoundSet

PACK_KIND = "sound-effects"
SET_SUFFIX = ".edosounds"
MAX_ASSIGNMENTS = 256
MAX_AUDIO_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ARCHIVE_BYTES = MAX_AUDIO_BYTES + 512 * 1024
_MAX_CENTRAL_BYTES = (MAX_ASSIGNMENTS + 1) * 512
_ZIP_END = struct.Struct("<4s4H2IH")
_SET_ID = re.compile(r"[0-9a-f]{32}")
_CREDIT_FIELDS = {"author", "license", "attribution"}


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("The sound set manifest contains duplicate keys.")
    return result


def _text(value: object, limit: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(char) < 32 and char not in "\n\r\t" for char in value)
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError(f"Invalid sound set {field}.")
    return value


def _credits(value: object) -> Credits:
    if not isinstance(value, dict) or value.keys() - _CREDIT_FIELDS:
        raise ValueError("Invalid sound set credits.")
    return Credits(
        _text(value.get("author", ""), 200, "author"),
        _text(value.get("license", ""), 200, "license"),
        _text(value.get("attribution", ""), 2000, "attribution"),
    )


def _audio_member(key: str, format: str) -> str:
    if format not in event_for(key).formats:
        raise ValueError(f"Invalid audio format for {key}.")
    return f"audio/{key}.{format}"


def _check_directory(data: bytes) -> None:
    # A bounded, single-disk ZIP footer prevents ZipFile from parsing an
    # attacker-sized directory before we can check its declared members.
    if len(data) < _ZIP_END.size:
        raise ValueError("This file is not a sound set ZIP archive.")
    magic, disk, central_disk, disk_count, count, size, offset, comment = _ZIP_END.unpack_from(
        data, len(data) - _ZIP_END.size
    )
    if (
        magic != b"PK\x05\x06"
        or disk
        or central_disk
        or comment
        or disk_count != count
        or not 1 <= count <= MAX_ASSIGNMENTS + 1
        or size > _MAX_CENTRAL_BYTES
        or offset + size != len(data) - _ZIP_END.size
    ):
        raise ValueError("The sound set ZIP directory is invalid or exceeds its limits.")


def _members(archive: ZipFile) -> set[str]:
    names = set()
    audio_bytes = 0
    for member in archive.infolist():
        name = member.orig_filename
        if (
            name in names
            or not name.isascii()
            or name != member.filename
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or member.is_dir()
            or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
            or member.flag_bits & 1
            or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
        ):
            raise ValueError(f"Unsafe or duplicate sound set member: {name!r}.")
        names.add(name)
        if name == "manifest.json":
            if member.file_size > _MAX_MANIFEST_BYTES:
                raise ValueError("The sound set manifest exceeds 64 KiB.")
        else:
            audio_bytes += member.file_size
            if member.file_size > MAX_CLIP_BYTES or audio_bytes > MAX_AUDIO_BYTES:
                raise ValueError("The sound set exceeds its audio size limits.")
    if "manifest.json" not in names or len(names) > MAX_ASSIGNMENTS + 1:
        raise ValueError("The sound set needs a manifest and at most 256 audio files.")
    return names


def _read_set(archive: ZipFile) -> SoundSet:
    names = _members(archive)
    with archive.open("manifest.json") as stream:
        manifest = json.loads(stream.read().decode("utf-8"), object_pairs_hook=_object)
    if (
        not isinstance(manifest, dict)
        or manifest.keys() - {"kind", "version", "name", "credits", "events"}
        or manifest.get("kind") != PACK_KIND
        or type(manifest.get("version")) is not int
        or manifest["version"] != 1
        or not isinstance(manifest.get("events"), dict)
        or len(manifest["events"]) > MAX_ASSIGNMENTS
    ):
        raise ValueError("Expected a version 1 sound-effects manifest with up to 256 assignments.")
    name = _text(manifest.get("name"), 120, "name")
    credits = _credits(manifest.get("credits", {}))
    assignments = {}
    names.remove("manifest.json")
    for key, entry in manifest["events"].items():
        event_for(key)
        if entry is None:
            assignments[key] = None
            continue
        if not isinstance(entry, dict) or entry.keys() - {"format", "credits"}:
            raise ValueError(f"Invalid sound set assignment for {key}.")
        format = _text(entry.get("format"), 4, "format")
        member = _audio_member(key, format)
        if member not in names:
            raise ValueError(f"The sound set is missing {member}.")
        try:
            assignments[key] = decode_clip(
                archive.read(member), format, _credits(entry.get("credits", {}))
            )
        except ValueError as exc:
            raise ValueError(f"{member}: {exc}") from exc
        names.remove(member)
    if names:
        raise ValueError(f"Unexpected sound set member: {min(names)!r}.")
    return SoundSet(name, assignments, credits)


def read_set(path: Path) -> SoundSet:
    """Read an untrusted ZIP in memory, without extracting any of its paths."""
    data = read_bounded(path, _MAX_ARCHIVE_BYTES)
    _check_directory(data)
    try:
        with ZipFile(BytesIO(data)) as archive:
            return _read_set(archive)
    except (BadZipFile, DeflateError, RuntimeError, EOFError, UnicodeError) as exc:
        raise ValueError(f"Invalid sound set archive: {path.name}.") from exc


def load_pack(path: Path, *, kind: str = PACK_KIND) -> SoundSet:
    """Load a local archive supplied by the content-pack layer."""
    if kind != PACK_KIND:
        raise ValueError(f"Expected a {PACK_KIND} pack.")
    return read_set(path)


def export_set(sound_set: SoundSet, path: Path) -> None:
    """Write a single portable archive with all of the set's audio and credits."""
    if len(sound_set.assignments) > MAX_ASSIGNMENTS:
        raise ValueError("A portable sound set can contain at most 256 assignments.")
    manifest = {
        "kind": PACK_KIND,
        "version": 1,
        "name": _text(sound_set.name, 120, "name"),
        "credits": asdict(_credits(asdict(sound_set.credits))),
        "events": {},
    }
    audio_bytes = 0
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for key, clip in sound_set.assignments.items():
            if clip is None:
                manifest["events"][key] = None
                continue
            audio_bytes += clip.size
            if audio_bytes > MAX_AUDIO_BYTES:
                raise ValueError("The sound set exceeds the 32 MiB audio limit.")
            member = _audio_member(key, clip.format)
            decode_clip(clip.data, clip.format)
            archive.writestr(member, clip.data)
            entry = {"format": clip.format}
            if clip.credits != Credits():
                entry["credits"] = asdict(_credits(asdict(clip.credits)))
            manifest["events"][key] = entry
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        if len(data) > _MAX_MANIFEST_BYTES:
            raise ValueError("The sound set manifest exceeds 64 KiB.")
        archive.writestr("manifest.json", data)
    extras._atomic_write(path, output.getvalue())


@dataclass(frozen=True, slots=True)
class SavedSet:
    id: str
    sound_set: SoundSet | None = None
    error: str = ""


class SoundLibrary:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (
            directory if directory is not None else Path(config.user_data_dir()) / "sounds"
        )

    def _path(self, set_id: str) -> Path:
        if not _SET_ID.fullmatch(set_id):
            raise ValueError("Invalid personal sound set ID.")
        return extras._regular_path(self.directory.resolve(), set_id + SET_SUFFIX)

    def save(self, sound_set: SoundSet, *, set_id: str | None = None) -> str:
        set_id = uuid4().hex if set_id is None else set_id
        export_set(sound_set, self._path(set_id))
        return set_id

    def load(self, set_id: str) -> SoundSet:
        return read_set(self._path(set_id))

    def import_set(self, path: Path) -> str:
        return self.save(read_set(path))

    def list(self) -> tuple[SavedSet, ...]:
        rows = []
        for path in sorted(self.directory.glob("*" + SET_SUFFIX)):
            try:
                rows.append(SavedSet(path.stem, self.load(path.stem)))
            except (OSError, ValueError) as exc:
                rows.append(SavedSet(path.stem, error=str(exc)))
        return tuple(rows)
