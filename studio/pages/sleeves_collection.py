"""The ready-made collection, in-memory filters and portrait crop geometry."""

from dataclasses import dataclass
from pathlib import Path

from sleeves import PALETTES, PATTERNS, Entry, Recipe
from sleeves.imaging import HEIGHT, WIDTH, Crop

FAMILIES = {
    "Blue": ("Midnight gilt", "Glacier", "Porcelain", "Kingfisher", "Blue hour"),
    "Green": ("Koi pond", "Peacock", "Matcha", "Patina"),
    "Red": ("Lacquer", "Cinnabar"),
    "Purple": ("Mulberry", "Moonstone"),
    "Gold": ("Golden hour", "Letterpress"),
    "Neutral": ("Obsidian",),
}
SOURCES = {
    "preset": "Original sleeve",
    "designed": "Saved design",
    "imported": "Your image",
    "card art": "Card artwork",
    "recovered": "Recovered sleeve",
}
# Alternate scenic, engraved and geometric prints on the opening shelf.
FEATURED = ("sun", "alpine", "fans", "orbit", "dunes", "stars", "rose", "tide")


@dataclass(frozen=True)
class Choice:
    key: Path
    name: str
    detail: str
    family: str = ""
    palette: str = ""
    recipe: Recipe | None = None
    entry: Entry | None = None

    @property
    def tooltip(self) -> str:
        if self.entry and (origin := self.entry.origin):
            return (
                f"{self.name} · {self.detail}\n{origin.author} · {origin.license}\n"
                f"{origin.attribution}\n{origin.source_url}"
            )
        return f"{self.full_name} · {self.detail}"

    @property
    def full_name(self) -> str:
        return f"{self.name} · {self.palette}" if self.recipe and not self.entry else self.name


def family_of(palette: str) -> str:
    return next((family for family, names in FAMILIES.items() if palette in names), "")


def collection() -> list[Choice]:
    palettes = list(PALETTES)
    order = dict.fromkeys((*FEATURED, *PATTERNS))
    result = []
    for offset in range(len(palettes)):
        for key in order:
            spec = PATTERNS[key]
            palette = palettes[(palettes.index(spec.palette) + offset) % len(palettes)]
            result.append(
                Choice(
                    Path("print") / key / palette,
                    spec.name,
                    palette,
                    family_of(palette),
                    palette,
                    Recipe(key, PALETTES[palette]),
                )
            )
    return result


def saved(entry: Entry) -> Choice:
    inks = entry.recipe.palette if entry.recipe else entry.layout.palette if entry.layout else ()
    palette = next((name for name, colours in PALETTES.items() if colours == inks), "")
    if entry.recipe and not inks:
        palette = PATTERNS[entry.recipe.pattern].palette
    return Choice(
        Path("saved") / entry.id,
        entry.name,
        "Pack sleeve" if entry.origin else SOURCES[entry.source],
        family_of(palette),
        palette,
        entry.recipe,
        entry,
    )


def filtered(
    items: list[Choice], query: str, source: str, colour: str, measured: dict[Path, str]
) -> list[Choice]:
    terms = query.casefold().split()
    result = []
    for item in items:
        entry = item.entry
        if (source == "Ready-made" and entry) or (source == "Saved sleeves" and not entry):
            continue
        if source == "Favorites" and not (entry and entry.favourite):
            continue
        if source == "Recently used" and not (entry and entry.last_used):
            continue
        if (source == "Pack sleeve" or source in SOURCES.values()) and item.detail != source:
            continue
        if colour != "All colors" and (item.family or measured.get(item.key)) != colour:
            continue
        fields = [item.name, item.detail, item.palette, item.family]
        if entry:
            fields.extend((*entry.tags, str(entry.passcode or "")))
        searchable = " ".join(fields).casefold()
        if all(term in searchable for term in terms):
            result.append(item)
    if source == "Recently used":
        result.sort(key=lambda item: item.entry.last_used, reverse=True)
    return result


def crop_box(size: tuple[int, int], centre: tuple[float, float], zoom: float) -> Crop:
    w, h = size
    scale = min(w / WIDTH, h / HEIGHT) / zoom
    cw, ch = min(w, WIDTH * scale), min(h, HEIGHT * scale)
    return ((w - cw) * centre[0], (h - ch) * centre[1], cw, ch)
