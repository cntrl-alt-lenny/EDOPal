"""Built-in, opt-in live Main Deck tracker and shell lifecycle entry points."""

from pathlib import Path

from edopal.core.feature import FeatureSpec

from .setup import PLUGIN_ID

HOW_TO = "Join online rooms through EDOPal Relay in EDOPro’s server list; the tracker appears beside your duel."
SETUP = "Enabling Deck Tracker adds EDOPal Relay to your server list. You can undo this in Deck Tracker."


def get_runtime(core):
    from .runtime import TrackerRuntime

    if (tracker := getattr(core, "_deck_tracker", None)) is None:
        core._deck_tracker = tracker = TrackerRuntime(core)
    return tracker


def on_enabled_changed(core, enabled):
    if enabled:
        get_runtime(core).prepare()
    elif tracker := getattr(core, "_deck_tracker", None):
        tracker.stop(drain=True)


def before_launch(core, ready, failed):
    """GUI thread. Dispatch the normal game launch once the listener is accepting."""
    if core.settings.get("plugins", PLUGIN_ID) is True:
        get_runtime(core).launch(ready, failed)
    else:
        ready()


def _exited(core):
    if tracker := getattr(core, "_deck_tracker", None):
        tracker.stop()
        tracker._status("Ready for launch")


class TrackerSpec(FeatureSpec):
    on_enabled_changed = staticmethod(on_enabled_changed)
    before_launch = staticmethod(before_launch)
    on_shutdown = staticmethod(_exited)

    def gallery_entry(self):
        from edopal.features.plugins import PluginEntry

        image = str(Path(__file__).with_name("duel.webp"))
        return PluginEntry(
            self.id, self.label, self.summary, image, f"{SETUP} {HOW_TO}", ((image, "In-Game Overlay · 1280 × 800"),)
        )


def register(core):
    def page(services):
        from .page import TrackerPage

        return TrackerPage(services)

    return TrackerSpec(
        PLUGIN_ID,
        "Deck Tracker",
        "tracker",
        80,
        page,
        "See your remaining cards and next-draw odds over your duel.",
        on_install_changed=lambda services: get_runtime(services).inspect(),
        on_game_exited=_exited,
        kind="plugin",
    )
