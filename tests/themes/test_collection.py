from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from themes import PRESETS, WIDGETS, Theme, derive, wallpaper, widget_image
from themes.colors import contrast, mix
from themes.model import OVERRIDES
from themes.presets import FAMILIES
from themes.skin import custom
from themes.wallpaper import PROCEDURAL_STYLES, procedural_wallpaper


def test_every_colourway_is_an_independently_editable_theme() -> None:
    assert len(PRESETS) == len({t.name.casefold() for t in PRESETS}) >= 48
    assert len({(t.background, t.surface, t.accent) for t in PRESETS}) == len(PRESETS)
    assert sorted(t.name for t in PRESETS) == sorted(t.name for ts in FAMILIES.values() for t in ts)
    for theme in PRESETS:
        assert all(getattr(theme, key) for key in OVERRIDES)
        assert Theme.from_json(theme.to_json()) == theme
        copy = replace(theme, name="A different name")
        assert wallpaper(copy, (160, 100)) == wallpaper(theme, (160, 100))


@pytest.mark.parametrize("theme", PRESETS, ids=lambda t: t.name)
def test_preset_contrast_on_exported_faces_and_field(theme: Theme) -> None:
    palette = derive(theme)
    for surface in (palette.surface, palette.raised, palette.inset, palette.selection):
        for color in (
            palette.text,
            palette.muted,
            palette.accent,
            palette.highlight,
            palette.positive,
            palette.danger,
        ):
            assert contrast(color, surface) >= 4.5

    # Sample the centre of each horizontal scan line in the actual PNG texture.
    # SunkenPane receives a separate composited-row test because it is translucent.
    for state in WIDGETS.keys() - {"SunkenPane"}:
        texture = widget_image(theme, state)
        ink = palette.muted if state.endswith("Disabled") else palette.text
        for y in range(4, texture.height() - 4):
            if state == "Window" and y == 25:  # one-pixel title divider
                continue
            assert contrast(ink, texture.pixelColor(texture.width() // 2, y).name()) >= 4.5
    keys = custom(theme)
    for key in (
        "DUELFIELD_CARD_LINK",
        "DUELFIELD_CARD_LEVEL",
        "DUELFIELD_HIGHER_CARD_ATK",
        "DUELFIELD_LOWER_CARD_DEF",
        "DUELFIELD_TURN_COUNT",
        "FPS_TEXT_COLOR",
    ):
        assert contrast(keys[key], theme.background) >= 4.5
    for panel in ("MAIN_INFO", "EXTRA_INFO", "SIDE_INFO", "SEARCH_RESULT", "SEARCH_RESULT_INFO"):
        value = QColor(keys[f"DECK_WINDOW_{panel}_TOP_LEFT"])
        assert contrast("#ffffff", mix("#ffffff", value.name(), value.alphaF())) >= 4.5
    for bar in palette.bars:
        for i in range(11):
            assert contrast(bar.text, mix(bar.top, bar.bottom, i / 10)) >= 4.5


@pytest.mark.parametrize("theme", FAMILIES["Clear signals"], ids=lambda t: t.name)
def test_accessible_palettes_keep_meaning_in_grayscale(theme: Theme) -> None:
    palette = derive(theme)
    assert (
        min(
            contrast(palette.text, face)
            for face in (palette.surface, palette.selection, palette.raised, palette.inset)
        )
        >= 7
    )
    # Luminance checks ensure these pairs remain distinguishable without hue.
    bars = [mix(bar.top, bar.bottom, 0.5) for bar in palette.bars]
    assert contrast(*bars) >= 2
    keys = custom(theme)
    assert contrast(keys["DUELFIELD_HIGHER_CARD_ATK"], keys["DUELFIELD_LOWER_CARD_ATK"]) >= 1.5


@pytest.mark.parametrize("style", PROCEDURAL_STYLES)
def test_wallpaper_seed_is_repeatable_and_resolution_independent(style: str) -> None:
    theme = PRESETS[0]
    a = procedural_wallpaper(theme, style, (480, 300), seed=317)
    assert a == procedural_wallpaper(theme, style, (480, 300), seed=317)
    assert a != procedural_wallpaper(theme, style, (480, 300), seed=318)
    deck = procedural_wallpaper(theme, style, (1280, 800), seed=317)
    wide = procedural_wallpaper(theme, style, (1920, 1080), seed=317, scale_background=False)
    assert deck.size().toTuple() == (1280, 800)
    assert wide.size().toTuple() == (1920, 1080)
    # The wide cover crop has the same centre. Average a small region to allow
    # fine linework to be sampled differently on the two pixel grids.
    for fx, fy in ((0.16, 0.22), (0.51, 0.51), (0.86, 0.79)):
        expected = (
            deck.copy(round(1280 * fx) - 8, round(40 + 720 * fy) - 8, 16, 16)
            .scaled(1, 1, mode=Qt.TransformationMode.SmoothTransformation)
            .pixelColor(0, 0)
        )
        actual = (
            wide.copy(round(1920 * fx) - 12, round(1080 * fy) - 12, 24, 24)
            .scaled(1, 1, mode=Qt.TransformationMode.SmoothTransformation)
            .pixelColor(0, 0)
        )
        assert (
            max(abs(a - b) for a, b in zip(expected.getRgb(), actual.getRgb(), strict=True)) <= 10
        )
