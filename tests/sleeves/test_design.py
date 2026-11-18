from dataclasses import asdict
from hashlib import sha256

import pytest
from PySide6.QtGui import QImage

from sleeves import EMBLEMS, FRAMES, PALETTES, PATTERNS, Emblem, Recipe, png_bytes, render


def test_every_pattern_and_its_declared_controls() -> None:
    signatures = set()
    assert len(PATTERNS) >= 24 and len(PALETTES) >= 16
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
