"""Per-user storage and cross-process coordination for content."""

import errno
import hashlib
import shutil
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import config
from state_lock import locked

from .manifest import PACK_ID, SHA256, VERSION, Pack, version_key


class CacheBusyError(OSError):
    """A competing operation owns the relevant cache lock."""


@dataclass(frozen=True, slots=True)
class CachedPack:
    id: str
    version: str
    sha256: str
    path: Path
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CacheEntry:
    pack_id: str
    path: Path
    size_bytes: int
    category: str


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _asset_key(prefix: str, pack_id: str) -> str:
    if not PACK_ID.fullmatch(pack_id):
        raise ValueError(f"Invalid pack id: {pack_id!r}.")
    return f"{prefix}-{pack_id}"


def _group_info(name: str) -> tuple[str, str] | None:
    prefix, _, key = name.partition("-")
    if prefix in {"pack", "preview"} and PACK_ID.fullmatch(key):
        return prefix, key
    if prefix == "manifest" and SHA256.fullmatch(key):
        return prefix, key
    return None


class PackCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(config.user_data_dir()) / "packs"

    @classmethod
    def for_config(cls, cfg: config.Config) -> "PackCache":
        if cfg.cli.config:
            return cls(Path(cfg.config_path).resolve().parent / "packs")
        return cls()

    @classmethod
    def for_config_path(cls, config_path: str) -> "PackCache":
        path = Path(config_path).resolve()
        if path != Path(config.default_config_path()).resolve():
            return cls(path.parent / "packs")
        return cls()

    @contextmanager
    def _lock(self, key: str) -> Iterator[None]:
        # Keep lock files outside removable groups. Unlinking a locked file on
        # POSIX lets the next process create a different inode and acquire it.
        with ExitStack() as stack:
            try:
                stack.enter_context(locked(self.root / ".locks", f"{key}.lock"))
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                raise CacheBusyError(
                    f"The content cache is busy ({key}). Try again shortly."
                ) from exc
            yield

    @contextmanager
    def _work(self, key: str, *, create: bool = True) -> Iterator[Path]:
        folder = self.root / key
        with ExitStack() as operation:
            with self._lock("mutations"):
                operation.enter_context(self._lock(key))
                if create:
                    folder.mkdir(exist_ok=True)
            yield folder

    def _groups(self) -> tuple[Path, ...]:
        try:
            return tuple(
                sorted(
                    path
                    for path in self.root.iterdir()
                    if _group_info(path.name) and not path.is_symlink() and path.is_dir()
                )
            )
        except FileNotFoundError:
            return ()

    def cached_versions(self, pack_id: str) -> tuple[CachedPack, ...]:
        folder = self.root / _asset_key("pack", pack_id)
        versions = []
        try:
            for path in folder.glob("*.zip"):
                version, _, digest = path.stem.partition("--")
                if VERSION.fullmatch(version) and SHA256.fullmatch(digest):
                    try:
                        if not path.is_symlink() and path.is_file():
                            versions.append(
                                CachedPack(pack_id, version, digest, path, path.stat().st_size)
                            )
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            pass
        return tuple(
            sorted(
                versions, key=lambda pack: (version_key(pack.version), pack.path.name), reverse=True
            )
        )

    def downloading(self, pack_id: str) -> bool:
        key = _asset_key("pack", pack_id)
        marker = self.root / key / ".downloading"
        if marker.exists():
            try:
                with self._lock(key):
                    return False
            except CacheBusyError:
                return marker.exists()
        return False

    @contextmanager
    def use_pack(self, pack_id: str, *, exact: Pack | None = None) -> Iterator[Path | None]:
        """Yield a verified archive pinned against removal until the scope exits."""
        key = _asset_key("pack", pack_id)
        if exact is not None and exact.id != pack_id:
            raise ValueError("The requested pack does not match the cache id.")
        if not (self.root / key).is_dir():
            yield None
            return
        with self._work(key, create=False):
            for candidate in self.cached_versions(pack_id):
                if exact is not None and (candidate.version, candidate.sha256) != (
                    exact.version,
                    exact.sha256,
                ):
                    continue
                if (exact is None or candidate.size_bytes == exact.size_bytes) and file_sha256(
                    candidate.path
                ) == candidate.sha256:
                    yield candidate.path
                    return
                candidate.path.unlink()
            yield None

    def list_cached(self) -> tuple[CacheEntry, ...]:
        """List archive, preview and partial files with their actual disk sizes."""
        entries = []
        for folder in self._groups():
            group = _group_info(folder.name)
            assert group is not None
            category, pack_id = group
            if category == "manifest":
                continue
            try:
                for path in sorted(folder.iterdir()):
                    if (
                        not path.is_symlink()
                        and path.suffix in {".zip", ".image", ".part"}
                        and path.is_file()
                    ):
                        try:
                            entries.append(
                                CacheEntry(
                                    pack_id,
                                    path,
                                    path.stat().st_size,
                                    "partial" if path.suffix == ".part" else category,
                                )
                            )
                        except FileNotFoundError:
                            continue
            except FileNotFoundError:
                continue
        return tuple(entries)

    def remove_pack(self, pack_id: str) -> None:
        """Remove all versions, previews and partials for one pack."""
        keys = [_asset_key(prefix, pack_id) for prefix in ("pack", "preview")]
        with self._lock("mutations"), ExitStack() as operations:
            for key in keys:
                operations.enter_context(self._lock(key))
            for key in keys:
                folder = self.root / key
                if folder.is_dir():
                    shutil.rmtree(folder)

    def clear(self) -> None:
        """Clear all catalogue and content data, or fail if an operation is active."""
        with self._lock("mutations"), ExitStack() as operations:
            groups = self._groups()
            for folder in groups:
                operations.enter_context(self._lock(folder.name))
            for folder in groups:
                shutil.rmtree(folder)

    def cleanup_partials(self, *, max_age_seconds: float = 7 * 86400) -> int:
        """Remove stale partial files while skipping groups in use."""
        if max_age_seconds < 0:
            raise ValueError("The maximum age must be nonnegative.")
        cutoff = time.time() - max_age_seconds
        removed = 0
        with self._lock("mutations"):
            for folder in self._groups():
                try:
                    with self._lock(folder.name):
                        for path in folder.glob("*.part"):
                            if not path.is_symlink() and path.stat().st_mtime <= cutoff:
                                path.unlink()
                                removed += 1
                        (folder / ".downloading").unlink(missing_ok=True)
                except CacheBusyError:
                    continue
        return removed
