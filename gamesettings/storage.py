"""Small atomic state files scoped to the local device and selected installation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from gamesettings.options import OPTIONS, PROFILES, Profile, normalize
from state_lock import locked


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def directory(config_dir: Path) -> Path:
    node = uuid.getnode()
    # getnode marks its random fallback with the multicast bit. A random
    # identifier would strand profiles after restarting the companion.
    identity = platform.node() if node & (1 << 40) else str(node)
    return config_dir / "game-display" / digest("|".join((platform.system(), identity)))


def atomic(path: Path, data: bytes, guard: Callable[[Path], None] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".display-", delete=False) as stream:
        staged = Path(stream.name)
    try:
        with staged.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if guard:
            guard(staged)
        staged.replace(path)
        if path.read_bytes() != data:
            raise OSError("The saved display settings could not be verified. Reload to check them.")
    finally:
        staged.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    if not isinstance(obj, dict):
        raise ValueError("The saved display settings could not be read.")
    return obj


def write_json(path: Path, obj: dict) -> None:
    atomic(path, json.dumps(obj, ensure_ascii=True).encode())


def profiles(config_dir: Path) -> tuple[Profile, ...]:
    obj = read_json(directory(config_dir) / "profiles.json")
    if any(not isinstance(values, dict) for values in obj.values()):
        raise ValueError("Could not read a saved device profile.")
    return tuple(
        Profile(
            name,
            "Saved on this device. Review the values before applying.",
            {k: normalize(k, v) for k, v in values.items()},
        )
        for name, values in obj.items()
    )


def available_profiles(config_dir: Path) -> tuple[tuple[Profile, ...], str]:
    try:
        return profiles(config_dir), ""
    except (ValueError, OSError):
        # Recovery belongs to the game and must remain reachable if local profiles are damaged.
        return (), "Couldn’t read saved profiles on this device. Built-in profiles are available."


def save_profile(config_dir: Path, name: str, values: dict[str, str]) -> None:
    name = name.strip()
    if not 1 <= len(name) <= 48 or any(ord(c) < 32 for c in name):
        raise ValueError("Give the profile a name between 1 and 48 characters.")
    if not values or not values.keys() <= OPTIONS.keys():
        raise ValueError("Choose at least one display setting to save.")
    path = directory(config_dir) / "profiles.json"
    with locked(path.parent, ".profiles.lock"):
        obj = read_json(path)
        names = [*obj, *(p.name for p in PROFILES), "Current settings"]
        if name.casefold() in {n.casefold() for n in names}:
            raise ValueError("This profile name is in use. Choose another name.")
        obj[name] = {k: normalize(k, v) for k, v in values.items()}
        write_json(path, obj)
