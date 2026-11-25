"""Verified duel texture sources and undoable installation of an edited preset."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from edopal.features.themes.service import _asset, _check, _conf, _inside, _manifest

PRESETS = {"stock": "EDOPro Default", "minimal": "Minimal", "minimal-gold": "Minimal Gold", "minimal-glass": "Minimal Glass"}
SLOTS = {
    "act": ("Activation", "Marks an effect that can be activated."),
    "attack": ("Attack", "Marks the attacking monster."),
    "chain": ("Chain", "Surrounds a chain link number."),
    "number": ("Chain Numbers", "Numbers the links in a chain."),
    "negated": ("Negated", "Marks a negated card or effect."),
    "target": ("Target", "Identifies the target of an effect."),
    "chaintarget": ("Chain Target", "Identifies a target in the current chain."),
    "equip": ("Equip", "Connects an equipped card to its target."),
    "lim": ("Banlist Badges", "Displays a card’s restriction level."),
    "lpf": ("LP Frame", "Frames both players’ Life Point bars."),
    "settings": ("Settings", "Opens the game settings."),
}
KEPT = {"chain", "chaintarget", "settings"}


def choice_name(choice):
    if choice == "stock":
        return "Stock"
    return PRESETS.get(choice, f"Candidate {choice}")


@dataclass
class IconLibrary:
    textures: dict[tuple[str, str], Path]
    field: Path
    by_digest: dict[tuple[str, str], str]


def load_icons(packs, ctx):
    catalog = {pack.id: pack for pack in packs.catalog("icons")}
    textures = {}
    by_digest = {}
    for slug in (*PRESETS, "candidates"):
        _check(ctx)
        pack_id = f"icons-{slug}"
        if pack_id not in catalog:
            raise ValueError(f"{pack_id} is missing from the pack catalog.")
        directory = packs.ensure(catalog[pack_id], cancel=lambda: ctx.cancelled)
        manifest = _manifest(directory, pack_id, "icons")
        if slug == "stock":
            field = _asset(directory, manifest, "previews/field.webp")
        for key in SLOTS:
            if slug == "candidates" and key in KEPT:
                continue
            choices = "ABCDE" if slug == "candidates" else [slug]
            for choice in choices:
                rel = f"candidates/{key}/{choice}.png" if slug == "candidates" else f"textures/{key}.png"
                textures[key, choice] = _asset(directory, manifest, rel)
                by_digest.setdefault((key, manifest["files"][rel]), choice)
    return IconLibrary(textures, field, by_digest)


def texture_dir(install):
    skin = _conf(install).get("skin", "")
    if skin:
        folder = f"skin/{skin}"
        if "/" in skin or not _inside(install.root, folder).is_dir():
            return "textures"
        return f"{folder}/textures"
    return "textures"


def installed_choices(install, library):
    """Match the effective skin/base PNGs, including textures installed before EDOPal."""
    dest = texture_dir(install) if install else None
    choices = {}
    custom = {}
    for key in SLOTS:
        choices[key] = "stock"
        if install:
            for folder in dict.fromkeys((dest, "textures")):
                image = _inside(install.root, f"{folder}/{key}.png")
                if image.is_file():
                    with image.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    choices[key] = library.by_digest.get((key, digest), "custom")
                    if choices[key] == "custom":
                        custom[key] = image
                    break
    return dest, choices, custom


def apply_icons(core, install, library, choices, expected_dir, ctx, confirmed_closed=False):
    if set(choices) != set(SLOTS):
        raise ValueError("Select a texture for every icon.")
    sources = {}
    for key, choice in choices.items():
        if choice == "custom":
            continue  # Keep the user's existing texture for this slot.
        if (key, choice) not in library.textures:
            raise ValueError(f"Unknown texture for {SLOTS[key][0]}.")
        sources[key] = library.textures[key, choice]
    with core.storage.changeset(install, "icons", "Apply duel icons", confirmed_closed=confirmed_closed) as cs:
        if core.installs.current() != install or texture_dir(install) != expected_dir:
            raise ValueError("The game folder or skin changed. Review the icons again.")
        for key, path in sources.items():
            _check(ctx)
            cs.copy_in(path, f"{expected_dir}/{key}.png")
