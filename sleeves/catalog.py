"""Version-one design vocabulary. Palette order is paper, shadow, ink, highlight."""

from dataclasses import dataclass, field

# Keep swatch order stable for the palette picker and existing home/away pairs.
_SWATCHES = {
    "Midnight gilt": ("Blue", "102237 25455C C29355 F2D9A6"),
    "Koi pond": ("Green", "113B42 286867 E78D59 FFE6B4"),
    "Lacquer": ("Red", "291D2C 693643 DB6251 F5C48A"),
    "Glacier": ("Blue", "172E4D 306384 87CDD3 F0FAEC"),
    "Peacock": ("Green", "102E36 185458 49B098 F4CD76"),
    "Mulberry": ("Purple", "301C38 693453 C98CA9 F3D4AE"),
    "Porcelain": ("Blue", "ECE5D5 A9B7BC 416680 142F50"),
    "Golden hour": ("Gold", "492B42 99504F F1A063 FFE0A0"),
    "Moonstone": ("Purple", "302D49 544E71 AB9FC9 F9E8D6"),
    "Matcha": ("Green", "1F362D 425C42 A1AF74 F2DFAB"),
    "Cinnabar": ("Red", "3B2026 783630 D05C3F F8C679"),
    "Patina": ("Green", "18373C 376461 B87851 F0CD92"),
    "Kingfisher": ("Blue", "182B46 275680 61BFC2 F7A65A"),
    "Letterpress": ("Gold", "E9D6AF B5A37D 6B7161 272E35"),
    "Blue hour": ("Blue", "162341 323F72 7D89C4 E7C7BC"),
    "Obsidian": ("Neutral", "181C22 3C444B 8D999B E1DBCD"),
    "Vellum": ("Gold", "F7EDD0 CDB4A2 A64252 392A43"),
    "Copper beech": ("Red", "222837 5D4548 D88259 F7D89C"),
    "Saltire home": ("Blue", "123D66 F1F0D8"),
    "Saltire away": ("Blue", "F1F0D8 123D66"),
    "Faience": ("Green", "103F50 117F84 F0B95E FFF1BF"),
    "Papyrus": ("Gold", "F6DFAD D4A772 976031 32302E"),
    "Bog oak": ("Gold", "2B2822 625341 B89B71 F4DDB1"),
    "Henna": ("Red", "561F39 923453 ED9671 FFE4BD"),
    "Carbon silver": ("Neutral", "16191F 414953 B3BCC4 F7F6EE"),
    "Antique brass": ("Gold", "292A25 55503F C09D61 FFE0A0"),
    "Ember glass": ("Red", "32172A 792840 FF7755 FFEF9E"),
    "Sea glass": ("Green", "D4EFE6 96CBC5 357F8B 153D59"),
    "Amethyst": ("Purple", "281F42 52355F BA86D2 FDD8EC"),
    "Lemon silk": ("Gold", "F9EEAC D5BF68 81704F 283D40"),
    "Night market": ("Purple", "1C1044 37338F F174CE FFEAA8"),
    "Indian ink": ("Neutral", "F4F1E4 B4B9AA 626D68 18262D"),
    "Persimmon": ("Gold", "F5A561 E28353 8A4154 382B42"),
    "Celadon": ("Green", "E3EDCF AAC5A3 507D68 243D3A"),
    "Moss velvet": ("Green", "1F352F 3F6053 B4AB72 F4E8BB"),
    "Lapis wash": ("Blue", "C4C7FF 8A96E0 505DAD 282658"),
    "Saddle tan": ("Gold", "51302B 875540 DFA669 FCE6BB"),
    "Soap bubble": ("Purple", "26243F 55467A 79D8D2 FFC7DD"),
    "Rice paper": ("Neutral", "F9E4D8 DDB9AC 987183 433347"),
    "Relay home": ("Red", "682842 FFEAA1"),
    "Relay away": ("Gold", "FFEAA1 682842"),
    "Phosphor": ("Green", "122925 245849 77DE96 DFFFAD"),
}
PALETTES: dict[str, tuple[str, ...]] = {
    name: tuple("#" + c for c in colours.split()) for name, (_, colours) in _SWATCHES.items()
}
PALETTE_FAMILIES: dict[str, str] = {name: family for name, (family, _) in _SWATCHES.items()}


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
    family: str = ""


COMMON = (
    Parameter("scale", "Magnification", 0.65, 1.65, 1),
    Parameter("angle", "Rotation (degrees)", -180, 180, 0),
    Parameter("density", "Repetition", 0.55, 1.65, 1),
    Parameter("contrast", "Ink strength", 0.35, 1, 1),
)
# Each group contains the named control specific to each of its plates.
_FAMILIES = {
    "Textile & paper": (
        ("fans", "Silk fans", "Koi pond", "ribs", "Fan ribs", 3, 14, 8),
        ("weave", "Basket twill", "Letterpress", "threads", "Threads per band", 2, 8, 5),
        ("scales", "Seigaiha", "Porcelain", "rings", "Wave rings", 2, 7, 4),
        ("quilt", "Harlequin", "Mulberry", "gem", "Diamond inset", 0.15, 0.85, 0.47),
        ("damask", "Pomegranate damask", "Mulberry", "fruit", "Fruit width", 14, 31, 22),
        ("tartan", "Loom sett", "Moss velvet", "band", "Warp width", 12, 43, 28),
        ("marble", "Marbled endpaper", "Lapis wash", "drift", "Pigment drift", 7, 34, 20),
        ("crane", "Paper crane", "Rice paper", "fold", "Wing lift", 0.66, 1.32, 1),
    ),
    "Geometric & Deco": (
        ("stars", "Asanoha", "Midnight gilt", "inset", "Inner facet", 0.1, 0.8, 0.36),
        ("tiles", "Prism tiles", "Peacock", "facet", "Inset facet", 0.15, 0.85, 0.63),
        ("arches", "Arcade", "Lacquer", "bands", "Arch mouldings", 2, 6, 3),
        ("ripple", "Echo chamber", "Blue hour", "offset", "Focus spacing", 20, 125, 75),
        ("ribbon", "Ribbon cable", "Moonstone", "width", "Ribbon width", 8, 47, 26),
        ("steps", "Ziggurat", "Lacquer", "steps", "Terraces", 3, 12, 7),
        ("velodrome", "Velodrome", "Kingfisher", "lanes", "Track lanes", 3, 9, 5),
        ("spire", "Crown spire", "Antique brass", "crown", "Crown height", 0.65, 1.42, 1),
        ("marquetry", "Matched veneer", "Persimmon", "pitch", "Mitre slope", 0.3, 0.85, 0.56),
    ),
    "Botanical & wildlife": (
        ("sprigs", "Laurel brocade", "Matcha", "leaf", "Leaf width", 0.5, 1.6, 1),
        ("nautilus", "Nautilus", "Patina", "twist", "Shell twist", 0.5, 2.1, 1.25),
        ("quill", "Secretary feather", "Indian ink", "barb", "Vane width", 0.65, 1.38, 1),
        ("vine", "Bramble arbor", "Persimmon", "sway", "Stem sway", 34, 79, 58),
        ("ginkgo", "Pressed ginkgo", "Celadon", "fan", "Leaf spread", 0.72, 1.27, 1),
    ),
    "Celestial": (
        ("sun", "Sol invictus", "Cinnabar", "disc", "Solar disc", 24, 82, 52),
        ("orbit", "Orrery", "Midnight gilt", "tilt", "Orbit inclination", 15, 65, 34),
        ("eclipse", "Occultation", "Obsidian", "phase", "Lunar phase", 0.15, 0.8, 0.39),
        ("rain", "Meteor shower", "Kingfisher", "trail", "Trail length", 15, 100, 57),
        ("sky", "Star atlas", "Blue hour", "reach", "Star spacing", 25, 95, 60),
        ("light", "Aurora", "Matcha", "flare", "Curtain depth", 20, 130, 85),
        ("lunation", "Lunar calendar", "Carbon silver", "phase", "Moon phase", 0.15, 0.85, 0.48),
        ("aquarius", "The water bearer", "Blue hour", "pour", "Vessel tilt", 32, 72, 51),
    ),
    "Ornament": (
        ("rose", "Rose window", "Moonstone", "lobes", "Petals", 5, 14, 8),
        ("guilloche", "Engine turning", "Glacier", "lobes", "Lobes", 3, 12, 7),
        ("compass", "Wind rose", "Letterpress", "points", "Compass points", 4, 12, 8),
        ("exlibris", "Book of hours", "Vellum", "arch", "Cartouche width", 47, 97, 73),
        ("signet", "Courier seal", "Cinnabar", "seal", "Seal diameter", 82, 168, 132),
        ("hourglass", "Sand keeper", "Antique brass", "sand", "Sand remaining", 0.18, 0.82, 0.58),
    ),
    "Elements": (
        ("dunes", "Dune sea", "Golden hour", "relief", "Dune height", 8, 58, 34),
        ("alpine", "Alpenglow", "Glacier", "summit", "Peak height", 25, 125, 76),
        ("tide", "Tidal current", "Peacock", "swell", "Wave height", 8, 75, 42),
        ("brazier", "Votive flame", "Ember glass", "lean", "Flame lean", -35, 35, 8),
        ("breaker", "Whitecap", "Sea glass", "curl", "Breaker height", 0.72, 1.3, 1),
        ("mistral", "Crosswind", "Saltire away", "bend", "Windsock sag", 0.55, 1.45, 1),
        ("geode", "Split agate", "Amethyst", "core", "Crystal pocket", 0.63, 1.28, 1),
        ("clerestory", "Clerestory", "Lemon silk", "beam", "Shaft angle", 25, 98, 68),
        ("vesper", "Vesper colony", "Night market", "wings", "Bat wingspan", 0.72, 1.32, 1),
    ),
    "Heraldry": (
        ("hart", "Hart standard", "Copper beech", "rack", "Antler spread", 0.65, 1.3, 1),
        ("keep", "Three towers", "Saltire home", "tower", "Tower height", 40, 88, 64),
    ),
    "Ancient motifs": (
        ("scarab", "Scarab pectoral", "Faience", "span", "Wing sweep", 0.68, 1.26, 1),
        ("kohl", "Kohl falcon", "Papyrus", "iris", "Iris radius", 23, 42, 32),
        ("stela", "Waystone", "Bog oak", "notch", "Rune arms", 12, 37, 25),
        ("rangoli", "Chalk mandala", "Henna", "petals", "Outer petals", 6, 16, 10),
    ),
    "Materials": (
        ("timber", "Quarter sawn", "Bog oak", "knot", "Knot width", 0.62, 1.42, 1),
        (
            "alloy",
            "Satin aluminium",
            "Carbon silver",
            "glint",
            "Reflection position",
            0.28,
            0.72,
            0.5,
        ),
        ("leather", "Saddle stitch", "Saddle tan", "seam", "Seam depth", 28, 80, 51),
    ),
    "Foil": (
        ("facetfoil", "Fractured foil", "Soap bubble", "offset", "Facet offset", 0.1, 0.64, 0.38),
        ("groove", "Hot stamper", "Soap bubble", "label", "Record label", 30, 72, 48),
    ),
    "Minimal & type": (
        ("plain", "Match plain", "Relay home", "margin", "Rule inset", 9, 36, 17),
        ("edition", "Edition 01", "Relay away", "weight", "Numeral weight", 0.72, 1.28, 1),
        ("ampersand", "Foundry ampersand", "Henna", "tail", "Terminal reach", 48, 103, 78),
    ),
    "Technology": (
        ("chip", "Pin matrix", "Phosphor", "die", "Die width", 31, 66, 47),
        ("scope", "Pulse monitor", "Phosphor", "gain", "Signal gain", 38, 105, 74),
        ("pianola", "Pianola roll", "Antique brass", "slot", "Punch length", 0.55, 1.45, 1),
    ),
}
PATTERNS: dict[str, Pattern] = {
    key: Pattern(name, palette, (*COMMON, Parameter(control, label, low, high, default)), family)
    for family, plates in _FAMILIES.items()
    for key, name, palette, control, label, low, high, default in plates
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
