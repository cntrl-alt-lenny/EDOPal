from dataclasses import asdict
from hashlib import sha256

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from sleeves import EMBLEMS, FRAMES, PALETTES, PATTERNS, Emblem, Recipe, png_bytes, render
from sleeves.catalog import PALETTE_FAMILIES


def test_every_pattern_and_its_declared_controls() -> None:
    signatures = set()
    assert len(PATTERNS) >= 60 and len(PALETTES) >= 40
    for key, spec in PATTERNS.items():
        recipe = Recipe(key)
        image = render(recipe)
        assert (image.width(), image.height(), image.format()) == (
            708,
            1016,
            QImage.Format.Format_RGB888,
        )
        assert spec.palette in PALETTES
        defaults = {p.key: p.default for p in spec.parameters}
        assert render(Recipe(key, params=defaults)) == image
        control = spec.parameters[-1]
        assert render(Recipe(key, params={control.key: control.maximum})) != image
        signatures.add(sha256(png_bytes(image)).hexdigest())
    assert len(signatures) == len(PATTERNS)


def test_palettes_frames_emblems_and_roundtrip() -> None:
    assert len(
        {sha256(png_bytes(render(Recipe("sun", c)))).digest() for c in PALETTES.values()}
    ) == len(PALETTES)
    assert len(
        {sha256(png_bytes(render(Recipe("rain", frame=f)))).digest() for f in FRAMES}
    ) == len(FRAMES)
    for shape in EMBLEMS:
        recipe = Recipe(
            "rain",
            ("#102230", "#eeeecc"),
            {"angle": 30, "density": 1.2, "contrast": 0.9, "scale": 1.2},
            23,
            "deco",
            Emblem(shape, 0.31, 77, "#882222", "#eeeeff"),
        )
        assert render(recipe) == render(Recipe.from_dict(asdict(recipe)))
        assert render(recipe) != render(Recipe("rain", seed=23))
    assert render(Recipe("sky", seed=0)) != render(Recipe("sky", seed=1))


@pytest.mark.parametrize(
    "recipe",
    [
        Recipe("unknown"),
        Recipe("sun", params={"angle": 181}),
        Recipe("sun", params={"contrast": float("nan")}),
        Recipe("sun", params={"twist": 2}),
        Recipe("sun", ("#abc",)),
        Recipe("sun", ("#abc", "#abcdef")),
        Recipe("sun", emblem=Emblem(size=2)),
        Recipe("sun", version=2),
    ],
)
def test_invalid_recipe(recipe: Recipe) -> None:
    with pytest.raises(ValueError):
        render(recipe)


def test_palette_contrast_and_matching_team_pairs() -> None:
    def luminance(value: str) -> float:
        rgb = QColor(value).getRgbF()[:3]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    assert PALETTE_FAMILIES.keys() == PALETTES.keys()
    assert set(PALETTE_FAMILIES.values()) == {"Blue", "Green", "Red", "Purple", "Gold", "Neutral"}
    for palette in PALETTES.values():
        assert 2 <= len(palette) <= 5
        low, high = sorted((luminance(palette[0]), luminance(palette[-1])))
        assert (high + 0.05) / (low + 0.05) >= 4.5
    for team in ("Saltire", "Relay"):
        assert PALETTES[team + " home"] == PALETTES[team + " away"][::-1]


def test_families_and_thumbnail_fidelity() -> None:
    assert len({spec.family for spec in PATTERNS.values()}) >= 12
    fingerprints = set()
    for key, spec in PATTERNS.items():
        assert spec.family.strip()
        recipe = Recipe(key, PALETTES["Saltire away"], {"angle": 19}, seed=12)
        preview = render(recipe, thumbnail=True)
        # Comparing in one ink set catches identical plates sold under different defaults.
        packed = preview.convertToFormat(QImage.Format.Format_RGB32)
        fingerprints.add(sha256(packed.constBits()).digest())
        assert (preview.width(), preview.height(), preview.format()) == (
            177,
            254,
            QImage.Format.Format_RGB888,
        )
        assert render(recipe, thumbnail=True) == preview
        # Compare regions rather than individual pixels: subpixel strokes are
        # antialiased at the requested resolution, but overall ink coverage must agree.
        swatches = [
            img.scaled(
                9,
                13,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).convertToFormat(QImage.Format.Format_RGB32)
            for img in (preview, render(recipe))
        ]
        a, b = (bytes(img.constBits()) for img in swatches)
        assert sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a) < 5, key
    assert len(fingerprints) == len(PATTERNS)
