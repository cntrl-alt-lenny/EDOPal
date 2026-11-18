"""Inspect active game settings, fonts, skins, and sound files."""

import hashlib
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from music_library import FORMATS

from .game_data import local_path
from .model import Finding, finding

# These are fingerprints of the two unchanged, malformed 41.0.2 distribution files.
_STOCK_XML = {
    "Indigo": "39d2030d0a946214cc545f35ffcc831e4d89d28da8122688a85778e6d1de76d8",
    "Purple - Obsessed": "935aae9560f767149c050c7171ab9e29be9d233856e1d56006005158416e3812",
}
_PNG = b"\x89PNG\r\n\x1a\n"
_SCENES = ("advantage", "deck", "disadvantage", "duel", "lose", "menu", "win")
_MAX_SKIN_FILE = 32 * 1024**2
_KEYS = {
    "skin",
    "textfont",
    "numfont",
    "fallbackFonts",
    "enable_music",
    "enable_sound",
    "music_volume",
    "sound_volume",
    "sound_backend",
}


def _bounded(path: Path) -> bytes:
    with path.open("rb") as file:
        data = file.read(_MAX_SKIN_FILE + 1)
    if not data or len(data) > _MAX_SKIN_FILE:
        raise ValueError("Invalid skin file size")
    return data


def _valid_image(path: Path) -> bool:
    data = _bounded(path)
    if path.suffix.casefold() in {".jpeg", ".jpg"}:
        return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if path.suffix.casefold() != ".png":
        # Irrlicht can load other image formats. Their contents are not interpreted here.
        return True
    if not data.startswith(_PNG) or data[12:16] != b"IHDR":
        return False
    pos = len(_PNG)
    image_data = False
    while pos + 12 <= len(data):
        size, name = struct.unpack_from(">I4s", data, pos)
        end = pos + 8 + size
        if end + 4 > len(data) or zlib.crc32(data[pos + 4 : end]) != int.from_bytes(
            data[end : end + 4], "big"
        ):
            return False
        if name == b"IDAT":
            image_data = True
        if name == b"IEND":
            return image_data and size == 0 and end + 4 == len(data)
        pos = end + 4
    return False


def _skin(root: Path, values: dict[str, str], findings: list[Finding]) -> None:
    name = values.get("skin", "none")
    if not name or name == "none":
        findings.append(finding("skin.default"))
        return
    path = root / "skin" / name / "skin.xml"
    try:
        if name in {".", ".."} or any(char in name for char in "/\\:"):
            raise ValueError("Invalid skin path")
        folder = local_path(root, path.parent)
        path = local_path(folder, path)
        xml = _bounded(path)
    except (OSError, ValueError):
        findings.append(finding("skin.missing", path=path))
        return
    try:
        tree = ET.fromstring(xml)
        if tree.tag != "Skin":
            raise ET.ParseError("Missing Skin element")
    except ET.ParseError as exc:
        if hashlib.sha256(xml).hexdigest() == _STOCK_XML.get(name):
            findings.append(
                finding(
                    "skin.stock_malformed",
                    path=path,
                    affected=1,
                    line=exc.position[0] if hasattr(exc, "position") else None,
                )
            )
        else:
            findings.append(
                finding(
                    "skin.malformed",
                    path=path,
                    line=exc.position[0] if hasattr(exc, "position") else None,
                )
            )
        return

    images = sorted({texture for element in tree.iter() if (texture := element.get("texture"))})
    broken = []
    for image in images:
        target = folder / image
        try:
            if not _valid_image(local_path(folder, target)):
                broken.append(target)
        except (OSError, ValueError):
            broken.append(target)
    if broken:
        findings.append(
            finding("skin.images", path=path, affected=len(broken), images=tuple(broken))
        )
    else:
        findings.append(finding("skin.available", path=path, images=len(images)))


def _fonts(root: Path, values: dict[str, str], findings: list[Finding]) -> None:
    fallback = values.get("fallbackFonts", "")
    refs = [values.get("numfont", "fonts/NotoSansJP-Regular.otf")]
    text = [values.get("textfont", "fonts/NotoSansJP-Regular.otf 12")]
    if fallback:
        text.extend(part.strip() for part in fallback.split('"') if part.strip())
    refs.extend(re.sub(r"\s+[0-9]+$", "", item).strip('"') for item in text)
    missing = []
    for ref in refs:
        if ref == "bundled":
            continue
        path = root / ref
        try:
            target = local_path(root, path)
            with target.open("rb") as stream:
                if not stream.read(1):
                    missing.append(path)
        except (OSError, ValueError):
            missing.append(path)
    if missing:
        findings.append(
            finding(
                "fonts.missing", affected=len(set(missing)), paths=tuple(dict.fromkeys(missing))
            )
        )
    else:
        findings.append(finding("fonts.available", fonts=len(set(refs)), references=tuple(refs)))


def _audio_count(root: Path, folder: Path) -> int:
    try:
        return sum(
            1
            for path in local_path(root, folder).iterdir()
            if path.suffix.casefold() in FORMATS
            and not path.is_symlink()
            and path.is_file()
            and path.stat().st_size > 0
        )
    except (OSError, ValueError):
        return 0


def _audio(root: Path, values: dict[str, str], findings: list[Finding]) -> None:
    bgm = root / "sound" / "BGM"
    scene_counts = {scene: _audio_count(root, bgm / scene) for scene in _SCENES}
    loose = _audio_count(root, bgm)
    music_files = sum(scene_counts.values()) + loose
    scene_counts["duel"] += loose
    missing = tuple(scene for scene, count in scene_counts.items() if not count)
    sound_files = _audio_count(root, root / "sound")
    for feature, files, absent, default in (
        ("music", music_files, missing, "0"),
        ("sound", sound_files, () if sound_files else ("effects",), "1"),
    ):
        enabled = values.get("enable_" + feature, default) != "0"
        volume = values.get(feature + "_volume", "20")
        if not enabled:
            status = "disabled"
        elif volume == "0":
            status = "zero_volume"
        elif absent:
            status = "missing"
        else:
            status = "available"
        findings.append(
            finding(
                feature + "." + status,
                files=files,
                affected=len(absent),
                missing=absent,
                volume=volume,
                scenes=scene_counts if feature == "music" else {},
            )
        )
    if values.get("sound_backend", "").casefold() == "none":
        findings.append(finding("audio.backend_disabled"))


def scan_settings(root: Path, findings: list[Finding]) -> None:
    path = root / "config" / "system.conf"
    try:
        lines = (
            local_path(root, path)
            .read_text(encoding="utf-8-sig", errors="surrogateescape")
            .splitlines()
        )
    except (OSError, ValueError):
        findings.append(finding("settings.unavailable", path=path))
        return
    values = {}
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() in _KEYS:
            values[key.strip()] = value.strip()
    _skin(root, values, findings)
    _fonts(root, values, findings)
    _audio(root, values, findings)
