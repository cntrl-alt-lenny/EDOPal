"""Built-in, opt-in live Main Deck tracker."""

from edopal.core.feature import FeatureSpec

from .setup import PLUGIN_ID


def register(core):
    def page(services):
        from .page import TrackerPage

        return TrackerPage(services)

    return FeatureSpec(
        PLUGIN_ID,
        "Deck Tracker",
        "tracker",
        80,
        page,
        "Track your remaining Main Deck and draw odds during a duel.",
        kind="plugin",
    )
