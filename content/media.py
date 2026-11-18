"""Pack credits and atomic collections of immutable library media."""

import hashlib
import json
import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from state_lock import locked

from .cache import file_sha256
from .manifest import PACK_ID, SHA256, VERSION, Pack, validate_url


@dataclass(frozen=True)
class Origin:
    id: str
    version: str
    sha256: str
    author: str
    license: str
    attribution: str
    source_url: str

    def __post_init__(self) -> None:
        if (
            not PACK_ID.fullmatch(self.id)
            or not VERSION.fullmatch(self.version)
            or not SHA256.fullmatch(self.sha256)
            or not all(
                isinstance(v, str) and v.strip()
                for v in (self.author, self.license, self.attribution)
            )
        ):
            raise ValueError("Invalid pack provenance.")
        validate_url(self.source_url, "Pack source")

    @classmethod
    def from_pack(cls, pack: Pack) -> "Origin":
        return cls(
            pack.id,
            pack.version,
            pack.sha256,
            pack.author,
            pack.license,
            pack.attribution,
            pack.source_url,
        )


@dataclass(frozen=True)
class Item:
    member: str
    title: str
    role: str
    game: str = ""
    scenes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Media:
    item: Item
    origin: Origin
    path: Path
    digest: str
    retained: bool = False
    personal: bool = False


@dataclass(frozen=True)
class Changes:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()


class MediaCollection:
    """A single index publication makes an album (including its covers) visible together."""

    def __init__(self, directory: Path, personal_path: Callable[[str], Path] | None = None) -> None:
        self.root = directory.absolute()
        self.index = self.root / "index.json"
        self.personal_path = personal_path

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}(?:-[a-f0-9]{32})?\.[a-z0-9]{2,4}", name):
            raise ValueError("Invalid library media filename.")
        path = self.root / name
        if path.resolve() != path:
            raise OSError("Linked library media path.")
        return path

    def entries(self) -> tuple[Media, ...]:
        try:
            rows = json.loads(self.index.read_bytes())
        except FileNotFoundError:
            return ()
        entries = []
        for row in rows:
            item = Item(
                **{
                    **row["item"],
                    "scenes": tuple(row["item"]["scenes"]),
                    "tags": tuple(row["item"]["tags"]),
                }
            )
            personal = row.get("personal", False)
            if personal and self.personal_path is None:
                raise ValueError("Unexpected personal media reference.")
            path = self.personal_path(row["file"]) if personal else self._path(row["file"])
            retained = row["retained"] or row["baseline"] != self._baseline(item)
            entries.append(
                Media(item, Origin(**row["origin"]), path, row["digest"], retained, personal)
            )
        return tuple(entries)

    @staticmethod
    def _baseline(item: Item) -> str:
        return hashlib.sha256(json.dumps(asdict(item), sort_keys=True).encode()).hexdigest()

    def _store(self, source: Path, digest: str, created: list[Path]) -> Path:
        ext = source.suffix.lower()
        path = self._path(digest + ext)
        if path.exists():
            if file_sha256(path) == digest:
                return path
            # A player replaced the original. Leave that path playable.
            path = self._path(f"{digest}-{uuid4().hex}{ext}")
        with NamedTemporaryFile(dir=self.root, delete=False) as staged:
            tmp = Path(staged.name)
        try:
            shutil.copyfile(source, tmp)
            if file_sha256(tmp) != digest:
                raise ValueError("Media changed during installation.")
            tmp.replace(path)
            created.append(path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def replace(
        self,
        origin: Origin,
        sources: Iterable[tuple[Item, Path]],
        *,
        personal: Mapping[str, Path] | None = None,
    ) -> Changes:
        """Replace one pack's manifest slice. Keep externally edited recordings and images."""
        if self.root.resolve() != self.root:
            raise OSError("Linked library storage directory.")
        with locked(self.root, ".media.lock"):
            before = self.entries()
            old = [row for row in before if row.origin.id == origin.id and not row.retained]
            keep = [row for row in before if row.origin.id != origin.id or row.retained]
            retained = [r.item.member for r in keep if r.origin.id == origin.id]
            known = {}
            edited = set()
            for row in old:
                if not row.path.is_file():
                    continue
                actual = file_sha256(row.path)
                if actual != row.digest:
                    keep.append(Media(row.item, row.origin, row.path, actual, True, row.personal))
                    retained.append(row.item.member)
                    edited.add(row)
                else:
                    known[row.digest] = row
            created: list[Path] = []
            incoming = []
            try:
                for item, source in sources:
                    digest = file_sha256(source)
                    existing = known.get(digest)
                    borrowed = (personal or {}).get(digest)
                    if existing:
                        path, owned_by_player = existing.path, existing.personal
                    elif borrowed and file_sha256(borrowed) == digest:
                        path, owned_by_player = borrowed, True
                    else:
                        path, owned_by_player = self._store(source, digest, created), False
                    entry = Media(item, origin, path, digest, personal=owned_by_player)
                    known[digest] = entry
                    incoming.append(entry)
                if old == incoming and not edited:
                    return Changes(retained=tuple(retained))
                after = (*keep, *incoming)
                if before != after:
                    self._save(after)
            except BaseException:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
            live = {row.path for row in after}
            for row in old:
                if (
                    not row.personal
                    and row.path not in live
                    and row.path.is_file()
                    and file_sha256(row.path) == row.digest
                ):
                    row.path.unlink()
            return Changes(
                tuple(r.item.member for r in incoming if r not in old),
                tuple(r.item.member for r in old if r not in incoming and r not in edited),
                tuple(retained),
            )

    def _save(self, rows: tuple[Media, ...]) -> None:
        payload = [
            {
                "item": asdict(r.item),
                "origin": asdict(r.origin),
                "file": r.path.relative_to(self.root.parent).as_posix()
                if r.personal
                else r.path.name,
                "personal": r.personal,
                "baseline": self._baseline(r.item),
                "digest": r.digest,
                "retained": r.retained,
            }
            for r in rows
        ]
        with NamedTemporaryFile(dir=self.root, delete=False) as staged:
            tmp = Path(staged.name)
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.index)
        finally:
            tmp.unlink(missing_ok=True)
