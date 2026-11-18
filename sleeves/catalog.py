"""Version-one design vocabulary. Palette order is paper, shadow, ink, highlight."""

from dataclasses import dataclass, field

PALETTES: dict[str, tuple[str, ...]] = {
    name: tuple("#" + c for c in colours.split())
    for name, colours in {
        "Midnight gilt": "102237 25455C C29355 F2D9A6",
        "Koi pond": "113B42 286867 E78D59 FFE6B4",
        "Lacquer": "291D2C 693643 DB6251 F5C48A",
        "Glacier": "172E4D 306384 87CDD3 F0FAEC",
        "Peacock": "102E36 185458 49B098 F4CD76",
        "Mulberry": "301C38 693453 C98CA9 F3D4AE",
        "Porcelain": "ECE5D5 A9B7BC 416680 142F50",
        "Golden hour": "492B42 99504F F1A063 FFE0A0",
        "Moonstone": "302D49 544E71 AB9FC9 F9E8D6",
        "Matcha": "1F362D 425C42 A1AF74 F2DFAB",
        "Cinnabar": "3B2026 783630 D05C3F F8C679",
        "Patina": "18373C 376461 B87851 F0CD92",
        "Kingfisher": "182B46 275680 61BFC2 F7A65A",
        "Letterpress": "E9D6AF B5A37D 6B7161 272E35",
        "Blue hour": "162341 323F72 7D89C4 E7C7BC",
        "Obsidian": "181C22 3C444B 8D999B E1DBCD",
    }.items()
}


@dataclass(frozen=True)
class Parameter:
    key: str
    label: str
    minimum: float
    maximum: float
    default: float


@dataclass(frozen=True)
class Pattern:
    name: str
    palette: str
    parameters: tuple[Parameter, ...]


COMMON = (
    Parameter("scale", "Magnification", 0.65, 1.65, 1),
    Parameter("angle", "Rotation (degrees)", -180, 180, 0),
    Parameter("density", "Repetition", 0.55, 1.65, 1),
    Parameter("contrast", "Ink strength", 0.35, 1, 1),
)
# Each plate has a named engraving control in addition to the four shared controls.
PATTERNS: dict[str, Pattern] = {
    key: Pattern(name, palette, (*COMMON, Parameter(control, label, low, high, default)))
    for key, name, palette, control, label, low, high, default in (
        ("fans", "Silk fans", "Koi pond", "ribs", "Fan ribs", 3, 14, 8),
        ("weave", "Basket twill", "Letterpress", "threads", "Threads per band", 2, 8, 5),
        ("stars", "Asanoha", "Midnight gilt", "inset", "Inner facet", 0.1, 0.8, 0.36),
        ("scales", "Seigaiha", "Porcelain", "rings", "Wave rings", 2, 7, 4),
        ("tiles", "Prism tiles", "Peacock", "facet", "Inset facet", 0.15, 0.85, 0.63),
        ("quilt", "Harlequin", "Mulberry", "gem", "Diamond inset", 0.15, 0.85, 0.47),
        ("sprigs", "Laurel brocade", "Matcha", "leaf", "Leaf width", 0.5, 1.6, 1),
        ("arches", "Arcade", "Lacquer", "bands", "Arch mouldings", 2, 6, 3),
        ("sun", "Sol invictus", "Cinnabar", "disc", "Solar disc", 24, 82, 52),
        ("orbit", "Orrery", "Midnight gilt", "tilt", "Orbit inclination", 15, 65, 34),
        ("nautilus", "Nautilus", "Patina", "twist", "Shell twist", 0.5, 2.1, 1.25),
        ("rose", "Rose window", "Moonstone", "lobes", "Petals", 5, 14, 8),
        ("eclipse", "Occultation", "Obsidian", "phase", "Lunar phase", 0.15, 0.8, 0.39),
        ("guilloche", "Engine turning", "Glacier", "lobes", "Lobes", 3, 12, 7),
        ("compass", "Wind rose", "Letterpress", "points", "Compass points", 4, 12, 8),
        ("ripple", "Echo chamber", "Blue hour", "offset", "Focus spacing", 20, 125, 75),
        ("dunes", "Dune sea", "Golden hour", "relief", "Dune height", 8, 58, 34),
        ("alpine", "Alpenglow", "Glacier", "summit", "Peak height", 25, 125, 76),
        ("rain", "Meteor shower", "Kingfisher", "trail", "Trail length", 15, 100, 57),
        ("sky", "Star atlas", "Blue hour", "reach", "Star spacing", 25, 95, 60),
        ("tide", "Tidal current", "Peacock", "swell", "Wave height", 8, 75, 42),
        ("ribbon", "Ribbon cable", "Moonstone", "width", "Ribbon width", 8, 47, 26),
        ("light", "Aurora", "Matcha", "flare", "Curtain depth", 20, 130, 85),
        ("steps", "Ziggurat", "Lacquer", "steps", "Terraces", 3, 12, 7),
    )
}
FRAMES = ("none", "keyline", "deco", "observatory")
EMBLEMS = ("diamond", "crescent", "star", "shield", "lotus", "sun")


@dataclass(frozen=True)
class Emblem:
    """Size is a fraction of sleeve width. Empty fill/outline inherit palette colours."""

    shape: str = "diamond"
    size: float = 0.24
    rotation: float = 0
    fill: str = ""
    outline: str = ""


@dataclass(frozen=True)
class Recipe:
    """Empty palette uses the pattern's palette. Omitted controls use catalog defaults."""

    pattern: str
    palette: tuple[str, ...] = ()
    params: dict[str, float] = field(default_factory=dict)
    seed: int | None = None
    frame: str = "keyline"
    emblem: Emblem | None = None
    version: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        values = dict(data)
        values["palette"] = tuple(values.get("palette", ()))
        if values.get("emblem") is not None:
            values["emblem"] = Emblem(**values["emblem"])
        return cls(**values)
