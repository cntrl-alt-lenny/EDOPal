"""Built-in plugin discovery and per-profile activation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from edopal.core.feature import FeatureSpec
from edopal.core.settings import Settings


@dataclass(frozen=True)
class PluginEntry:
    id: str
    title: str
    description: str
    artwork: str = ""
    detail: str = ""
    screenshots: tuple[tuple[str, str], ...] = ()
    stage: str = "Planned"


_CONCEPTS = (
    PluginEntry(
        "deck-tracker",
        "Deck Tracker",
        "Follow the cards remaining in your deck during a duel.",
        "deck-tracker.png",
        "See your remaining Main Deck cards and the odds of drawing each one in a duel.",
        (("deck-tracker.png", "Deck Tracker Concept"),),
        "Coming Soon",
    ),
    PluginEntry(
        "replay-library",
        "Replay Library",
        "Find a saved duel and replay it turn by turn.",
        "replay-library.webp",
        "Browse saved EDOPro replays by filename and open a duel at the turn you want to review.",
        (("replay-playback.webp", "Replay Playback"), ("replay-browser.webp", "Replay Browser")),
    ),
    PluginEntry(
        "deck-odds",
        "Deck Odds",
        "Check the chance of drawing a card in your opening hand.",
        "deck-odds.webp",
    ),
    PluginEntry(
        "practice-vs-history",
        "Practice vs History",
        "Practice against tournament decks from past formats.",
        "practice-vs-history.webp",
    ),
    PluginEntry(
        "play-with-a-buddy",
        "Play With a Buddy",
        "Invite a friend to a private duel room.",
        "play-with-a-buddy.webp",
    ),
    PluginEntry(
        "discord-status",
        "Discord Status",
        "Show your current duel in Discord.",
        "discord-status.webp",
    ),
)


class PluginRegistry:
    def __init__(self, specs: Iterable[FeatureSpec], settings: Settings):
        self._settings = settings
        self.specs = {spec.id: spec for spec in specs if spec.kind == "plugin"}
        self._enabled = {key for key in self.specs if settings.get("plugins", key) is True}
        entries = {entry.id: entry for entry in _CONCEPTS}
        for spec in self.specs.values():
            if spec.id not in entries:
                entries[spec.id] = PluginEntry(spec.id, spec.label, spec.summary)
        self.entries = tuple(entries.values())

    def is_enabled(self, plugin_id: str) -> bool:
        return plugin_id in self._enabled

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        if plugin_id not in self.specs or self.is_enabled(plugin_id) == enabled:
            return False
        self._settings.set("plugins", plugin_id, enabled)
        if enabled:
            self._enabled.add(plugin_id)
        else:
            self._enabled.discard(plugin_id)
        return True
