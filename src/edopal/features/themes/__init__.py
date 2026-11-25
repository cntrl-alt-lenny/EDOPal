"""Theme gallery and independent card-back selection."""

from edopal.core.feature import FeatureSpec


def _page(core):
    from .page import ThemesPage

    return ThemesPage(core)


def register(core) -> FeatureSpec:
    return FeatureSpec("themes", "Themes", "themes", 20, _page, "Game themes and card backs")
