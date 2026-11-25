"""Downloadable packs (themes, card backs, music) from the edopal-packs repo (Qt-free).

Catalog: ``catalog.json`` attached to the newest ``catalog`` release of PACKS_REPO, listing
:class:`PackInfo` rows. Each pack is a ZIP release asset containing ``manifest.json`` at its
root. Packs download on demand, are verified (size + SHA-256), extracted into the per-user
cache, and stay available offline. See docs/packs.md for the manifest schema.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

PackKind = Literal["theme", "cardback", "music", "format"]
Progress = Callable[[int, int], None]  # (done_bytes, total_bytes)


@dataclass(frozen=True)
class PackInfo:
    id: str  # "theme-abyss", "cardback-vortex-classic", "music-power-of-chaos-yugi"
    kind: PackKind
    name: str  # "Abyss"
    version: str  # "1.0.0"
    url: str  # ZIP download URL
    size: int  # bytes
    sha256: str
    thumbnail: str = ""  # small image URL (tile)
    preview: str = ""  # optional small preview URL (e.g. 20 s audio clip or large preview image)
    meta: dict = field(default_factory=dict, compare=False, hash=False)  # kind-specific fields


class PackService(Protocol):
    def catalog(self, kind: PackKind | None = None, *, refresh: bool = False) -> list[PackInfo]: ...
    def cached(self, pack_id: str) -> Path | None: ...  # extracted folder if present & verified
    def ensure(self, pack: PackInfo, progress: Progress | None = None, cancel=None) -> Path: ...
    def thumbnail(self, pack: PackInfo) -> Path | None: ...  # cached small image, downloads if needed
    def remove(self, pack_id: str) -> None: ...
