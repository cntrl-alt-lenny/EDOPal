"""Shared, read-only disk measurements for artwork sync and the installation doctor."""

import os
from collections.abc import Callable, Mapping

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


def _scan_art_folder(
    folder: str,
    extensions: tuple[str, ...],
    is_valid: Callable[[bytes], bool],
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
                if not is_valid(image_sample(entry.path)):
                    continue
            except OSError:
                continue
            try:
                ids.add(int(stem))
            except ValueError:
                continue
    return ids, size


def scan_artwork_coverage(
    pics_path: str,
    id_to_name: Mapping[int, str],
    field_ids: set[int],
) -> dict[str, int]:
    """Measure images on disk against the caller's card index."""
    card_files, card_bytes = _scan_art_folder(pics_path, ("jpg",), looks_like_jpeg)
    field_files, field_bytes = _scan_art_folder(
        field_art_dir(pics_path), ("jpg", "png"), looks_like_image
    )
    return {
        "cards_total": len(id_to_name),
        "cards_have": sum(1 for cid in id_to_name if cid in card_files),
        "fields_total": len(field_ids & id_to_name.keys()),
        "fields_have": sum(1 for cid in field_ids if cid in id_to_name and cid in field_files),
        "disk_bytes": card_bytes + field_bytes,
    }
