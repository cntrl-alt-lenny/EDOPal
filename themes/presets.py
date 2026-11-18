from .model import Theme

# Inputs remain editable and use exactly the same defaults as a player's theme.
PRESETS = tuple(
    Theme(name, bg, surface, accent, wallpaper=style, roundness=radius, depth=depth)
    for name, bg, surface, accent, style, radius, depth in (
        ("Deep Current", "#071e29", "#183946", "#50e0c2", "aurora", 6, 62),
        ("Observatory", "#111429", "#282b49", "#aca2ff", "vignette", 4, 58),
        ("Ember Forge", "#251311", "#482a24", "#ffa56b", "geometry", 2, 82),
        ("Pine Lantern", "#10231e", "#253e31", "#c0df86", "gradient", 8, 38),
        ("Night Train", "#0d1b2d", "#243956", "#7fbfff", "geometry", 3, 60),
        ("Plum Velvet", "#28162c", "#493046", "#f0a7d3", "aurora", 8, 54),
        ("Graphite & Brass", "#17191c", "#343538", "#e8c477", "vignette", 2, 72),
        ("Porcelain Blue", "#d8e5ed", "#f4f8fc", "#285b94", "gradient", 5, 30),
        ("Matcha Paper", "#d8e1c4", "#f1f3df", "#427146", "vignette", 5, 32),
        ("Signal Amber", "#0d1012", "#242629", "#ffbe37", "geometry", 1, 94),
        ("Glacier Glass", "#c7e5e8", "#eaf6f5", "#006978", "aurora", 7, 42),
        ("Cinnabar", "#391416", "#582626", "#ffb080", "vignette", 3, 63),
        ("After Hours", "#191035", "#382450", "#4ef4e4", "aurora", 5, 76),
        ("Letterpress", "#e7d8bd", "#faf2e2", "#9b3529", "geometry", 1, 36),
        ("Ultramarine", "#111846", "#23367e", "#ffe570", "geometry", 3, 82),
        ("Rose Quartz", "#ecd6dc", "#fff1f1", "#933b68", "gradient", 8, 35),
    )
)
