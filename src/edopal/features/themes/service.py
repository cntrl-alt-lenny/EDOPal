"""Apply appearance packs in one undoable storage transaction per user action."""

from __future__ import annotations

import codecs
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from edopal.core import Core
from edopal.core.install import Install, RunState
from edopal.core.jobs import JobContext
from edopal.core.packs import PackInfo
from edopal.core.storage import ChangeSet, UndoResult

DEFAULT_BACKS = {"you": "cardback-vortex-classic", "opponent": "cardback-ocg-1998"}
_COVERS = {"you": "cover", "opponent": "cover2"}
_CONF = "config/system.conf"
# In the same changeset as the textures and conf. Undo restores the corresponding
# original font/skin values and independent sleeve choices, including after restart.
_RECORD = "config/edopal-themes.json"
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_CONF_KEYS = ("skin", "textfont", "numfont")
_ACCENTS = {
    "Global": ("EGDC_ICON_HIGH_LIGHT", "CheckBoxColor"),
    "Custom": (
        "DECK_WINDOW_HOVERED_CARD_OUTLINE",
        "DUELFIELD_SELECTABLE_CARD_OUTLINE",
        "DUELFIELD_SELECTABLE_FIELD_OUTLINE",
        "CARDINFO_ARCHETYPE_TEXT_COLOR",
    ),
}


@dataclass(frozen=True)
class ThemeState:
    active_theme: str | None = None
    cardbacks: dict[str, str] = field(default_factory=lambda: DEFAULT_BACKS.copy())
    back_images: dict[str, Path] = field(default_factory=dict)
    accent: str | None = None
    theme_font: bool = False
    has_font: bool = False
    can_undo: bool = False
    unknown_back_slots: frozenset[str] = frozenset()


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in ("", ".", "..") for part in value.split("/"))
        or "\\" in value
        or ":" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"Invalid relative path: {value!r}")
    return path


def _inside(root: Path, name: str) -> Path:
    path = root.joinpath(_relative(name))
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes folder: {name}")
    return path


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _conf_text(path: Path) -> tuple[str, bytes, str]:
    raw = _read(path)
    for bom, codec in (
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
        (codecs.BOM_UTF8, "utf-8"),
    ):
        if raw.startswith(bom):
            return raw[len(bom) :].decode(codec), bom, codec
    try:
        return raw.decode("utf-8"), b"", "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", "surrogateescape"), b"", "cp1252"


def _conf(install: Install) -> dict[str, str]:
    values = {}
    text, _, _ = _conf_text(_inside(install.root, _CONF))
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in _CONF_KEYS:
            values[key.strip()] = re.split(r"(?:^|[ \t]+)[#;]", value.strip(), maxsplit=1)[0].strip()
    return values


def _update_conf(cs: ChangeSet, install: Install, current: dict, changes: dict) -> None:
    changes = {key: value for key, value in changes.items() if current.get(key) != value}
    if not changes:
        return
    path = _inside(install.root, _CONF)
    removed = {key for key, value in changes.items() if value is None}
    if removed or not path.exists():
        # set_conf cannot remove a formerly absent key. Remove just those
        # declarations, retaining all unrelated bytes and the game's encoding.
        text, bom, codec = _conf_text(path)
        lines = [
            line
            for line in text.splitlines(keepends=True)
            if line.partition("=")[0].strip() not in removed or "=" not in line
        ]
        if not path.exists():
            lines.extend(f"{key} = {value}\n" for key, value in changes.items() if value is not None)
            cs.write(_CONF, "".join(lines).encode("utf-8"))
            return
        cs.write(_CONF, bom + "".join(lines).encode(codec, "surrogateescape"))
    for key, value in changes.items():
        if value is not None:
            cs.set_conf(_CONF, key, value)


def _record(install: Install) -> dict:
    data = _read(_inside(install.root, _RECORD))
    if not data:
        return {}
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("schema") != 1:
        raise ValueError("Cannot read the saved appearance settings for this game.")
    return result


def _save(cs: ChangeSet, record: dict) -> None:
    cs.write(_RECORD, json.dumps({**record, "schema": 1}, sort_keys=True, indent=2).encode() + b"\n")


def _skin_id(conf: dict) -> str | None:
    match = re.fullmatch(rf"EDOPal-({_SLUG})", conf.get("skin", ""))
    return f"theme-{match[1]}" if match else None


def _texture_dir(theme: str | None) -> str:
    return f"skin/EDOPal-{theme.removeprefix('theme-')}/textures" if theme else "textures"


def _back_images(install: Install, skin: str) -> dict[str, Path]:
    directories = ["textures"]
    if skin and skin not in (".", "..") and re.fullmatch(r"[^/\\:\x00-\x1f]+", skin):
        directories.insert(0, f"skin/{skin}/textures")
    images = {}
    for slot, stem in _COVERS.items():
        for directory in directories:
            for ext in ("png", "jpg"):
                path = _inside(install.root, f"{directory}/{stem}.{ext}")
                if path.is_file():
                    images[slot] = path
                    break
            if slot in images:
                break
    if "you" in images:
        images.setdefault("opponent", images["you"])
    return images


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check(ctx: JobContext | None) -> None:
    if ctx and ctx.cancelled:
        raise CancelledError("Appearance change cancelled.")


def _manifest(directory: Path, pack_id: str, kind: str) -> dict:
    data = json.loads(_inside(directory, "manifest.json").read_bytes())
    if data.get("schema") != 1 or data.get("id") != pack_id or data.get("kind") != kind:
        raise ValueError(f"Manifest does not describe {pack_id}.")
    if not isinstance(data.get("files"), dict):
        raise ValueError(f"Missing file list in {pack_id}.")
    return data


def _asset(directory: Path, manifest: dict, name: str) -> Path:
    path = _inside(directory, name)
    if name not in manifest["files"] or not path.is_file():
        raise ValueError(f"Missing pack asset: {name}")
    return path


def _accent_xml(data: bytes, accent: str) -> bytes:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
        raise ValueError("Choose a six-digit RGB accent colour.")
    rgb = {key: str(int(accent[i : i + 2], 16)) for key, i in zip("rgb", (1, 3, 5), strict=True)}
    tree = ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    for group, names in _ACCENTS.items():
        section = tree.find(group)
        if section is None:
            section = ET.SubElement(tree, group)
        for name in names:
            element = section.find(name)
            if element is None:
                element = ET.SubElement(section, name)
            hex_color = element.attrib.pop("color", "")
            alpha = str(int(hex_color[:2], 16)) if re.fullmatch(r"[A-Fa-f0-9]{8}", hex_color) else "255"
            element.attrib.setdefault("a", alpha)
            element.attrib.update(rgb)
    return ET.tostring(tree, encoding="utf-8", xml_declaration=True)


class ThemeService:
    def __init__(self, core: Core):
        self.core = core

    def state(self) -> ThemeState:
        install = self.core.installs.current()
        if install is None:
            return ThemeState()
        conf = _conf(install)
        active = _skin_id(conf)
        record = _record(install)
        managed = record if active and record.get("active") == active else {}
        fonts = managed.get("fonts", {})
        cards = {**DEFAULT_BACKS, **managed.get("defaults", {}), **record.get("choices", {})}
        images = _back_images(install, conf.get("skin", ""))
        hashes = record.get("back_hashes", {})
        return ThemeState(
            active_theme=active,
            cardbacks=cards,
            back_images=images,
            unknown_back_slots=frozenset(
                slot for slot, path in images.items() if cards[slot] not in hashes or _digest(path) != hashes[cards[slot]]
            ),
            accent=managed.get("accent"),
            theme_font=bool(fonts) and all(conf.get(key) == value for key, value in fonts.items()),
            has_font=bool(fonts),
            can_undo=self.core.storage.last_receipt(install, "themes") is not None,
        )

    def _install(self) -> Install:
        install = self.core.installs.current()
        if not install:
            raise ValueError("Choose your EDOPro game folder first.")
        return install

    def _ready(self, install: Install, ctx: JobContext | None) -> None:
        _check(ctx)
        if self.core.installs.current() != install:
            raise ValueError("The game folder changed. Select the theme again.")

    def _ensure(self, pack: PackInfo, kind: str, ctx: JobContext | None) -> Path:
        if pack.kind != kind or not re.fullmatch(rf"{kind}-{_SLUG}", pack.id):
            raise ValueError(f"Invalid {kind} pack: {pack.id}")
        _check(ctx)

        def progress(done: int, total: int) -> None:
            _check(ctx)
            if ctx:
                ctx.progress(done, total, f"Downloading {pack.name}")

        directory = self.core.packs.ensure(pack, progress=progress, cancel=lambda: bool(ctx and ctx.cancelled))
        _check(ctx)
        return directory

    def _back(self, directory: Path, pack_id: str) -> Path:
        manifest = _manifest(directory, pack_id, "cardback")
        name = manifest["image"]
        image = _asset(directory, manifest, name)
        if image.suffix.lower() != ".png":
            raise ValueError("The card-back image must be a PNG.")
        return image

    def _backs_for_theme(self, ids: dict, ctx: JobContext | None, optional_slots=()) -> dict[str, Path]:
        if not ids:
            return {}
        packs = {p.id: p for p in self.core.packs.catalog("cardback")}
        images = {}
        for slot, pack_id in ids.items():
            if not re.fullmatch(rf"cardback-{_SLUG}", pack_id):
                raise ValueError(f"Invalid card back: {pack_id}")
            if pack_id in packs:
                directory = self._ensure(packs[pack_id], "cardback", ctx)
            else:
                directory = self.core.packs.cached(pack_id)
            if directory is None:
                if slot in optional_slots:
                    continue
                raise ValueError(f"Card-back pack unavailable: {pack_id}")
            images[slot] = self._back(directory, pack_id)
        return images

    def apply_theme(self, pack: PackInfo | None, ctx: JobContext | None = None, *, confirmed_closed=False) -> ThemeState:
        install = self._install()
        self._ready(install, ctx)
        if pack is None:
            return self._restore(install, ctx, confirmed_closed)
        directory = self._ensure(pack, "theme", ctx)
        manifest = _manifest(directory, pack.id, "theme")
        folder = f"EDOPal-{pack.id.removeprefix('theme-')}"
        skin = _relative(manifest["skin_dir"])
        _asset(directory, manifest, f"{skin}/skin.xml")
        files = {}
        for name in manifest["files"]:
            relative = _relative(name)
            if relative.is_relative_to(skin):
                files[f"skin/{folder}/{relative.relative_to(skin)}"] = _asset(directory, manifest, name)
        font_conf = {}
        font = manifest.get("font") or {}
        for role, key in (("text", "textfont"), ("num", "numfont")):
            if not font.get(role):
                continue
            source = _asset(directory, manifest, font[role])
            if re.search(r"\s", source.name):
                raise ValueError("EDOPro font filenames must be space-free.")
            dest = f"fonts/{folder}/{source.name}"
            if dest in files and files[dest] != source:
                raise ValueError(f"Conflicting font files: {source.name}")
            files[dest] = source
            font_conf[key] = dest
            if role == "text":
                size = int(font["size"])
                if not 1 <= size <= 255:
                    raise ValueError("Invalid theme font size.")
                font_conf[key] += f" {size}"

        previous = _record(install)
        choices = previous.get("choices", {})
        defaults = {s: p for s, p in (manifest.get("default_cardbacks") or {}).items() if s in _COVERS}
        selected_backs = {**DEFAULT_BACKS, **defaults, **choices}
        # Theme defaults are optional assets: a partially published catalog
        # can still supply the skin. Explicit slot choices survive switches.
        backs = self._backs_for_theme(selected_backs, ctx, DEFAULT_BACKS.keys() - choices.keys())
        hashes = dict(previous.get("back_hashes", {}))
        for slot, source in backs.items():
            files[f"skin/{folder}/textures/{_COVERS[slot]}.png"] = source
            hashes[selected_backs[slot]] = _digest(source)

        self._ready(install, ctx)
        if ctx:
            ctx.progress(0, 0, f"Applying {pack.name}")
        with self.core.storage.changeset(install, "themes", f"Apply {pack.name}", confirmed_closed=confirmed_closed) as cs:
            conf = _conf(install)
            if _record(install) != previous:
                raise ValueError("Appearance changed while loading. Apply the theme again.")
            old_managed = previous if _skin_id(conf) and previous.get("active") == _skin_id(conf) else {}
            original = {k: conf.get(k) for k in _CONF_KEYS}
            for key, value in old_managed.get("applied", {}).items():
                if conf.get(key) == value:
                    original[key] = old_managed["original"].get(key)
            applied = {"skin": folder, **font_conf}
            record = {
                "active": pack.id,
                "accent": manifest.get("accent"),
                "choices": choices,
                "back_hashes": hashes,
                "defaults": defaults,
                "original": original,
                "fonts": font_conf,
                "applied": applied,
            }
            for dest, source in files.items():
                cs.copy_in(source, dest)
            _update_conf(cs, install, conf, {**original, **applied})
            _save(cs, record)
        return self.state()

    def _restore(self, install: Install, ctx: JobContext | None, confirmed_closed: bool) -> ThemeState:
        conf = _conf(install)
        active = _skin_id(conf)
        if not active:
            return self.state()
        record = _record(install)
        managed = record if record.get("active") == active else {}
        changes = {"skin": managed.get("original", {}).get("skin")}
        for key, value in managed.get("applied", {}).items():
            if conf.get(key) == value:
                changes[key] = managed["original"].get(key)
        choices = record.get("choices", {})
        # Explicit slot choices were installed with this managed skin. Carry its
        # PNGs back to the base textures even if the pack cache is now empty.
        images = {slot: _inside(install.root, f"{_texture_dir(active)}/{_COVERS[slot]}.png") for slot in choices}
        hashes = record.get("back_hashes", {})
        missing = {
            slot: choices[slot]
            for slot, path in images.items()
            if not path.is_file() or (choices[slot] in hashes and _digest(path) != hashes[choices[slot]])
        }
        images.update(self._backs_for_theme(missing, ctx))
        self._ready(install, ctx)
        with self.core.storage.changeset(
            install, "themes", "Restore EDOPro default", confirmed_closed=confirmed_closed
        ) as cs:
            if _record(install) != record or _conf(install) != conf:
                raise ValueError("Appearance changed while loading. Restore the skin again.")
            for slot, source in images.items():
                cs.copy_in(source, f"textures/{_COVERS[slot]}.png")
            _update_conf(cs, install, conf, changes)
            _save(cs, {"choices": choices, "back_hashes": record.get("back_hashes", {})})
        return self.state()

    def apply_cardback(
        self, slot: str, pack: PackInfo, ctx: JobContext | None = None, *, confirmed_closed=False
    ) -> ThemeState:
        if slot not in _COVERS:
            raise ValueError("Select You or Opponent.")
        install = self._install()
        source = self._back(self._ensure(pack, "cardback", ctx), pack.id)
        self._ready(install, ctx)
        title = f"Set {slot} card back to {pack.name}"
        with self.core.storage.changeset(install, "themes", title, confirmed_closed=confirmed_closed) as cs:
            record = _record(install)
            dest = _texture_dir(_skin_id(_conf(install)))
            cs.copy_in(source, f"{dest}/{_COVERS[slot]}.png")
            choices = {**record.get("choices", {}), slot: pack.id}
            hashes = {**record.get("back_hashes", {}), pack.id: _digest(source)}
            _save(cs, {**record, "choices": choices, "back_hashes": hashes})
        return self.state()

    def customize(
        self, accent: str | None, theme_font: bool, ctx: JobContext | None = None, *, confirmed_closed=False
    ) -> ThemeState:
        install = self._install()
        previous = _record(install)
        record = deepcopy(previous)
        conf = _conf(install)
        active = _skin_id(conf)
        if not active or record.get("active") != active:
            raise ValueError("Apply an EDOPal theme to customize it.")
        changed_xml = original_xml = None
        rel = f"skin/{conf['skin']}/skin.xml"
        xml_path = _inside(install.root, rel)
        if accent is not None and accent != record.get("accent"):
            original_xml = xml_path.read_bytes()
            changed_xml = _accent_xml(original_xml, accent)
            record["accent"] = accent
        fonts = record.get("fonts", {})
        enabled = bool(fonts) and all(conf.get(key) == value for key, value in fonts.items())
        desired = {}
        if theme_font != enabled:
            for key in fonts:
                if conf.get(key) != record["applied"].get(key):
                    record["original"][key] = conf.get(key)
            desired = fonts if theme_font else {key: record["original"].get(key) for key in fonts}
        if changed_xml is None and all(conf.get(key) == value for key, value in desired.items()):
            return self.state()
        self._ready(install, ctx)
        with self.core.storage.changeset(
            install, "themes", "Customize active theme", confirmed_closed=confirmed_closed
        ) as cs:
            if (
                _record(install) != previous
                or _conf(install) != conf
                or (original_xml is not None and _read(xml_path) != original_xml)
            ):
                raise ValueError("Appearance changed while customizing. Try again with the current settings.")
            if changed_xml is not None:
                cs.write(rel, changed_xml)
            _update_conf(cs, install, conf, desired)
            record["applied"].update(desired)
            _save(cs, record)
        return self.state()

    def undo(self, ctx: JobContext | None = None, *, confirmed_closed=False) -> UndoResult:
        install = self._install()
        self._ready(install, ctx)
        if confirmed_closed and self.core.installs.run_state() is RunState.UNKNOWN:
            # Production storage accepts confirmation; preview storage is always CLOSED.
            return self.core.storage.undo_last(install, "themes", confirmed_closed=True)
        return self.core.storage.undo_last(install, "themes")
