"""The versioned wire format for Studio's content catalogue."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
DEFAULT_REPOSITORY = "cntrl-alt-lenny/edopro-studio-content"
DEFAULT_MANIFEST_URL = (
    f"https://github.com/{DEFAULT_REPOSITORY}/releases/latest/download/manifest.json"
)
MANIFEST_URL_ENV = "EDOPRO_CONTENT_MANIFEST_URL"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PREVIEW_BYTES = 1024 * 1024
KINDS = frozenset({"skin", "wallpaper", "sound-effects", "music", "sleeves", "covers"})
PACK_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SHA256 = re.compile(r"[a-f0-9]{64}")
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")
PACK_FIELDS = frozenset(
    {
        "id",
        "kind",
        "title",
        "description",
        "author",
        "license",
        "attribution",
        "source_url",
        "version",
        "download_url",
        "size_bytes",
        "sha256",
        "previews",
        "min_app_version",
        "withheld",
    }
)
PREVIEW_FIELDS = frozenset({"url", "sha256", "size_bytes"})


class ManifestError(ValueError):
    """A malformed or unsupported catalogue."""


@dataclass(frozen=True, slots=True)
class Preview:
    url: str
    sha256: str
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class Pack:
    id: str
    kind: str
    title: str
    description: str
    author: str
    license: str
    attribution: str
    source_url: str
    version: str
    download_url: str
    size_bytes: int
    sha256: str
    previews: tuple[Preview, ...]
    min_app_version: str
    withheld: bool = False


@dataclass(frozen=True, slots=True)
class Manifest:
    schema_version: int
    packs: tuple[Pack, ...]

    def get(self, pack_id: str) -> Pack | None:
        return next((pack for pack in self.packs if pack.id == pack_id), None)


def version_key(value: str) -> tuple[int, ...]:
    """Compare the numeric components in a release version."""
    return tuple(map(int, value.split(".")))


def is_compatible(pack: Pack, app_version: str) -> bool:
    return bool(VERSION.fullmatch(app_version)) and (
        version_key(app_version) >= version_key(pack.min_app_version)
    )


def validate_url(value: str, name: str) -> str:
    try:
        parts = urlsplit(value)
        if (
            not any(char.isspace() or ord(char) < 32 for char in value)
            and "\\" not in value
            and parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and not parts.fragment
            and (parts.port is None or parts.port > 0)
        ):
            return value
    except ValueError:
        pass
    raise ManifestError(
        f"{name} must be an absolute HTTP(S) URL without credentials or a fragment."
    )


def _object(
    value: object, path: str, fields: frozenset[str], forward: bool, optional: tuple[str, ...] = ()
) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{path} must be an object.")
    if missing := fields - value.keys() - set(optional):
        raise ManifestError(f"{path} is missing {', '.join(sorted(missing))}.")
    if not forward and (unknown := value.keys() - fields):
        raise ManifestError(f"{path} has unknown fields: {', '.join(sorted(unknown))}.")
    return value


def _string(obj: dict, field: str, path: str, pattern: re.Pattern | None = None) -> str:
    value = obj[field]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestError(f"{path}.{field} must be nonempty text without surrounding whitespace.")
    if pattern and not pattern.fullmatch(value):
        raise ManifestError(f"{path}.{field} has an invalid format.")
    return value


def _size(value: object, path: str, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise ManifestError(f"{path} must be an integer between 1 and {maximum}.")
    return value


def _preview(value: object, path: str, forward: bool) -> Preview:
    obj = _object(value, path, PREVIEW_FIELDS, forward, ("size_bytes",))
    size = (
        _size(obj["size_bytes"], f"{path}.size_bytes", MAX_PREVIEW_BYTES)
        if "size_bytes" in obj
        else None
    )
    return Preview(
        validate_url(_string(obj, "url", path), f"{path}.url"),
        _string(obj, "sha256", path, SHA256),
        size,
    )


def _pack(value: object, path: str, forward: bool) -> Pack | None:
    # A future kind may use an entirely different payload and preview format.
    if (
        forward
        and isinstance(value, dict)
        and isinstance(kind := value.get("kind"), str)
        and kind
        and kind not in KINDS
    ):
        return None
    obj = _object(value, path, PACK_FIELDS, forward, ("withheld",))
    kind = _string(obj, "kind", path)
    if kind not in KINDS:
        raise ManifestError(f"{path}.kind is unknown: {kind!r}.")
    previews = obj["previews"]
    if not isinstance(previews, list) or not previews:
        raise ManifestError(f"{path}.previews must be a nonempty array.")
    withheld = obj.get("withheld", False)
    if type(withheld) is not bool:
        raise ManifestError(f"{path}.withheld must be a boolean.")
    return Pack(
        id=_string(obj, "id", path, PACK_ID),
        kind=kind,
        title=_string(obj, "title", path),
        description=_string(obj, "description", path),
        author=_string(obj, "author", path),
        license=_string(obj, "license", path),
        attribution=_string(obj, "attribution", path),
        source_url=validate_url(_string(obj, "source_url", path), f"{path}.source_url"),
        version=_string(obj, "version", path, VERSION),
        download_url=validate_url(_string(obj, "download_url", path), f"{path}.download_url"),
        size_bytes=_size(obj["size_bytes"], f"{path}.size_bytes"),
        sha256=_string(obj, "sha256", path, SHA256),
        previews=tuple(
            _preview(preview, f"{path}.previews[{i}]", forward)
            for i, preview in enumerate(previews)
        ),
        min_app_version=_string(obj, "min_app_version", path, VERSION),
        withheld=withheld,
    )


def _unique_object(items: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in items:
        if key in obj:
            raise ManifestError(f"Duplicate JSON field: {key!r}.")
        obj[key] = value
    return obj


def _reject_constant(value: str) -> None:
    raise ManifestError(f"Invalid JSON constant: {value}.")


def parse_manifest(data: bytes | str) -> Manifest:
    """Validate a manifest, filtering withheld packs and unknown future kinds."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("The manifest exceeds the size limit.")
    try:
        obj = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except ManifestError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ManifestError(f"Invalid manifest JSON: {exc}") from exc
    schema = obj.get("schema_version") if isinstance(obj, dict) else None
    if type(schema) is not int or schema < 1:
        raise ManifestError("manifest.schema_version must be a positive integer.")
    forward = schema > SCHEMA_VERSION
    packs = _object(obj, "manifest", frozenset({"schema_version", "packs"}), forward)["packs"]
    if not isinstance(packs, list):
        raise ManifestError("manifest.packs must be an array.")
    seen = set()
    visible = []
    for index, item in enumerate(packs):
        pack = _pack(item, f"packs[{index}]", forward)
        if pack is None:
            continue
        if pack.id in seen:
            raise ManifestError(f"Duplicate pack id: {pack.id!r}.")
        seen.add(pack.id)
        if not pack.withheld:
            visible.append(pack)
    return Manifest(schema, tuple(visible))
