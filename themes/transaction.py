"""One installation-wide write-ahead journal. A pending operation rolls forward."""

import errno
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import extras

from .model import check_id
from .skin import FONT_FILES
from .widgets import WIDGETS

ALLOWED = {"skin.xml", "textures/bg.png", *FONT_FILES, *(w + ".png" for w in WIDGETS)}
KEYS = {"skin", "textfont", "numfont"}
MARKER = ".edopro-studio-owner"
STATE = "config/.edopro-studio-themes"


def safe_file(root: Path, name: str) -> Path:
    return extras._regular_path(root, name)


def read_json(path: Path) -> dict:
    if path.stat().st_size > 64000:
        raise ValueError("Oversized theme install record.")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("Invalid theme install record.")
    return data


def write_json(path: Path, data: dict) -> None:
    encoded = json.dumps(data).encode()
    if len(encoded) > 64000:
        raise ValueError("Oversized theme install record.")
    extras._atomic_write(path, encoded)


def check_settings(settings: dict) -> None:
    if not isinstance(settings, dict) or settings.keys() - KEYS:
        raise ValueError("Unexpected theme setting.")
    for value in settings.values():
        if value is not None and (not isinstance(value, str) or "\n" in value or "\r" in value):
            raise ValueError("Invalid saved theme setting.")


def check_pairs(pairs: dict) -> None:
    if not isinstance(pairs, dict):
        raise ValueError("Invalid activation record.")
    for key, pair in pairs.items():
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("Invalid activation record.")
        for value in pair:
            check_settings({key: value})


def check_manifest(theme_id: str, manifest: dict | None) -> None:
    if manifest is None:
        return
    if not re.fullmatch("Studio-" + theme_id + r"(?:-[1-9][0-9]*)?", manifest["folder"]):
        raise ValueError("Invalid skin folder in theme record.")
    check_id(manifest["owner"])
    if not isinstance(manifest["dirs"], list) or set(manifest["dirs"]) - {"fonts", "textures"}:
        raise ValueError("Unexpected directory in theme manifest.")
    files = manifest["files"]
    if not isinstance(files, dict) or "skin.xml" not in files or files.keys() - ALLOWED:
        raise ValueError("Unexpected file in theme manifest.")
    if any(not re.fullmatch("[0-9a-f]{64}", value) for value in files.values()):
        raise ValueError("Invalid file digest.")
    check_settings(manifest["applied"])
    if manifest["previous"].keys() != manifest["applied"].keys():
        raise ValueError("Missing previous theme setting.")
    for key, candidates in manifest["previous"].items():
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Invalid previous selection.")
        for value in candidates:
            check_settings({key: value})


def check_record(theme_id: str, record: dict) -> None:
    check_manifest(theme_id, record["current"])
    if record["undo"] is not None:
        check_manifest(theme_id, record["undo"]["prior"])
        check_pairs(record["undo"]["settings"])


def discard_empty(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise


class Store:
    def __init__(self, root: Path, theme_id: str) -> None:
        self.root = root
        self.id = check_id(theme_id)
        self.record_path = safe_file(root, f"{STATE}/{theme_id}/record.json")
        self.pending = safe_file(root, f"{STATE}/pending.json")
        self.conf = safe_file(root, "config/system.conf")

    def record(self) -> dict:
        if not self.record_path.exists():
            return {"current": None, "undo": None}
        record = read_json(self.record_path)
        check_record(self.id, record)
        return record

    def blob(self, digest: str) -> Path:
        return safe_file(self.root, f"{STATE}/{self.id}/objects/{digest}")

    def stage(self, files: dict[str, bytes]) -> dict[str, str]:
        hashes = {}
        for name, data in files.items():
            digest = hashlib.sha256(data).hexdigest()
            path = self.blob(digest)
            if extras._signature(path) != digest:
                extras._atomic_write(path, data)
            hashes[name] = digest
        return hashes

    def config(self) -> tuple[bytes, dict[str, str]]:
        content = self.conf.read_bytes() if self.conf.exists() else b""
        return content, extras._settings(content.removeprefix(b"\xef\xbb\xbf"))

    def patch_settings(self, changes: dict) -> None:
        content, current = self.config()
        patch = {
            k: after
            for k, (before, after) in changes.items()
            if before != after and current.get(k) == before
        }
        if patch:
            if self.config()[0] != content:
                raise OSError("EDOPro settings changed. Close the game and retry.")
            extras._atomic_write(self.conf, extras._update_settings(content, patch))

    def folder(self, manifest: dict) -> Path:
        return safe_file(self.root, "skin/" + manifest["folder"] + "/" + MARKER).parent

    def verify(self, manifest: dict) -> None:
        folder = self.folder(manifest)
        if (folder / MARKER).read_bytes() != manifest["owner"].encode():
            raise ValueError("The installed skin's ownership marker changed.")
        for name, digest in manifest["files"].items():
            if extras._signature(safe_file(folder, name)) != digest:
                raise ValueError(f"{name} changed outside Studio. Its contents have been kept.")

    def claim(self, folder: Path, owner: str) -> None:
        marker = safe_file(folder, MARKER)
        if folder.exists():
            if not marker.is_file() or marker.read_bytes() != owner.encode():
                raise ValueError("The skin directory belongs to another installation.")
            return
        # Publish an already marked directory, without an unowned mkdir/marker gap.
        staged = self.record_path.parent / ("claim-" + owner)
        extras._atomic_write(safe_file(staged, MARKER), owner.encode())
        folder.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(folder)

    def commit(self, action: str, old: dict | None, record: dict, settings: dict) -> None:
        op = {"action": action, "id": self.id, "old": old, "record": record, "settings": settings}
        if old:
            self.verify(old)
        new = record["current"]
        if new:
            folder = self.folder(new)
            if not old and folder.exists():
                raise ValueError("Cannot take over an existing skin directory.")
            for name in new["files"].keys() - (old["files"].keys() if old else set()):
                if safe_file(folder, name).exists():
                    raise ValueError(f"{name} already exists and is not owned by Studio.")
        write_json(self.pending, op)
        self.finish(op)

    def finish(self, op: dict) -> None:
        old, record, settings = op["old"], op["record"], op["settings"]
        check_manifest(self.id, old)
        check_record(self.id, record)
        check_pairs(settings)
        new = record["current"]
        before, after = (old["files"] if old else {}), (new["files"] if new else {})
        folder = self.folder(new or old)
        if new and old and (new["folder"], new["owner"]) != (old["folder"], old["owner"]):
            raise ValueError("Mismatched skin ownership records.")
        if new:
            self.claim(folder, new["owner"])
        elif folder.exists():
            marker = safe_file(folder, MARKER)
            if marker.exists() and marker.read_bytes() != old["owner"].encode():
                raise ValueError("The skin folder's ownership changed.")
            if not marker.exists() and any(safe_file(folder, k).exists() for k in before):
                raise ValueError("Missing ownership marker for installed files.")
        todo = []
        for name in sorted(before.keys() | after.keys()):
            path = safe_file(folder, name)
            actual = extras._signature(path)
            if actual == after.get(name):
                continue
            if actual != before.get(name):
                raise ValueError(f"{name} changed outside Studio. Its contents have been kept.")
            digest = after.get(name)
            if digest and extras._signature(self.blob(digest)) != digest:
                raise OSError(f"The staged copy of {name} is missing or damaged.")
            todo.append((name, actual, digest))
        if not new:
            self.patch_settings(settings)
        for name, expected, digest in todo:
            path = safe_file(folder, name)
            if extras._signature(path) != expected:
                raise OSError(f"{name} changed during installation.")
            if digest:
                data = self.blob(digest).read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise OSError(f"The staged copy of {name} changed.")
                extras._atomic_write(path, data)
            else:
                path.unlink()
        if new:
            self.patch_settings(settings)
        old_dirs = set(old["dirs"]) if old else set()
        new_dirs = set(new["dirs"]) if new else set()
        for parent in old_dirs - new_dirs:
            discard_empty(folder / parent)
        if not new:
            safe_file(folder, MARKER).unlink(missing_ok=True)
            discard_empty(folder)
        write_json(self.record_path, record)
        self.pending.unlink()
        self.collect(record)

    def collect(self, record: dict) -> None:
        retained = set()
        for manifest in (record["current"], (record["undo"] or {}).get("prior")):
            if manifest:
                retained.update(manifest["files"].values())
        for path in (self.record_path.parent / "objects").glob("*"):
            if re.fullmatch("[0-9a-f]{64}", path.name) and path.name not in retained:
                safe_file(path.parent, path.name).unlink()


def recover_locked(root: Path) -> dict[str, Any] | None:
    path = safe_file(root, STATE + "/pending.json")
    if path.exists():
        op = read_json(path)
        Store(root, op["id"]).finish(op)
        return op
    return None
