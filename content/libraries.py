"""Page actions. Run on a worker event loop, with Qt initialized on the main thread."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import Event
from zipfile import ZipFile

from music.packs import pack_media
from music.packs import replace_pack as replace_music
from sleeves import Library
from state_lock import locked
from themes import ThemeLibrary

from .archives import shared_files, skin_files
from .cache import PackCache
from .catalogue import PackStatus
from .download import DownloadError, DownloadFailure, ProgressCallback, check_cancel, download_pack
from .layouts import LAYOUTS, MAX_TOTAL, staged_items
from .manifest import PACK_ID
from .media import Changes, Item, Origin

Sources = list[tuple[Item, Path]]
Installer = Callable[[Origin, Sources], Changes]


@dataclass(frozen=True)
class _Library:
    kinds: tuple[str, ...]
    replace: Installer
    origins: Callable[[bool], set[Origin]]


def _libraries(root: Path) -> tuple[_Library, ...]:
    music = root / "soundtracks"
    themes = ThemeLibrary(root / "themes")
    sleeves = Library(root / "sleeves")
    return (
        _Library(
            ("music", "covers"),
            partial(replace_music, music),
            lambda include_retained: {
                r.origin for r in pack_media(music) if include_retained or not r.retained
            },
        ),
        _Library(
            ("wallpaper",),
            themes.replace_wallpapers,
            lambda include_retained: {
                r.origin for r in themes.wallpapers() if include_retained or not r.retained
            },
        ),
        _Library(
            ("sleeves",),
            sleeves.replace_pack,
            lambda include_retained: (
                {
                    e.origin
                    for e in sleeves.entries()
                    if e.origin and (include_retained or not e.retained)
                }
                if sleeves.root.is_dir()
                else set()
            ),
        ),
    )


def installed_origins(cache: PackCache, pack_id: str) -> tuple[Origin, ...]:
    """Library editions, including after cache eviction or a failed update."""
    return tuple(
        o
        for library in _libraries(cache.root.parent)
        for o in library.origins(False)
        if o.id == pack_id
    )


def _retire(cache: PackCache, pack_id: str, keep: str = "") -> Changes:
    removed, retained = [], []
    for library in _libraries(cache.root.parent):
        if keep in library.kinds:
            continue
        origin = next((o for o in library.origins(True) if o.id == pack_id), None)
        if origin:
            changes = library.replace(origin, [])
            removed.extend(changes.removed)
            retained.extend(changes.retained)
    return Changes(removed=tuple(removed), retained=tuple(retained))


async def make_available(
    cache: PackCache,
    status: PackStatus,
    *,
    app_version: str,
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
    phase: Callable[[str], None] | None = None,
    timeout: float = 30,
) -> Changes:
    pack = status.pack
    if pack.id == "shared" and pack.kind != "sound-effects":
        raise ValueError("The shared ID is reserved for the legacy appearance dependency.")
    if pack.kind not in LAYOUTS and pack.kind != "skin" and pack.id != "shared":
        raise ValueError("Installation is unavailable for this pack kind.")
    if pack.kind in LAYOUTS and pack.size_bytes > MAX_TOTAL + 1024**2:
        raise ValueError("Library ZIP exceeds the size limit.")
    if not PACK_ID.fullmatch(pack.id):
        raise ValueError("Invalid pack ID.")
    notify = phase or (lambda _: None)
    with locked(cache.root / ".locks", f"library-{pack.id}.lock"):
        notify("downloading")
        await download_pack(
            cache, pack, app_version=app_version, progress=progress, cancel=cancel, timeout=timeout
        )
        notify("verifying")
        with cache.use_pack(pack.id, exact=pack) as path:
            if path is None:
                raise DownloadError(DownloadFailure.INTEGRITY, "The verified archive is missing.")
            check_cancel(cancel)
            origin = Origin.from_pack(pack)
            if pack.kind not in LAYOUTS:
                with ZipFile(path) as archive:
                    if pack.kind == "skin":
                        skin_files(archive, pack.title)
                    else:
                        shared_files(archive)
                return _retire(cache, pack.id)
            with staged_items(path, pack.kind, cache.root, cancel=cancel) as sources:
                check_cancel(cancel)
                notify("installing")
                library = next(
                    lib for lib in _libraries(cache.root.parent) if pack.kind in lib.kinds
                )
                changes = library.replace(origin, sources)
            stale = _retire(cache, pack.id, pack.kind)
            return Changes(
                changes.added, changes.removed + stale.removed, changes.retained + stale.retained
            )


def remove_pack(cache: PackCache, status: PackStatus | str) -> Changes:
    """Remove unchanged library entries first. A busy cache can be retried independently."""
    pack_id = status if isinstance(status, str) else status.pack.id
    if not PACK_ID.fullmatch(pack_id):
        raise ValueError("Invalid pack ID.")
    with locked(cache.root / ".locks", f"library-{pack_id}.lock"):
        changes = _retire(cache, pack_id)
        cache.remove_pack(pack_id)
        return changes
