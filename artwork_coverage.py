"""Shared, read-only disk measurements for artwork sync and the installation doctor."""

import os
import struct
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path

# File servers can return a successful status for an error page. Check the framing too.
JPEG_MAGIC = b"\xff\xd8\xff"
JPEG_END = b"\xff\xd9"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_IMAGE_BYTES = 512


def looks_like_jpeg(content: bytes) -> bool:
    """Return True when the content has the basic shape of a complete JPEG."""
    return (
        len(content) >= MIN_IMAGE_BYTES
        and content.startswith(JPEG_MAGIC)
        and JPEG_END in content[-64:]
    )


def looks_like_image(content: bytes) -> bool:
    """Return True for a complete-looking PNG or JPEG."""
    if len(content) < MIN_IMAGE_BYTES:
        return False
    if content.startswith(JPEG_MAGIC):
        return JPEG_END in content[-64:]
    return content.startswith(PNG_MAGIC) and b"IEND" in content[-32:]


def image_sample(path: str) -> bytes:
    """Read only the size threshold, signature and trailer our image checks inspect."""
    with open(path, "rb", buffering=0) as source:
        head = source.read(MIN_IMAGE_BYTES)
        if len(head) < MIN_IMAGE_BYTES:
            return head
        source.seek(-64, os.SEEK_END)
        return head + source.read(64)


def field_art_dir(pics_path: str) -> str:
    return os.path.join(pics_path, "field")


# Store provenance in the image so even an interrupted sync, a portable
# installation or a Studio undo keeps the source and the pixels together.
_SOURCE_KEY = b"EDOPro field source"
_PNG_HEADER_END = 33  # signature and mandatory IHDR chunk


def mark_field_source(content: bytes | bytearray, source: str) -> bytes:
    label = _SOURCE_KEY + b"\0" + source.encode("ascii")
    if content.startswith(JPEG_MAGIC):
        marker = b"\xff\xfe" + struct.pack(">H", len(label) + 2) + label
        return content[:2] + marker + content[2:]
    chunk = b"tEXt" + label
    marker = struct.pack(">I", len(label)) + chunk + struct.pack(">I", zlib.crc32(chunk))
    return content[:_PNG_HEADER_END] + marker + content[_PNG_HEADER_END:]


def field_source(header: bytes) -> str:
    if header.startswith(b"\xff\xd8\xff\xfe") and len(header) >= 6:
        label = header[6 : 4 + int.from_bytes(header[4:6], "big")]
    elif header.startswith(PNG_MAGIC) and header[37:41] == b"tEXt":
        label = header[41 : 41 + int.from_bytes(header[33:37], "big")]
    else:
        return ""
    prefix = _SOURCE_KEY + b"\0"
    return label[len(prefix) :].decode("ascii", "replace") if label.startswith(prefix) else ""


def derived_field(path: Path) -> bool:
    try:
        with path.open("rb") as image:
            return field_source(image.read(128)) == "derived"
    except FileNotFoundError:
        return False


def crop_field_face(path: Path, layout: str) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice

    from sleeves.imaging import decode, read_source

    image = decode(read_source(path))
    w, h = image.width(), image.height()
    bounds = {
        "card": (0.117, 0.182, 0.883, 0.707),
        "rush": (0.08, 0.145, 0.92, 0.684),
        "skill": (0.13, 0.19, 0.87, 0.707),
    }[layout]
    x, y, right, bottom = (round(n * side) for n, side in zip(bounds, (w, h, w, h), strict=True))
    cropped = image.copy(x, y, right - x, bottom - y)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not cropped.save(buffer, "JPEG", 92):
        raise OSError("Could not encode field artwork.")
    return bytes(buffer.data())


def _scan_art_folder(
    folder: str,
    extensions: tuple[str, ...],
    is_valid: Callable[[bytes], bool],
    derived: set[int] | None = None,
) -> tuple[set[int], int]:
    ids: set[int] = set()
    size = 0
    if not os.path.isdir(folder):
        return ids, size
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.is_file():
                continue
            stem, _, ext = entry.name.rpartition(".")
            if ext not in extensions:
                continue
            try:
                size += entry.stat().st_size
                sample = image_sample(entry.path)
                if not is_valid(sample):
                    continue
            except OSError:
                continue
            try:
                cid = int(stem)
                ids.add(cid)
                if derived is not None and field_source(sample) == "derived":
                    derived.add(cid)
            except ValueError:
                continue
    return ids, size


def scan_artwork_coverage(
    pics_path: str,
    id_to_name: Mapping[int, str],
    field_ids: set[int],
    *,
    optional_missing: set[int] | None = None,
) -> dict[str, int]:
    """Measure images on disk against the caller's card index."""
    card_files, card_bytes = _scan_art_folder(pics_path, ("jpg",), looks_like_jpeg)
    derived: set[int] = set()
    field_files, field_bytes = _scan_art_folder(
        field_art_dir(pics_path), ("jpg", "png"), looks_like_image, derived
    )
    return {
        "cards_total": len(id_to_name),
        "cards_expected": len(id_to_name)
        - len(((optional_missing or set()) & id_to_name.keys()) - card_files),
        "fields_derived": len(derived & field_ids & id_to_name.keys()),
        "cards_have": sum(1 for cid in id_to_name if cid in card_files),
        "fields_total": len(field_ids & id_to_name.keys()),
        "fields_have": sum(1 for cid in field_ids if cid in id_to_name and cid in field_files),
        "disk_bytes": card_bytes + field_bytes,
    }
