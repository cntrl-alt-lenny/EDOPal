"""Card backs and shared duel icons."""

from edopal.core.feature import FeatureSpec


def _page(core):
    from .page import CosmeticsPage

    return CosmeticsPage(core)


def register(core):
    return FeatureSpec("cosmetics", "Card Backs & Icons", "cardbacks", 21, _page, "Choose card backs and duel icons")
