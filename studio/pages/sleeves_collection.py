"""The ready-made collection, in-memory filters and portrait crop geometry."""

from dataclasses import dataclass, replace
from functools import cached_property
from itertools import zip_longest
from pathlib import Path

from sleeves import PALETTES, PATTERNS, Entry, Recipe
from sleeves.catalog import PALETTE_FAMILIES
from sleeves.imaging import HEIGHT, WIDTH, Crop

FAMILIES = tuple(dict.fromkeys(PALETTE_FAMILIES.values()))
TEAMS = ("Saltire", "Relay")
PARTNERS = {
    f"{team} {side}": f"{team} {other}"
    for team in TEAMS
    for side, other in (("home", "away"), ("away", "home"))
}
SOURCES = {
    "preset": "Original sleeve",
    "designed": "Saved design",
    "imported": "Your image",
    "card art": "Card artwork",
    "recovered": "Recovered sleeve",
}


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
    def motif(self) -> str:
        return PATTERNS[self.recipe.pattern].family if self.recipe else ""

    @cached_property
    def search_text(self) -> str:
        fields = [self.name, self.detail, self.palette, self.family, self.motif]
        if self.recipe:
            fields.append(PATTERNS[self.recipe.pattern].name)
        if self.entry:
            fields.extend((*self.entry.tags, str(self.entry.passcode or "")))
            if self.entry.origin:
                fields.append(self.entry.origin.id)
        return " ".join(fields).casefold()

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


def recolored(item: Choice, palette: str) -> Choice:
    if item.palette == palette:
        return item
    recipe = replace(item.recipe, palette=PALETTES[palette])
    key = Path("pair") / item.key.name / palette
    return Choice(key, item.name, palette, family_of(palette), palette, recipe)


def family_of(palette: str) -> str:
    return PALETTE_FAMILIES.get(palette, "")


def collection() -> list[Choice]:
    palettes = list(PALETTES)
    starts = {name: index for index, name in enumerate(palettes)}
    families: dict[str, list[str]] = {}
    for key, spec in PATTERNS.items():
        families.setdefault(spec.family, []).append(key)
    # Show a plate from each family before showing another from the same family.
    order = [key for group in zip_longest(*families.values()) for key in group if key]
    result = []
    for offset in range(len(palettes)):
        for key in order:
            spec = PATTERNS[key]
            palette = palettes[(starts[spec.palette] + offset) % len(palettes)]
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
        if not terms:
            result.append(item)
            continue
        if all(term in item.search_text for term in terms):
            result.append(item)
    if source == "Recently used":
        result.sort(key=lambda item: item.entry.last_used, reverse=True)
    return result


def crop_box(size: tuple[int, int], centre: tuple[float, float], zoom: float) -> Crop:
    w, h = size
    scale = min(w / WIDTH, h / HEIGHT) / zoom
    cw, ch = min(w, WIDTH * scale), min(h, HEIGHT * scale)
    return ((w - cw) * centre[0], (h - ch) * centre[1], cw, ch)
