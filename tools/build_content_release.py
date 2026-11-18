"""Build a local release from your media folders, previews and packs.json."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from content.archives import shared_files, skin_files  # noqa: E402
from content.cache import file_sha256  # noqa: E402
from content.download import preview_format  # noqa: E402
from content.layouts import LAYOUTS, MAX_AUDIO, MAX_FILES, MAX_TOTAL, staged_items  # noqa: E402
from content.manifest import (  # noqa: E402
    DEFAULT_REPOSITORY,
    MAX_PREVIEW_BYTES,
    PACK_ID,
    SCHEMA_VERSION,
    VERSION,
    parse_manifest,
)

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*")
_PREVIEW_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*\.(?:png|jpg|jpeg|webp)", re.IGNORECASE)
_GENERATED_FIELDS = {"download_url", "size_bytes", "sha256", "previews"}


def _validate_archive(path: Path, info: dict) -> None:
    if info["id"] == "shared" and info["kind"] != "sound-effects":
        raise ValueError("The shared ID is reserved for the legacy appearance dependency.")
    if info["kind"] in LAYOUTS:
        with staged_items(path, info["kind"], path.parent):
            return
    if info["kind"] == "sound-effects" and info["id"] != "shared":
        raise ValueError("The sound-effects layout is reserved for a future release.")
    with ZipFile(path) as archive:
        if info["kind"] == "skin":
            skin_files(archive, info["title"])
        else:
            shared_files(archive)
        if corrupt := archive.testzip():
            raise ValueError(f"ZIP checksum failed: {corrupt}.")


def _stage_preview(source: Path, stage: Path, name: str, release_url: str) -> dict:
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Expected a regular preview file: {source.name}.")
    with source.open("rb") as handle:
        data = handle.read(MAX_PREVIEW_BYTES + 1)
    actual_format = preview_format(data[:12])
    claimed_format = source.suffix.lower().removeprefix(".").replace("jpeg", "jpg")
    if len(data) > MAX_PREVIEW_BYTES or actual_format != claimed_format:
        raise ValueError(f"Preview {source.name} must be a PNG, JPEG or WebP image under 1 MiB.")
    staged = stage / f"{name}.{actual_format}"
    staged.write_bytes(data)
    return {
        "url": f"{release_url}/{staged.name}",
        "sha256": file_sha256(staged),
        "size_bytes": len(data),
    }


def _zip_folder(source: Path, target: Path) -> None:
    root = source.absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError(f"Expected a regular pack folder: {source.name}.")
    count = size = 0
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path == target.absolute():
                raise ValueError("Choose an output folder outside the input pack.")
            count += 1
            if path.resolve() != path or count > MAX_FILES:
                raise ValueError("Linked paths or too many files in pack folder.")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"Expected an ordinary file: {path}")
            length = path.stat().st_size
            size += length
            if size > MAX_TOTAL or length > MAX_AUDIO:
                raise ValueError("Pack folder exceeds the size limit.")
            archive.write(path, path.relative_to(root).as_posix())


def _stage_pack(
    metadata: dict, folder: Path, preview_root: Path, stage: Path, release_url: str
) -> dict:
    if not isinstance(metadata, dict) or metadata.keys() & _GENERATED_FIELDS:
        raise ValueError(
            "Each packs.json entry must be an object with only editorial fields and preview_files."
        )
    info = dict(metadata)
    pack_id, version = info.get("id"), info.get("version")
    if not isinstance(pack_id, str) or not PACK_ID.fullmatch(pack_id):
        raise ValueError("Each entry needs a valid pack id.")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError(f"{pack_id}: invalid version.")
    preview_files = info.pop("preview_files", None)
    if not isinstance(preview_files, list) or not preview_files:
        raise ValueError(f"{pack_id}: preview_files must be a nonempty array.")
    if any(
        not isinstance(name, str) or not _PREVIEW_NAME.fullmatch(name) for name in preview_files
    ):
        raise ValueError(f"{pack_id}: preview_files must contain PNG, JPEG or WebP filenames.")
    source = folder / f"{pack_id}.zip"
    staged = stage / f"{pack_id}.{version}.zip"
    plain = folder / pack_id
    if plain.exists() and source.exists():
        raise ValueError(f"Choose either {pack_id}/ or {pack_id}.zip.")
    if plain.is_dir():
        _zip_folder(plain, staged)
    elif source.is_file() and not source.is_symlink():
        shutil.copyfile(source, staged)
    else:
        raise ValueError(f"Expected a folder named {pack_id} or a regular {source.name}.")
    info.update(
        download_url=f"{release_url}/{staged.name}",
        size_bytes=staged.stat().st_size,
        sha256=file_sha256(staged),
        previews=[
            _stage_preview(preview_root / name, stage, f"{pack_id}.{version}-{index}", release_url)
            for index, name in enumerate(preview_files, start=1)
        ],
    )
    parse_manifest(json.dumps({"schema_version": SCHEMA_VERSION, "packs": [info]}))
    try:
        _validate_archive(staged, info)
    except (BadZipFile, RuntimeError, ValueError) as exc:
        raise ValueError(f"{source.name}: {exc}") from exc
    return info


def build_release(
    folder: Path,
    out: Path,
    *,
    repo: str,
    tag: str,
    preview_root: Path | None = None,
    base_manifest: Path | None = None,
    draft: bool = False,
) -> Path:
    """Stage the ZIPs, previews and manifest; return the manifest path."""
    if not _REPO.fullmatch(repo) or not _TAG.fullmatch(tag):
        raise ValueError(
            "Supply a GitHub owner/repository and a tag made of letters, digits, dots, hyphens or underscores."
        )
    metadata = json.loads((folder / "packs.json").read_bytes())
    if not isinstance(metadata, list) or not metadata:
        raise ValueError("packs.json must contain a nonempty array of pack metadata.")
    if out.exists():
        raise ValueError(f"Output already exists: {out}. Choose a new output directory.")
    ids = [entry.get("id") for entry in metadata if isinstance(entry, dict)]
    ids = [pack_id for pack_id in ids if isinstance(pack_id, str)]
    if len(set(ids)) != len(ids):
        raise ValueError("packs.json contains duplicate pack ids.")
    if not {path.name for path in folder.glob("*.zip")} <= {f"{pack_id}.zip" for pack_id in ids}:
        raise ValueError("packs.json must describe every ZIP in the pack folder.")
    previous = []
    if base_manifest is not None:
        body = base_manifest.read_bytes()
        if parse_manifest(body).schema_version != SCHEMA_VERSION:
            raise ValueError("The base manifest must use the publisher's schema version.")
        previous = [entry for entry in json.loads(body)["packs"] if entry["id"] not in ids]
    if not draft:
        metadata = [
            entry
            for entry in metadata
            if not isinstance(entry, dict) or entry.get("withheld") is not True
        ]
        previous = [entry for entry in previous if not entry.get("withheld")]
        if not metadata and not previous:
            raise ValueError("Withheld entries require --draft and have no upload commands.")
    release_url = f"https://github.com/{repo}/releases/download/{tag}"
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as temporary:
        stage = Path(temporary) / "release"
        stage.mkdir()
        new = [
            _stage_pack(entry, folder, preview_root or folder, stage, release_url)
            for entry in metadata
        ]
        body = (
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "packs": [*previous, *new]},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        parse_manifest(body)
        (stage / "manifest.json").write_bytes(body.encode("utf-8"))
        # A failed validation never leaves a partially staged release.
        stage.rename(out)
    return out / "manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Getting started\n"
            "  1. Make a folder per pack ID. Put tracks and covers under each game ID,\n"
            "     or put wallpaper and sleeve pictures under images/.\n"
            "  2. List them in that folder's pack.json. Put credits and preview names\n"
            "     in packs.json beside the folders. Examples are in docs/content-packs.md.\n"
            "  3. Choose a new output folder. Add --draft to review withheld packs.\n"
            "     Without --draft, only publishable assets get upload commands.\n"
            "     This tool stages files on your computer. Nothing is uploaded."
        ),
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="folder containing packs.json, previews, and a subfolder (or ZIP) named for each pack ID",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="new directory for the staged release"
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REPOSITORY, help="GitHub repository as owner/name"
    )
    parser.add_argument("--tag", default="content-v1", help="tag for the new content release")
    parser.add_argument(
        "--preview-root", type=Path, help="directory containing previews (default: pack folder)"
    )
    parser.add_argument(
        "--base-manifest", type=Path, help="retain other packs from a previous v1 manifest"
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="stage for offline review without printing upload commands",
    )
    args = parser.parse_args(argv)
    try:
        manifest = build_release(
            args.folder,
            args.out,
            repo=args.repo,
            tag=args.tag,
            preview_root=args.preview_root,
            base_manifest=args.base_manifest,
            draft=args.draft,
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(f"Generated {manifest}")
    if args.draft:
        print("Local draft for review.")
    else:
        print(f"Run these commands from {manifest.parent}:")
        print(f"gh release create {args.tag} --repo {args.repo} --title {args.tag} --draft")
        for path in sorted(manifest.parent.iterdir()):
            print(f"gh release upload {args.tag} {path.name} --repo {args.repo}")
        print(f"gh release edit {args.tag} --repo {args.repo} --draft=false --latest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
