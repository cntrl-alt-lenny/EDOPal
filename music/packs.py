"""Downloadable recordings and cover art in the player's soundtrack library."""

from collections.abc import Iterable
from pathlib import Path

from content.cache import file_sha256
from content.media import Changes, Item, Media, MediaCollection, Origin
from music.audio import FORMATS, _audio
from music.catalogue import ART, ID, SCENES, catalog, official_games
from state_lock import locked


def _collection(root: Path) -> MediaCollection:
    root = root.absolute()

    def personal_path(name: str) -> Path:
        parts = name.split("/")
        path = root / name
        if (
            len(parts) != 3
            or not ID.fullmatch(parts[0])
            or parts[1] not in SCENES
            or Path(parts[2]).name != parts[2]
            or path.suffix.lower() not in FORMATS
            or path.resolve() != path
            or not path.is_relative_to(root)
        ):
            raise ValueError("Invalid personal soundtrack reference.")
        return path

    return MediaCollection(root / "pack-media", personal_path)


def pack_media(root: Path) -> tuple[Media, ...]:
    return _collection(root).entries()


def replace_pack(root: Path, origin: Origin, sources: Iterable[tuple[Item, Path]]) -> Changes:
    sources = list(sources)
    sizes = {source.stat().st_size for item, source in sources if item.role == "track"}
    personal = {}
    with locked(root, ".import.lock"):
        if sizes:
            for game in catalog(root=root):
                for scene in SCENES:
                    for path in _audio(root.absolute() / game / scene):
                        if path.resolve() == path and path.stat().st_size in sizes:
                            personal.setdefault(file_sha256(path), path)
        return _collection(root).replace(origin, sources, personal=personal)


def cover_path(game: str, root: Path, size: str = "") -> Path | None:
    """Prefer the last installed cover, then the bundled image for the requested size."""
    if game not in official_games() or size not in {"", "grid", "thumb", "detail"}:
        return None
    for row in reversed(pack_media(root)):
        if row.item.role == "cover" and row.item.game == game and row.path.is_file():
            return row.path
    path = ART / size / f"{game}.png"
    return path if path.is_file() else None
