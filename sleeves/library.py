"""Synchronous collection operations for the page's worker. Entry IDs survive renames."""

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt

import config
import extras

from .catalog import Recipe
from .designer import render
from .imaging import Layout, compose, decode, png_bytes, read_source
from .storage import Entry, Source, Store, digest


def _name(name: str) -> str:
    if not name.strip():
        raise ValueError("Give the sleeve a name.")
    return name.strip()


class Library:
    """Default root is config.user_data_dir()/sleeves. Supply an isolated root for tests."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else Path(config.user_data_dir()) / "sleeves"
        self._store = Store(self.root)

    def add_design(self, name: str, recipe: Recipe) -> Entry:
        """Render and save a recipe and an immutable print."""
        name = _name(name)
        data = png_bytes(render(recipe))
        with self._store.transaction() as state:
            e = Entry(uuid4().hex, name, "designed", self._store.put(data), recipe=recipe)
            state.entries[e.id] = e
            return e

    def add_image(
        self,
        name: str,
        original: Path | bytes,
        layout: Layout | None = None,
        *,
        passcode: int | None = None,
    ) -> Entry:
        """Retain the original bytes and composition. Supply the card ID for cached card art."""
        name = _name(name)
        if passcode is not None and (type(passcode) is not int or not 0 < passcode <= 0xFFFFFFFF):
            raise ValueError("Choose a valid card passcode.")
        if layout is None:
            layout = Layout("framed" if passcode is not None else "fill")
        data = read_source(original) if isinstance(original, Path) else original
        rendered = png_bytes(compose(data, layout))
        with self._store.transaction() as state:
            e = Entry(
                uuid4().hex,
                name,
                "imported" if passcode is None else "card art",
                self._store.put(rendered),
                self._store.put(data, original=True),
                layout=layout,
                passcode=passcode,
            )
            state.entries[e.id] = e
            return e

    def entries(
        self,
        query: str = "",
        *,
        source: Source | None = None,
        favourites: bool = False,
        recent: bool = False,
    ) -> list[Entry]:
        """Search name, tags, source or passcode. recent lists applied entries, newest first."""
        with self._store.open() as state:
            terms = query.casefold().split()
            found = []
            for e in state.entries.values():
                if (
                    (source and e.source != source)
                    or (favourites and not e.favourite)
                    or (recent and not e.last_used)
                ):
                    continue
                fields = " ".join((e.name, e.source, str(e.passcode or ""), *e.tags)).casefold()
                if all(term in fields for term in terms):
                    found.append(e)
            return sorted(
                found, key=lambda e: (-e.last_used if recent else 0, e.name.casefold(), e.id)
            )

    def edit(
        self,
        entry_id: str,
        *,
        name: str | None = None,
        tags: Iterable[str] | None = None,
        favourite: bool | None = None,
    ) -> Entry:
        """Change only the supplied metadata. Return the updated entry."""
        with self._store.transaction() as state:
            e = state.entries[entry_id]
            e = replace(
                e,
                name=e.name if name is None else _name(name),
                tags=e.tags
                if tags is None
                else tuple(dict.fromkeys(t.strip() for t in tags if t.strip())),
                favourite=e.favourite if favourite is None else favourite,
            )
            state.entries[e.id] = e
            return e

    def delete(self, entry_id: str) -> None:
        """Forget this entry and its assignments. Remove unshared blobs after the manifest commits."""
        with self._store.open(writable=True) as state:
            e = state.entries.pop(entry_id)
            for installs in (state.assignments, state.undo):
                for slots in installs.values():
                    for slot in [slot for slot, ref in slots.items() if ref == entry_id]:
                        del slots[slot]
            # Publish the catalogue before reclamation, under the same writer lock.
            self._store.save(state)
            if all(other.image != e.image for other in state.entries.values()):
                self._store.blob(e.image).unlink(missing_ok=True)
                self._thumb(e).unlink(missing_ok=True)
            if e.original and all(other.original != e.original for other in state.entries.values()):
                self._store.blob(e.original, original=True).unlink(missing_ok=True)

    def image_path(self, entry_id: str, *, original: bool = False) -> Path:
        """Read-only managed file for a preview or re-crop. Raises ValueError if no original."""
        with self._store.open() as state:
            e = state.entries[entry_id]
            key = e.original if original else e.image
            if key is None:
                raise ValueError("This sleeve has no imported original.")
            return self._store.blob(key, original=original)

    def _thumb(self, e: Entry) -> Path:
        return self.root / "thumbnails" / (e.image + ".png")

    def thumbnail(self, entry_id: str) -> Path:
        """Return a cached 177 × 254 preview. Damaged or missing thumbnails regenerate."""
        with self._store.open() as state:
            e = state.entries[entry_id]
            path = self._thumb(e)
            if path.is_file():
                try:
                    thumb = decode(read_source(path))
                    if thumb.width() == 177 and thumb.height() == 254:
                        return path
                except ValueError:
                    pass
            image = decode(self._bytes(e)).scaled(
                177,
                254,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            extras._atomic_write(path, png_bytes(image))
            return path

    def _bytes(self, e: Entry) -> bytes:
        data = read_source(self._store.blob(e.image))
        if digest(data) != e.image:
            raise ValueError(f"The saved image for {e.name} is damaged.")
        return data
