"""Streaming transfers shared by content archives and their previews."""

import asyncio
import errno
import re
import shutil
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event

import aiohttp

from network_tls import create_http_session

from .cache import PackCache, _asset_key, file_sha256
from .manifest import MAX_PREVIEW_BYTES, SHA256, VERSION, Pack, Preview, is_compatible

_CHUNK_SIZE = 64 * 1024
_CONTENT_RANGE = re.compile(r"bytes ([0-9]{1,19})-([0-9]{1,19})/([0-9]{1,19})", re.ASCII)


class DownloadFailure(StrEnum):
    CANCELLED = "cancelled"
    DISK_SPACE = "disk-space"
    INCOMPATIBLE = "incompatible"
    INTEGRITY = "integrity"
    NOT_FOUND = "not-found"
    HTTP = "http"
    TRANSIENT = "transient"


class DownloadError(Exception):
    def __init__(self, reason: DownloadFailure, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Progress:
    bytes_done: int
    total_bytes: int | None
    bytes_per_second: float


ProgressCallback = Callable[[Progress], None]


def check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise DownloadError(DownloadFailure.CANCELLED, "Download cancelled.")


def _check_space(folder: Path, required: int, *, reclaim: int = 0) -> None:
    if shutil.disk_usage(folder).free + reclaim < required:
        raise DownloadError(
            DownloadFailure.DISK_SPACE, "There is not enough free space for this download."
        )


def _file_size(path: Path) -> int | None:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise OSError(f"Expected an ordinary cache file: {path.name}")
        return path.stat().st_size
    except FileNotFoundError:
        return None


def preview_format(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass(frozen=True, slots=True)
class _Transfer:
    url: str
    sha256: str
    size_bytes: int | None
    target: Path
    is_preview: bool = False

    @property
    def limit(self) -> int:
        if self.is_preview:
            return MAX_PREVIEW_BYTES
        assert self.size_bytes is not None
        return self.size_bytes

    def verify(self, path: Path, size: int) -> bool:
        if not 0 < size <= self.limit or (self.size_bytes is not None and size != self.size_bytes):
            return False
        if self.is_preview:
            with path.open("rb") as handle:
                if preview_format(handle.read(12)) is None:
                    return False
        return file_sha256(path) == self.sha256


def _response_length(
    response: aiohttp.ClientResponse, offset: int, transfer: _Transfer
) -> tuple[int | None, int | None]:
    """Return the total object length and the end offset of this response."""
    length = response.content_length
    if response.status == 206 and offset:
        match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if match is None:
            raise DownloadError(
                DownloadFailure.TRANSIENT, "The server sent an invalid Content-Range."
            )
        start, end, total = (int(value) for value in match.groups())
        if (
            start != offset
            or not start <= end < total
            or (length is not None and length != end - start + 1)
        ):
            raise DownloadError(
                DownloadFailure.TRANSIENT, "The server sent an inconsistent Content-Range."
            )
        response_end = end + 1
    elif response.status == 200:
        total = length
        response_end = length
    else:
        status = response.status
        if status in {404, 410}:
            reason = DownloadFailure.NOT_FOUND
        elif status in {408, 425, 429} or status >= 500:
            reason = DownloadFailure.TRANSIENT
        else:
            reason = DownloadFailure.HTTP
        raise DownloadError(reason, f"The download server returned HTTP {status}.")
    if total is not None and (
        not 0 < total <= transfer.limit
        or (transfer.size_bytes is not None and total != transfer.size_bytes)
    ):
        raise DownloadError(
            DownloadFailure.INTEGRITY, "The server reported an unexpected file size."
        )
    return transfer.size_bytes or total, response_end


async def _stream(
    session: aiohttp.ClientSession,
    transfer: _Transfer,
    partial: Path,
    offset: int,
    progress: ProgressCallback | None,
    cancel: Event | None,
    timeout: float,
) -> int:
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request_timeout = aiohttp.ClientTimeout(
        sock_connect=timeout, sock_read=timeout, connect=timeout
    )
    while True:
        check_cancel(cancel)
        async with session.get(
            transfer.url, headers=headers, auto_decompress=False, timeout=request_timeout
        ) as response:
            # An unsatisfiable range can be a stale partial. Retry once from zero.
            if response.status == 416 and "Range" in headers:
                del headers["Range"]
                continue
            resumed = offset if "Range" in headers and response.status == 206 else 0
            total, response_end = _response_length(response, resumed, transfer)
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise DownloadError(
                    DownloadFailure.TRANSIENT, "The server did not send identity encoding."
                )
            if total is not None:
                _check_space(partial.parent, total - resumed, reclaim=offset if not resumed else 0)
            done = resumed
            rate = 0.0
            last_tick = time.monotonic()
            check_cancel(cancel)
            if progress is not None:
                progress(Progress(done, total, rate))
            check_cancel(cancel)
            oversized = False
            with partial.open("ab" if resumed else "wb") as output:
                async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
                    check_cancel(cancel)
                    if done + len(chunk) > (response_end or total or transfer.limit):
                        oversized = True
                        break
                    output.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        now = time.monotonic()
                        sample = len(chunk) / max(now - last_tick, 0.001)
                        rate = (rate * 0.8 + sample * 0.2) if rate else sample
                        last_tick = now
                        progress(Progress(done, total, rate))
            if oversized:
                partial.unlink(missing_ok=True)
                raise DownloadError(
                    DownloadFailure.INTEGRITY, "The download exceeded the expected size."
                )
            check_cancel(cancel)
            if (total is not None and done != total) or (
                response_end is not None and done != response_end
            ):
                raise DownloadError(
                    DownloadFailure.TRANSIENT, "The download ended early. Retry to resume it."
                )
            return done


async def _fetch(
    transfer: _Transfer, progress: ProgressCallback | None, cancel: Event | None, timeout: float
) -> Path:
    path = transfer.target
    partial = path.with_suffix(path.suffix + ".part")
    try:
        check_cancel(cancel)
        existing = _file_size(path)
        if existing is not None:
            if transfer.verify(path, existing):
                return path
            path.unlink()
        offset = _file_size(partial) or 0
        if offset and transfer.verify(partial, offset):
            check_cancel(cancel)
            partial.replace(path)
            return path
        if offset >= (transfer.size_bytes or transfer.limit):
            partial.unlink()
            offset = 0
        if transfer.size_bytes is not None:
            _check_space(path.parent, transfer.size_bytes - offset)
        async with create_http_session() as session:
            stream = _stream(session, transfer, partial, offset, progress, cancel, timeout)
            if cancel is None:
                done = await stream
            else:
                # The event can be set by the UI even while the connection is stalled.
                task = asyncio.create_task(stream)
                try:
                    while not task.done():
                        check_cancel(cancel)
                        await asyncio.wait((task,), timeout=0.1)
                    done = task.result()
                finally:
                    if not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
        if not transfer.verify(partial, done):
            partial.unlink()
            raise DownloadError(
                DownloadFailure.INTEGRITY,
                "The downloaded file failed size, format or SHA-256 verification.",
            )
        check_cancel(cancel)
        partial.replace(path)
        return path
    except (aiohttp.ClientError, TimeoutError) as exc:
        check_cancel(cancel)
        raise DownloadError(DownloadFailure.TRANSIENT, f"Download interrupted: {exc}") from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in {39, 112}:
            raise DownloadError(
                DownloadFailure.DISK_SPACE, "The content cache ran out of disk space."
            ) from exc
        raise


async def download_pack(
    cache: PackCache,
    pack: Pack,
    *,
    app_version: str,
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
    timeout: float = 30,
) -> Path:
    """Fetch and verify one exact version on the caller's worker loop."""
    if pack.withheld or not all(
        field.strip() for field in (pack.author, pack.license, pack.attribution)
    ):
        raise ValueError("A content pack requires author, licence and attribution information.")
    if not is_compatible(pack, app_version):
        raise DownloadError(
            DownloadFailure.INCOMPATIBLE, f"This pack requires app version {pack.min_app_version}."
        )
    if not VERSION.fullmatch(pack.version) or not SHA256.fullmatch(pack.sha256):
        raise ValueError("Invalid pack version or SHA-256.")
    with cache._work(_asset_key("pack", pack.id)) as folder:
        active = folder / ".downloading"
        active.touch()
        try:
            transfer = _Transfer(
                pack.download_url,
                pack.sha256,
                pack.size_bytes,
                folder / f"{pack.version}--{pack.sha256}.zip",
            )
            return await _fetch(transfer, progress, cancel, timeout)
        finally:
            active.unlink(missing_ok=True)


async def fetch_preview(
    cache: PackCache,
    pack: Pack,
    preview: Preview,
    *,
    progress: ProgressCallback | None = None,
    cancel: Event | None = None,
    timeout: float = 30,
) -> Path:
    """Fetch a declared preview lazily and return its verified local image."""
    if (
        pack.withheld
        or not pack.license.strip()
        or not pack.attribution.strip()
        or preview not in pack.previews
        or not SHA256.fullmatch(preview.sha256)
    ):
        raise ValueError("The preview must be declared by an attributed pack.")
    with cache._work(_asset_key("preview", pack.id)) as folder:
        transfer = _Transfer(
            preview.url,
            preview.sha256,
            preview.size_bytes,
            folder / f"{preview.sha256}.image",
            True,
        )
        return await _fetch(transfer, progress, cancel, timeout)
