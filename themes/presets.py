"""Art direction for the built-in collection. Families belong to the catalogue,
so a player's editable copy carries only ordinary version-one theme fields.
"""

from .model import STYLES, Theme
from .style_names import CLASSIC_EQUIVALENTS


def _look(
    name: str,
    style: str,
    radius: int,
    depth: int,
    grounds: str,
    lettering: str,
    signals: str,
    life: str,
) -> Theme:
    """Grouped swatches follow the order in the maker's base, text, status and LP controls."""
    background, surface, accent = ("#" + rgb for rgb in grounds.split())
    text, muted = ("#" + rgb for rgb in lettering.split())
    highlight, positive, danger = ("#" + rgb for rgb in signals.split())
    your_lp, opponent_lp = ("#" + rgb for rgb in life.split())
    return Theme(
        name,
        background,
        surface,
        accent,
        text=text,
        muted=muted,
        highlight=highlight,
        positive=positive,
        danger=danger,
        your_lp=your_lp,
        opponent_lp=opponent_lp,
        wallpaper=style if style in STYLES else CLASSIC_EQUIVALENTS[style],
        roundness=radius,
        depth=depth,
    )


# Each pair of lines is one palette: materials first, then lettering, signals and life bars.
# fmt: off
FAMILIES = {
    "Club tables": (
        _look("Bottle Green", "felt", 5, 56, "064b38 155642 e4c989",
              "fff4d9 c6d4ae", "d89e80 a5d899 ffab94", "bbd49b ce8c72"),
        _look("Oxblood Rail", "felt", 3, 70, "48171e 652e37 f4ca9c",
              "fff0da dfc4b7", "b9d5ca b7daab f5a19f", "edbf81 94bbb5"),
        _look("Blue Chalk", "felt", 6, 32, "24495c 325c67 b8e4df",
              "faf6e9 c5dad5", "f2c8ad c3e5a4 f5b8a4", "9cd9d6 efbb87"),
        _look("Honey Oak", "brushed", 4, 48, "ae8250 f2dec1 684014",
              "311e15 704e34", "804947 3c6738 963b2d", "997031 5e7479"),
        _look("Chalk Stripe", "lattice", 1, 64, "23332d 293833 d4deae",
              "f0f2df b6c5ac", "e2c8a2 b7d18e f1b995", "bacfa5 d9b189"),
        _look("Plum Velvet", "aurora", 8, 54, "28162c 493046 f0a7d3",
              "ffedf5 e2bfd2", "fbd4a1 b6d6bc ffaf99", "db8ac1 efc080"),
    ),
    "Earth & ember": (
        _look("Ember Forge", "geometry", 2, 82, "251311 482a24 ffa56b",
              "ffead8 e5c0a8", "f7dba0 c6d79a ff968a", "ed985b b8bac8"),
        _look("Cinnabar", "vignette", 3, 63, "391416 582626 ffb080",
              "fff1dd ecc7bd", "f0d095 c6d9a8 ff9b96", "e38f75 f4ce88"),
        _look("Canyon Kiln", "dunes", 5, 37, "b96349 f5d5b6 77392e",
              "40261e 754c3f", "614460 365e4b 922d30", "ab5944 536e77"),
        _look("Copper Dune", "contours", 2, 62, "4a3025 695340 f9d6a7",
              "fff7e8 e2d1b9", "97d5cb cce5a0 f7ae98", "baebd7 ecae87"),
        _look("Pumice", "facets", 1, 24, "b2ada4 e6e4dc 5f574c",
              "2c2b28 625e56", "605271 3b6351 873f43", "667e75 746573"),
        _look("Lava Tube", "contours", 2, 94, "130f17 31202e ff704f",
              "fff0e2 dec2c3", "ffca64 c3e9a3 ff8d83", "fa915c c58aaf"),
    ),
    "Water & weather": (
        _look("Deep Current", "aurora", 6, 62, "071e29 183946 50e0c2",
              "effaf7 b5cec9", "f0bc9b a2dfae ffab96", "63d9be edaf8b"),
        _look("Glacier Glass", "aurora", 7, 42, "c7e5e8 eaf6f5 006978",
              "193937 4d6c6c", "494a80 306845 923c4d", "368391 8274a3"),
        _look("Tidal Glass", "facets", 7, 39, "358389 d7eee2 1c565b",
              "173d3c 49665e", "734952 245c3e 8b3a37", "368779 985963"),
        _look("Kelp Sound", "contours", 6, 33, "343f20 394b37 c7d279",
              "f6f2d8 cad1b0", "e8ba7a c6de93 f6b39f", "b9c979 94c1be"),
        _look("Squall Line", "dunes", 3, 72, "404955 283541 f7c874",
              "f1f5fa c3cdd5", "b3d6f2 b8d9c4 f9aa9a", "94b4cb eec07d"),
        _look("Salt Pan", "contours", 2, 23, "ded8c2 fffae9 386877",
              "343732 616659", "8b493d 456044 903b3d", "538c9a be7959"),
    ),
    "Garden & canopy": (
        _look("Pine Lantern", "gradient", 8, 38, "10231e 253e31 c0df86",
              "f0f5db c2cfb3", "f4ca87 c2e29a f2aa97", "c2df89 eab282"),
        _look("Matcha Paper", "vignette", 5, 32, "d8e1c4 f1f3df 427146",
              "29392b 55644b", "84563f 396943 853f3f", "658951 a76f59"),
        _look("Rose Quartz", "gradient", 8, 35, "ecd6dc fff1f1 933b68",
              "462835 745563", "704a84 3a6756 9a3a46", "ad6589 697e9d"),
        _look("Cherry After Rain", "petals", 7, 42, "2c1c2c 463649 e6adc6",
              "ffeef1 e2c5d6", "ffd6db bce0bd ffaa98", "ebadc8 cdcea1"),
        _look("Fern Press", "petals", 3, 31, "687f64 e8ecd1 405c3b",
              "263728 526348", "744f40 356139 893d39", "63885f b28b51"),
        _look("Iris House", "lattice", 8, 31, "a69cbf ede4f3 54427d",
              "332641 665474", "795249 406951 8d3d52", "846b9d 6c8975"),
    ),
    "Night sky": (
        _look("Observatory", "vignette", 4, 58, "111429 282b49 aca2ff",
              "f3f2ff c5c6e1", "edbd8a aed9c6 f7a1ae", "ab9de6 ebba85"),
        _look("Ultramarine", "geometry", 3, 82, "111846 23367e ffe570",
              "f8f7ef ced8fa", "83e0ee c4e799 ffaf97", "ffe16b 79cbdc"),
        _look("After Hours", "aurora", 5, 76, "191035 382450 4ef4e4",
              "fff0fa decced", "ffa6ce aeeaa7 ff9caa", "77ebd9 eb83bb"),
        _look("Night Train", "brushed", 3, 60, "0d1b2d 243956 7fbfff",
              "eaf4ff c0d0e4", "f7c792 b4dfae f9aaab", "86bde4 d6a57b"),
        _look("Redshift", "starfield", 2, 69, "0c142a 3b243a ff7092",
              "ffefdf dfbfbe", "f596ae b2d8b1 ffa39a", "ef9879 b291bf"),
        _look("Daybreak Orbit", "starfield", 7, 35, "aa9aba ffe0c8 654374",
              "392a41 70566a", "505d83 3a655c 963e4d", "a26681 6682a6"),
    ),
    "Paper & pigment": (
        _look("Letterpress", "geometry", 1, 36, "e7d8bd faf2e2 9b3529",
              "342b24 65584d", "405d72 365e44 942e25", "a14d3a 4e7080"),
        _look("Porcelain Blue", "gradient", 5, 30, "d8e5ed f4f8fc 285b94",
              "21324b 52657c", "82546a 41654d 8e414c", "4c76a4 aa7282"),
        _look("Indigo Proof", "lattice", 1, 78, "1c294d 354569 e9dfbc",
              "fff8df d7d7c7", "f3b179 bbd9ae f6a89a", "e7d9ac bfa6b9"),
        _look("Saffron Wash", "dunes", 6, 27, "e4b74d f8e8a7 6d4e24",
              "3d301e 665539", "874451 496441 913a37", "99752e 97526a"),
        _look("Carbon Copy", "brushed", 0, 72, "8897a8 d0d9df 244259",
              "192c3d 445b6d", "733d4a 325a49 833947", "47687b 98606a"),
        _look("Apricot Rag", "petals", 7, 22, "efd2ad ffedcf 6f4d32",
              "413325 705b49", "9b4854 476447 903f3e", "a36c52 71866e"),
    ),
    "Instruments": (
        _look("Signal Amber", "geometry", 1, 94, "0d1012 242629 ffbe37",
              "fff6e0 d8cdb4", "a1d6e7 b5dea8 ffaa86", "ffc755 8eb8cf"),
        _look("Graphite & Brass", "vignette", 2, 72, "17191c 343538 e8c477",
              "f9f1de d4cbb9", "bbd2cd c4dbac f2b19c", "d2b36c 94b7b2"),
        _look("Pocket Matrix", "lattice", 1, 80, "939e69 c9d69b 35472e",
              "202f22 45573b", "54472e 36523b 64432f", "a9bc72 45563c"),
        _look("Phosphor", "brushed", 0, 93, "061c13 103628 81ef9e",
              "dcffe1 a5dcb0", "f5dc8c 96e9ae ffd489", "83e399 e4c778"),
        _look("Tape Head", "facets", 2, 80, "5d494c 49353f eec7a2",
              "fff2d8 edcfb7", "a8e4dd b8e1af ffb4a2", "f2ba67 8ecbc8"),
        _look("Mercury Relay", "brushed", 1, 77, "323943 485462 c7efff",
              "f5fcff d4e0eb", "ffcaa9 b9dfbc ffb7aa", "aed5e9 f4bb98"),
    ),
    "Clear signals": (
        _look("Polar Ink", "facets", 1, 78, "f9f8f3 ffffff 163357",
              "101820 2c3c45", "683950 13273b 975729", "163357 eaaa40"),
        _look("Blackout Gold", "felt", 2, 80, "080909 101112 ffe45c",
              "ffffff efefdc", "b9dfff 82b7ed fff0a1", "ffdf59 648bbb"),
        _look("Tide & Ochre", "facets", 3, 62, "492716 173449 f9c66a",
              "fff7e4 e0dfca", "97d4f3 e3faff e7a061", "ffdf91 518ab3"),
        _look("Slate & Citrus", "contours", 3, 53, "d7dcc5 eef2d7 334657",
              "101c25 35444b", "735022 17344b 985c29", "234d66 e4af54"),
        _look("Mono Negative", "lattice", 0, 84, "141414 242424 f0f0f0",
              "ffffff e1e1e1", "d5d5d5 f7f7f7 bcbcbc", "eeeeee 797979"),
        _look("Lilac Signal", "starfield", 4, 58, "4c324d 2d2240 e5cdff",
              "fffafd e4def0", "ffda83 f9f1ff e4a151", "ffe297 9a80b1"),
    ),
}
# fmt: on

# These names feed persistent installation IDs. Keep the original picker order
# for clients which use the first and second colours as their starting examples.
_ORIGINAL = (
    "Deep Current",
    "Observatory",
    "Ember Forge",
    "Pine Lantern",
    "Night Train",
    "Plum Velvet",
    "Graphite & Brass",
    "Porcelain Blue",
    "Matcha Paper",
    "Signal Amber",
    "Glacier Glass",
    "Cinnabar",
    "After Hours",
    "Letterpress",
    "Ultramarine",
    "Rose Quartz",
)
_by_name = {theme.name: theme for family in FAMILIES.values() for theme in family}
PRESETS = tuple(_by_name[name] for name in _ORIGINAL) + tuple(
    theme for name, theme in _by_name.items() if name not in _ORIGINAL
)
