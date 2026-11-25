"""Theme gallery and customization."""

from edopal.core.feature import FeatureSpec


def _page(core):
    from .page import ThemesPage

    return ThemesPage(core)


def register(core) -> FeatureSpec:
    return FeatureSpec("themes", "Themes", "themes", 20, _page, "Preview and apply game themes")
