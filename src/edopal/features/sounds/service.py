"""Pack downloads and stock preview lookup, independent of Qt."""

from pathlib import Path

from edopal.core.packs import PackInfo, PackService

GROUPS = (
    (
        "Card Actions",
        (
            ("Summon", "summon.wav"),
            ("Special Summon", "specialsummon.wav"),
            ("Activate", "activate.wav"),
            ("Chain", "activate.wav"),
            ("Draw", "draw.wav"),
            ("Shuffle", "shuffle.wav"),
            ("Equip", "equip.wav"),
        ),
    ),
    (
        "Battle & Results",
        (
            ("Attack", "attack.wav"),
            ("Direct Attack", "directattack.wav"),
            ("Damage", "damage.wav"),
            ("Recover LP", "gainlp.wav"),
            ("Destroy", "destroyed.wav"),
            ("Win", "BGM/win"),
            ("Lose", "BGM/lose"),
        ),
    ),
)
SLOTS = tuple(dict.fromkeys(slot for _, rows in GROUPS for _, slot in rows))
_AUDIO = {".wav", ".ogg", ".mp3", ".flac"}


def stock_previews(root: Path | None) -> dict[str, Path]:
    if root is None:
        return {}
    files = {}
    for slot in SLOTS:
        target = root / "sound" / slot
        stock = target.is_file()
        folder = target.with_suffix("") if target.suffix else target
        if folder.is_dir():
            # EDOPro chooses from files in the slot directory before using the loose file.
            candidates = sorted(file for file in folder.iterdir() if file.suffix.lower() in _AUDIO and file.is_file())
            if candidates:
                files[slot] = candidates[0]
                continue
        if stock:
            files[slot] = target
    return files


def fetch_pack(packs: PackService, pack: PackInfo, ctx) -> Path:
    return packs.ensure(
        pack,
        progress=lambda done, total: ctx.progress(done, total),
        cancel=lambda: ctx.cancelled,
    )


def cached_sound_packs(packs: PackService, slugs: tuple[str, ...], ctx) -> set[str]:
    downloaded = set()
    for slug in slugs:
        if ctx.cancelled:
            break
        try:
            if packs.cached("sound-" + slug):
                downloaded.add(slug)
        except (OSError, ValueError):
            continue
    return downloaded
