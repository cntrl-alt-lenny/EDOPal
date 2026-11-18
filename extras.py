"""Install optional visuals and sounds, retaining the files that they replace."""

import hashlib
import json
import os
import re
from collections.abc import Callable
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree
from zipfile import ZipFile

from content.archives import pack_files
from content.cache import PackCache
from content.manifest import Pack
from state_lock import appearance_writer

ASSETS = Path(__file__).resolve().parent / "assets" / "extras"
PACKS = ASSETS.parent / "appearance"
SKINS = ("Abyss", "Moon", "Nebula", "Ristretto")
SLEEVES = {"Vortex": "textures/cover.png", "1998 OCG": "textures/cover2.png"}
FONT_SETTINGS = {"textfont": "fonts/InterEDO-Regular.ttf 12", "numfont": "fonts/Inter-Medium.ttf"}
SETTING = re.compile(rb"(?m)^([ \t]*)(skin|textfont|numfont)([ \t]*=[ \t]*)([^\r\n]*)")
# These two colours draw the current player's LP frame and its background.
TURN_ACCENTS = {
    "Abyss": ((205, 112, 199, 187), (134, 35, 90, 86)),
    "Moon": ((201, 201, 193, 216), (130, 78, 72, 91)),
    "Nebula": ((211, 153, 168, 238), (139, 56, 63, 116)),
    "Ristretto": ((210, 227, 179, 132), (137, 99, 66, 47)),
}
_TURN_ELEMENTS = ("DUELFIELD_TURNPLAYER_OUTLINE_COLOR", "DUELFIELD_TURNPLAYER_COLOR")
_COLOR_ATTR = re.compile(rb"(\s([argb])\s*=\s*)([\"'])(\d+)\3")


def _backup_dir(root: Path, config_path: str | Path) -> Path:
    install_id = hashlib.sha256(os.fsencode(root)).hexdigest()[:16]
    return Path(config_path).resolve().parent / "extras-backups" / install_id


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        staged.write_bytes(data)
        staged.chmod(path.stat().st_mode if path.is_file() else 0o644)
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def _publish(root: Path, backups: Path, relative: str | Path, content: bytes) -> None:
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError(f"Invalid asset path: {relative}")
    destination = root / relative
    if not destination.resolve().is_relative_to(root):
        raise OSError(f"Cannot install outside the selected EDOPro folder: {relative}")
    if destination.is_file():
        previous = destination.read_bytes()
        if previous == content:
            return
        revision = hashlib.sha256(previous).hexdigest()[:16]
        backup = backups / revision / relative
        if not backup.exists():
            _atomic_write(backup, previous)
    _atomic_write(destination, content)


@appearance_writer
def install(edopro_path: str, config_path: str, group: str) -> None:
    """Install the original Midnight Circuit and synth extras."""
    _install(edopro_path, config_path, group)


def _install(edopro_path: str, config_path: str, group: str) -> None:
    root = Path(edopro_path).resolve()
    backups = _backup_dir(root, config_path)
    directory = ASSETS / group
    if group == "sound":
        _install_effects(root, config_path, "Synth")
        directory /= "BGM"
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"The optional {group} files are missing from this build.")
    for source in files:
        _publish(root, backups, source.relative_to(ASSETS), source.read_bytes())


def _pack_sources(archive: ZipFile, prefix: str) -> dict[str, str]:
    return {
        name: source
        for name, source in (
            [(name, name) for name in archive.namelist()]
            + list(json.loads(archive.read("duplicates.json")).items())
        )
        if name.startswith(prefix) and not name.endswith("/")
    }


def _install_pack(
    root: Path,
    backups: Path,
    pack: str,
    prefix: str,
    *,
    turn_colors: tuple[tuple[int, ...], ...] | None = None,
    archive_path: Path | None = None,
) -> set[str]:
    with ZipFile(archive_path or PACKS / f"{pack}.zip") as archive:
        if archive_path is None:
            files = _pack_sources(archive, prefix)
        else:
            files = {
                name: source
                for name, source in pack_files(archive).items()
                if name.startswith(prefix)
            }
        if not files:
            raise ValueError(f"No {prefix} files in the {pack} pack.")
        if (
            archive_path is not None
            and prefix.startswith("skin/")
            and prefix + "skin.xml" not in files
        ):
            raise ValueError(f"No skin.xml in the {pack} pack.")
        for name, source in files.items():
            data = archive.read(source)
            if turn_colors is not None and name == prefix + "skin.xml":
                data = _recolor_turn(data, turn_colors)
            _publish(root, backups, name, data)
        return set(files)


def _skin_files(
    root: Path,
    backups: Path,
    skin: str,
    highlight_turn: bool | None,
    cache: PackCache,
    exact_skin: Pack | None,
    exact_shared: Pack | None,
) -> str:
    pack_id = exact_skin.id if exact_skin else skin.lower()
    prefix = f"skin/Monokai {skin}/"
    relative = prefix + "skin.xml"
    with ExitStack() as locks:
        skin_path = locks.enter_context(cache.use_pack(pack_id, exact=exact_skin))
        if exact_skin and skin_path is None:
            raise FileNotFoundError(f"Download {exact_skin.title} before applying it.")
        if (
            exact_skin is None
            and highlight_turn is not None
            and current_skin(str(root)) == "Monokai " + skin
        ):
            installed = _regular_path(root, relative)
            if installed.exists() and turn_highlight_enabled(root, skin) != highlight_turn:
                with ZipFile(skin_path or PACKS / f"{pack_id}.zip") as pack:
                    colors = (
                        TURN_ACCENTS[skin] if highlight_turn else _turn_colors(pack.read(relative))
                    )
                _publish(root, backups, relative, _recolor_turn(installed.read_bytes(), colors))
                # Keep local preferences when only the active skin's highlight changes.
                return ""
        shared_path = locks.enter_context(cache.use_pack("shared", exact=exact_shared))
        if exact_shared and shared_path is None:
            raise FileNotFoundError(f"Download {exact_shared.title} before applying this skin.")
        _install_pack(
            root,
            backups,
            pack_id,
            prefix,
            turn_colors=TURN_ACCENTS[skin] if highlight_turn else None,
            archive_path=skin_path,
        )
        fonts = _install_pack(root, backups, "shared", "fonts/", archive_path=shared_path)
        if "fonts/Inter-OFL.txt" not in fonts:
            _publish(root, backups, "fonts/Inter-OFL.txt", (PACKS / "Inter-OFL.txt").read_bytes())
    return skin


def _turn_colors(data: bytes):
    custom = ElementTree.fromstring(data).find("Custom")
    if custom is None:
        raise ValueError("The skin is missing its custom duel colours.")
    colors = []
    for name in _TURN_ELEMENTS:
        element = custom.find(name)
        if element is None:
            raise ValueError("The skin is missing its turn-player colours.")
        colors.append(tuple(int(element.attrib[key]) for key in "argb"))
    return tuple(colors)


def _recolor_turn(data: bytes, colors: tuple) -> bytes:
    """Replace just eight numeric values, preserving the rest of the user's XML."""
    for name, values in zip(_TURN_ELEMENTS, colors, strict=True):
        tag = re.compile(rb"<" + name.encode("ascii") + rb"\b[^>]*>")
        rgba = dict(zip(b"argb", values, strict=True))

        def color(match, rgba=rgba):
            return match[1] + match[3] + str(rgba[match[2][0]]).encode("ascii") + match[3]

        data, count = tag.subn(lambda match, color=color: _COLOR_ATTR.sub(color, match[0]), data)
        if count != 1:
            raise ValueError(
                "The skin has no unique turn-player colours. Reinstall it to apply the highlight."
            )
    if _turn_colors(data) != colors:
        raise ValueError("This skin's turn-player colours could not be updated.")
    return data


def turn_highlight_enabled(root: Path, skin: str) -> bool:
    if skin not in TURN_ACCENTS:
        return False
    path = root / "skin" / ("Monokai " + skin) / "skin.xml"
    try:
        return _turn_colors(path.read_bytes()) == TURN_ACCENTS[skin]
    except (OSError, ValueError, KeyError, ElementTree.ParseError):
        return False


def _settings(content: bytes) -> dict[str, str]:
    return {
        m[2].decode(): m[4].strip().decode("utf-8", "surrogateescape")
        for m in SETTING.finditer(content)
    }


def current_skin(edopro_path: str) -> str:
    try:
        content = (Path(edopro_path) / "config/system.conf").read_bytes()
    except OSError:
        return ""
    return _settings(content.removeprefix(b"\xef\xbb\xbf")).get("skin", "")


def installed_sleeve(root: Path, basename: str, skin: str) -> Path | None:
    """EDOPro prefers the active skin texture, then the installation texture."""
    if basename not in ("cover", "cover2"):
        raise ValueError("Unknown sleeve slot.")
    folders = [root / "textures"]
    if skin and skin not in (".", "..") and not any(char in skin for char in "/\\:"):
        folders.insert(0, root / "skin" / skin / "textures")
    return next(
        (
            file
            for folder in folders
            for ext in (".png", ".jpg")
            if (file := folder / (basename + ext)).is_file()
        ),
        None,
    )


def _update_settings(content: bytes, values: dict[str, str | None]) -> bytes:
    """Preserve unrelated bytes, the original line endings, and any UTF-8 BOM."""
    bom = b"\xef\xbb\xbf" if content.startswith(b"\xef\xbb\xbf") else b""
    body = content[len(bom) :]
    newline = b"\r\n" if b"\r\n" in body else b"\n"
    remaining = dict(values)
    lines = []
    # EDOPro uses the last occurrence. Leave shadowed settings and their spacing alone.
    original_lines = body.splitlines(keepends=True)
    last = {
        match[2].decode(): i
        for i, line in enumerate(original_lines)
        if (match := SETTING.match(line))
    }
    for i, line in enumerate(original_lines):
        match = SETTING.match(line)
        key = match[2].decode() if match else ""
        if key not in values or (values[key] is not None and i != last[key]):
            lines.append(line)
            continue
        remaining.pop(key, None)
        value = values[key]
        if value is not None:
            lines.append(
                line[: match.start(4)]
                + value.encode("utf-8", "surrogateescape")
                + match[4][len(match[4].rstrip(b" \t")) :]
                + line[match.end(4) :]
            )
    body = b"".join(lines)
    added = []
    for key, value in remaining.items():
        if value is not None:
            added.append(f"{key} = {value}".encode("utf-8", "surrogateescape") + newline)
    # Leave an unterminated final line byte-identical when an added key is removed.
    insert = len(body) if not body or body.endswith((b"\r", b"\n")) else body.rfind(b"\n") + 1
    return bom + body[:insert] + b"".join(added) + body[insert:]


def can_restore(edopro_path: str, config_path: str) -> bool:
    return (_backup_dir(Path(edopro_path).resolve(), config_path) / "prior-skin.json").is_file()


_UNDO_GROUPS = {"sleeves", "effects", "music"}
_SHA256 = re.compile(r"[a-f0-9]{64}")


def _group_dir(root: Path, config_path: str | Path, group: str) -> Path:
    if group not in _UNDO_GROUPS:
        raise ValueError("Unknown appearance category.")
    return _backup_dir(root, config_path) / ("undo-" + group)


def can_undo(edopro_path: str, config_path: str, group: str) -> bool:
    if group == "effects":
        from sounds.install import can_undo as can_undo_effects

        return can_undo_effects(Path(edopro_path), config_path)
    state = _group_dir(Path(edopro_path).resolve(), config_path, group)
    return any(
        (state / name).is_file() for name in ("last.json", "installing.json", "restoring.json")
    )


def _group_files(root: Path, group: str) -> set[str]:
    if group == "sleeves":
        allowed = set(SLEEVES.values())
        skins = root / "skin"
        if skins.is_dir():
            for skin in skins.iterdir():
                if skin.is_dir() and not skin.is_symlink():
                    for texture in SLEEVES.values():
                        allowed.add((Path("skin") / skin.name / texture).as_posix())
        return allowed
    if group == "music":
        return {
            source.relative_to(ASSETS).as_posix()
            for source in (ASSETS / "sound/BGM").rglob("*.flac")
        }
    from sounds.catalogue import EVENT_BY_STEM

    return {f"sound/{stem}.wav" for stem in EVENT_BY_STEM}


def _regular_path(root: Path, relative: str | Path) -> Path:
    """Writable members must be ordinary files beneath their intended directory."""
    path = root / relative
    if (
        path.resolve() != path
        or not path.resolve().is_relative_to(root)
        or path.is_symlink()
        or (path.exists() and not path.is_file())
    ):
        raise OSError(f"Expected a regular file inside {root.name}: {relative}")
    return path


def _group_target(root: Path, group: str, name: str) -> Path:
    if group == "music" and name.startswith("sound/BGM/duel/"):
        from music_library import original_duel_track_path

        target = original_duel_track_path(root, Path(name).name)
        return _regular_path(root, target.relative_to(root))
    return _regular_path(root, name)


def _signature(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _read_changes(journal: Path, allowed: set[str]) -> dict[str, list]:
    if journal.is_symlink():
        raise OSError("The saved appearance record is a symbolic link.")
    changes = json.loads(journal.read_bytes())
    if not isinstance(changes, dict) or not changes or changes.keys() - allowed:
        raise ValueError("The saved appearance record contains an unrecognised file.")
    for digests in changes.values():
        if (
            not isinstance(digests, list)
            or len(digests) != 2
            or not isinstance(digests[1], str)
            or not _SHA256.fullmatch(digests[1])
            or (
                digests[0] is not None
                and not (isinstance(digests[0], str) and _SHA256.fullmatch(digests[0]))
            )
        ):
            raise ValueError("The saved appearance record is damaged.")
    return changes


def _reverse_group(
    root: Path, backups: Path, group: str, changes: dict, *, begin=None
) -> tuple[str, ...]:
    # Validate the whole group before modifying any member. Matching an original is
    # also valid: this makes recovery idempotent after an interrupted install/undo.
    pending = []
    for name, (before, after) in changes.items():
        target = _group_target(root, group, name)
        current = _signature(target)
        if current == before:
            continue
        if current != after:
            raise ValueError(
                f"{name} changed after installation. Its current contents have been kept."
            )
        previous = None
        if before is not None:
            backup = _regular_path(backups, Path(before[:16]) / name)
            previous = backup.read_bytes() if backup.is_file() else None
            if previous is None or hashlib.sha256(previous).hexdigest() != before:
                raise OSError(f"The original backup for {name} is missing or has changed.")
        pending.append((name, target, after, previous))
    if begin is not None:
        begin()
    for name, target, after, previous in pending:
        if _group_target(root, group, name) != target or _signature(target) != after:
            raise OSError(f"{name} changed while restoring. Try again after closing EDOPro.")
        if previous is None:
            target.unlink()
        else:
            _atomic_write(target, previous)
    return tuple(item[0] for item in pending)


def _change_group(
    root: Path,
    config_path: str,
    group: str,
    files: dict[str, bytes] | None,
    *,
    prepare: Callable[[dict], None] | None = None,
) -> tuple[str, ...]:
    """Install a scope transactionally, or undo its last change with files=None."""
    if group == "effects":
        from sounds.install import change_legacy_effects

        change_legacy_effects(root, config_path, files)
        return ()
    from state_lock import locked

    backups = _backup_dir(root, config_path)
    state = _group_dir(root, config_path, group)
    if backups.resolve() != backups or state.resolve() != state:
        raise OSError("The appearance backup directory points outside the expected location.")
    allowed = _group_files(root, group)
    if files is not None and (not files or files.keys() - allowed):
        raise ValueError("The appearance pack contains no valid selection.")
    with ExitStack() as locks:
        if group == "music":
            from music_library import playlist_lock

            locks.enter_context(playlist_lock(root, "duel"))
        else:
            # Separate configurations can point at the same game. Lock the ordinary
            # asset directory so those configurations also serialize their writes.
            lock = _regular_path(root, "textures/.edopro-hd-sync.lock")
            locks.enter_context(locked(lock.parent, lock.name))
        last = _regular_path(state, "last.json")
        installing = _regular_path(state, "installing.json")
        restoring = _regular_path(state, "restoring.json")
        for incomplete in (restoring, installing):
            if incomplete.exists():
                changes = _read_changes(incomplete, allowed)
                written = _reverse_group(
                    root,
                    backups,
                    group,
                    changes,
                    begin=partial(prepare, changes)
                    if files is None and prepare is not None
                    else None,
                )
                incomplete.unlink()
                if files is None:
                    return written
        if files is None:
            if not last.exists():
                raise ValueError("There is no previous change to undo for this category.")
            changes = _read_changes(last, allowed)

            # A failed or interrupted restore can be resumed from this journal.
            def begin_restore() -> None:
                if prepare is not None:
                    prepare(changes)
                last.replace(restoring)

            written = _reverse_group(root, backups, group, changes, begin=begin_restore)
            restoring.unlink()
            return written

        changes = {}
        for name, content in files.items():
            target = _group_target(root, group, name)
            after = hashlib.sha256(content).hexdigest()
            before = _signature(target)
            if before == after:
                continue
            if before is not None:
                backup = _regular_path(backups, Path(before[:16]) / name)
                if not backup.exists():
                    previous = target.read_bytes()
                    if hashlib.sha256(previous).hexdigest() != before:
                        raise OSError(f"{name} changed while backing up. Please retry.")
                    _atomic_write(backup, previous)
                if _signature(backup) != before:
                    raise OSError(f"Could not validate the backup for {name}.")
            changes[name] = [before, after]
        if prepare is not None:
            prepare(changes)
        if not changes:
            return ()
        _atomic_write(installing, json.dumps(changes).encode("utf-8"))
        try:
            for name, (before, _after) in changes.items():
                target = _group_target(root, group, name)
                if _signature(target) != before:
                    raise OSError(f"{name} changed during installation. Please retry.")
                _atomic_write(target, files[name])
            installing.replace(last)
        except Exception:
            # Preserve the transaction record if restoring a file also fails.
            _reverse_group(root, backups, group, changes)
            installing.unlink()
            raise
        return tuple(changes)


def apply_sleeve_pngs(
    root: Path,
    config_path: str,
    *,
    mine: bytes | None = None,
    opponent: bytes | None = None,
    skin: str | None = None,
    prepare: Callable[[dict], None] | None = None,
) -> tuple[str, ...]:
    """Apply PNG bytes in one sleeves transaction. Return changed root-relative paths."""
    root = root.resolve()
    active = current_skin(str(root)) if skin is None else skin
    files = {}
    for base, data in (("cover", mine), ("cover2", opponent)):
        if data is None:
            continue
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Choose a valid PNG sleeve.")
        source = installed_sleeve(root, base, active)
        target = source.with_suffix(".png") if source else root / "textures" / (base + ".png")
        files[target.relative_to(root).as_posix()] = data
    return _change_group(root, config_path, "sleeves", files, prepare=prepare) if files else ()


def _install_effects(root: Path, config_path: str, name: str) -> None:
    from sounds.sets import bundled_files

    _change_group(root, config_path, "effects", bundled_files(name))


@appearance_writer
def apply_appearance(
    edopro_path: str,
    config_path: str,
    skin: str = "",
    sleeves: bool = False,
    audio: str = "",
    restore: bool = False,
    *,
    player_sleeve: str = "",
    opponent_sleeve: str = "",
    effects: str = "",
    synth_music: bool = False,
    undo_group: str = "",
    highlight_turn: bool | None = None,
    pack_cache: PackCache | None = None,
    skin_pack: Pack | None = None,
    shared_pack: Pack | None = None,
) -> None:
    """Install only the selected groups, activating the skin after its files are ready."""
    root = Path(edopro_path).resolve()
    backups = _backup_dir(root, config_path)
    snapshot = backups / "prior-skin.json"
    rel = "config/system.conf"
    if skin_pack is not None and (
        skin_pack.kind != "skin"
        or skin_pack.withheld
        or skin_pack.id == "shared"
        or skin_pack.title != skin
        or not re.fullmatch(r"[\w][\w -]{0,63}", skin)
    ):
        raise ValueError("Select the name and catalogue entry for a valid skin pack.")
    if shared_pack is not None and (shared_pack.id != "shared" or shared_pack.withheld):
        raise ValueError("Select the shared appearance pack.")
    if (
        (skin and skin not in SKINS and skin_pack is None)
        or audio not in ("", "Thonk", "Synth")
        or (restore and skin)
    ):
        raise ValueError("Unknown or conflicting appearance selection.")
    if any(name and name not in SLEEVES for name in (player_sleeve, opponent_sleeve)):
        raise ValueError("Unknown card sleeve.")
    if effects not in ("", "Thonk", "Synth"):
        raise ValueError("Unknown duel sound effects.")
    if highlight_turn is not None and (skin not in SKINS or not isinstance(highlight_turn, bool)):
        raise ValueError("Select a Monokai skin for the turn-player highlight.")
    if undo_group:
        if (
            skin
            or restore
            or sleeves
            or audio
            or player_sleeve
            or opponent_sleeve
            or effects
            or synth_music
        ):
            raise ValueError("Undo one appearance category at a time.")
        _change_group(root, config_path, undo_group, None)
        return
    if skin:
        skin = _skin_files(
            root,
            backups,
            skin,
            highlight_turn,
            pack_cache or PackCache.for_config_path(config_path),
            skin_pack,
            shared_pack,
        )
    if sleeves:
        _install_pack(root, backups, "shared", "textures/")
    if audio == "Thonk":
        _install_effects(root, config_path, "Thonk")
    elif audio == "Synth":
        _install(edopro_path, config_path, "sound")
    if player_sleeve or opponent_sleeve:
        active = f"Monokai {skin}" if skin else current_skin(str(root))
        with ZipFile(PACKS / "shared.zip") as archive:
            apply_sleeve_pngs(
                root,
                config_path,
                skin=active,
                mine=archive.read(SLEEVES[player_sleeve]) if player_sleeve else None,
                opponent=archive.read(SLEEVES[opponent_sleeve]) if opponent_sleeve else None,
            )
    if effects:
        _install_effects(root, config_path, effects)
    if synth_music:
        files = {
            p.relative_to(ASSETS).as_posix(): p.read_bytes()
            for p in (ASSETS / "sound/BGM").rglob("*.flac")
        }
        _change_group(root, config_path, "music", files)
    if not skin and not restore:
        return
    conf = root / rel
    content = conf.read_bytes() if conf.is_file() else b""
    if restore:
        values = json.loads(snapshot.read_bytes())
    else:
        values = {"skin": f"Monokai {skin}", **FONT_SETTINGS}
        if not snapshot.exists():
            before = _settings(content.removeprefix(b"\xef\xbb\xbf"))
            _atomic_write(snapshot, json.dumps({key: before.get(key) for key in values}).encode())
    _publish(root, backups, rel, _update_settings(content, values))
    if restore:
        snapshot.unlink(missing_ok=True)
