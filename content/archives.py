"""Validation for ZIP packs and the appearance installer's duplicate map."""

import json
import re
import stat
from pathlib import PureWindowsPath
from zipfile import ZipFile

MAX_EXPANDED_BYTES = 2 * 1024**3
MAX_MEMBER_BYTES = 512 * 1024**2
MAX_DUPLICATES_BYTES = 1024**2
_UNSAFE = re.compile(r'[<>:"\\|?*\x00-\x1f]')


def _validate_name(name: str) -> None:
    parts = name.rstrip("/").split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or _UNSAFE.search(part)
        or PureWindowsPath(part).is_reserved()
        for part in parts
    ):
        raise ValueError(f"Unsafe path in the pack: {name!r}.")


def pack_files(archive: ZipFile) -> dict[str, str]:
    """Map safe game paths to ZIP members, including the compressed aliases."""
    files = {}
    names = set()
    expanded = 0
    for info in archive.infolist():
        name = info.orig_filename
        _validate_name(name)
        key = name.rstrip("/").casefold()
        if key in names:
            raise ValueError(f"Duplicate path in the pack: {name!r}.")
        names.add(key)
        member_type = stat.S_IFMT(info.external_attr >> 16)
        valid_type = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
        if member_type not in {0, valid_type} or info.flag_bits & 1:
            raise ValueError(f"Unsupported ZIP member: {name!r}.")
        if not info.is_dir():
            expanded += info.file_size
            if info.file_size > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise ValueError("The expanded pack exceeds the size limit.")
            files[name] = name
    if "duplicates.json" in files:
        if archive.getinfo("duplicates.json").file_size > MAX_DUPLICATES_BYTES:
            raise ValueError("The duplicate map exceeds the size limit.")

        def pairs(items: list[tuple[str, object]]) -> dict:
            result = dict(items)
            if len(result) != len(items):
                raise ValueError("Repeated entry in duplicates.json.")
            return result

        aliases = json.loads(archive.read("duplicates.json"), object_pairs_hook=pairs)
        if not isinstance(aliases, dict):
            raise ValueError("duplicates.json must be an object.")
        del files["duplicates.json"]
        for target, source in aliases.items():
            _validate_name(target)
            if (
                target.endswith("/")
                or target.casefold() in names
                or not isinstance(source, str)
                or source not in files
                or source in aliases
            ):
                raise ValueError(f"Invalid duplicate mapping for {target!r}.")
            expanded += archive.getinfo(source).file_size
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError("The expanded pack exceeds the size limit.")
            files[target] = source
            names.add(target.casefold())
    if not files:
        raise ValueError("The pack contains no files.")
    file_names = {name.casefold() for name in files}
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]) in file_names for i in range(1, len(parts))):
            raise ValueError(f"A ZIP file is also used as a directory: {name!r}.")
    return files
