"""Read sizes for application-owned data, including a portable config's neighbours."""

import os
import stat
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import config
from content.cache import PackCache
from content.catalogue import offline_manifest


def byte_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}"
    raise AssertionError


def disk_size(path: Path) -> int:
    try:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return 0
        if stat.S_ISREG(info.st_mode):
            return info.st_size
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as children:
                return sum(disk_size(Path(child.path)) for child in children)
    except FileNotFoundError:
        pass
    return 0


@dataclass(frozen=True)
class PackSize:
    key: str
    title: str
    size: int


@dataclass(frozen=True)
class Storage:
    parts: dict[str, int]
    packs: tuple[PackSize, ...]
    has_catalogue: bool


def inspect_storage(cfg: config.Config, cache: PackCache) -> Storage:
    settings = Path(cfg.config_path)
    root = settings.parent
    library = root if cfg.cli.config else Path(config.user_data_dir())
    parts = {
        "Settings & retry history": sum(
            disk_size(p)
            for p in (
                settings,
                settings.with_suffix(".studio.ini"),
                root / "failed_cards.json",
                root / "failed_fields.json",
                root / "manual_map.json",
            )
        ),
        "Sync reports": sum(disk_size(p) for p in root.glob("sync-report-*.txt")),
        "Restore copies": disk_size(root / "extras-backups") + disk_size(root / "artwork-restore"),
        "Music library": disk_size(library / "soundtracks"),
        "Skins & sleeves": disk_size(library / "themes") + disk_size(library / "sleeves"),
        "Downloaded packs": disk_size(cache.root),
    }
    sizes: dict[str, int] = defaultdict(int)
    for entry in cache.list_cached():
        sizes[entry.pack_id] += entry.size_bytes
    catalogue = offline_manifest(cache, cfg.content_manifest_url)
    packs = []
    for key, size in sizes.items():
        pack = catalogue.get(key)
        title = pack.title if pack else key.replace("-", " ").capitalize()
        packs.append(PackSize(key, title, size))
    return Storage(
        parts,
        tuple(sorted(packs, key=lambda p: p.title.casefold())),
        any(cache.root.glob("manifest-*/catalogue.json")),
    )
