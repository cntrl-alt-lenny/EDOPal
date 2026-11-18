"""Wallpaper vocabulary, kept independent of Qt so JSON validation can share it."""

# Four version-one choices already occur in player libraries. The value is the
# nearest legacy treatment for clients whose definition validator only knows those four.
CLASSIC_STYLES = ("gradient", "vignette", "geometry", "aurora")
CLASSIC_EQUIVALENTS = {
    "felt": "gradient",
    "starfield": "vignette",
    "contours": "aurora",
    "dunes": "gradient",
    "petals": "vignette",
    "brushed": "geometry",
    "lattice": "geometry",
    "facets": "geometry",
}
PROCEDURAL_STYLES = (*CLASSIC_STYLES, *CLASSIC_EQUIVALENTS)
