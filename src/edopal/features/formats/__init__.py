"""Format Library tournaments and historical EDOPro rules."""

from edopal.core.feature import FeatureSpec


def _page(core):
    from .page import FormatsPage

    return FormatsPage(core)


def register(core) -> FeatureSpec:
    return FeatureSpec("formats", "Formats", "formats", 40, _page, summary="Historical banlists and tournament decks")
