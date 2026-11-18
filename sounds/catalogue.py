"""Duel cues and card-specific chant slots in EDOPro 41.0.2."""

import re
from dataclasses import dataclass
from types import MappingProxyType

FORMATS = ("wav", "mp3", "ogg", "flac")
CHANT_FORMATS = tuple(sorted(FORMATS))
_CHANT_KEY = re.compile(r"chant/(activate|attack|summon)/([1-9][0-9]{0,9})")


@dataclass(frozen=True)
class Event:
    stem: str
    name: str
    description: str
    group: str
    formats: tuple[str, ...] = FORMATS


EVENTS = (
    Event("summon", "Normal Summon", "A monster is Normal Summoned.", "Summoning"),
    Event("specialsummon", "Special Summon", "A monster is Special Summoned.", "Summoning"),
    Event("flip", "Flip Summon", "A monster is Flip Summoned.", "Summoning"),
    Event("token", "Token", "The client defines a slot for Token cues.", "Summoning"),
    Event("attack", "Attack", "A monster declares an attack.", "Battle"),
    Event(
        "directattack",
        "Direct attack",
        "The client defines a slot for direct-attack cues.",
        "Battle",
    ),
    Event("damage", "Damage", "A player takes damage or pays Life Points.", "Life Points"),
    Event("gainlp", "Recover Life Points", "A player gains Life Points.", "Life Points"),
    Event("activate", "Activate", "A card or effect is activated.", "Cards"),
    Event("set", "Set", "A card is Set on the field.", "Cards"),
    Event("reveal", "Reveal", "The client defines a slot for card-reveal cues.", "Cards"),
    Event("equip", "Equip", "A card is equipped to another card.", "Cards"),
    Event("destroyed", "Destroy", "A card is destroyed.", "Cards"),
    Event("banished", "Banish", "A card is banished.", "Cards"),
    Event("draw", "Draw", "A player draws cards.", "Cards"),
    Event("shuffle", "Shuffle", "Cards are shuffled.", "Cards"),
    Event("addcounter", "Add counter", "Counters are added to a card.", "Cards"),
    Event("removecounter", "Remove counter", "Counters are removed from a card.", "Cards"),
    Event("coinflip", "Coin flip", "A coin is flipped.", "Duel flow"),
    Event("diceroll", "Dice roll", "Dice are rolled.", "Duel flow"),
    Event("nextturn", "Next turn", "A new turn begins.", "Duel flow"),
    Event("phase", "Phase change", "The duel enters a new phase.", "Duel flow"),
    Event("playerenter", "Player joins", "A player joins the room.", "Interface"),
    Event("chatmessage", "Chat message", "A room chat message arrives.", "Interface"),
)
EVENT_BY_STEM = MappingProxyType({event.stem: event for event in EVENTS})
CHANT_TYPES = MappingProxyType(
    {
        "activate": "A card-specific clip can replace the Activate cue.",
        "attack": "A card-specific clip can replace the Attack cue.",
        "summon": "A card-specific clip can replace Normal, Special, and Flip Summon cues.",
    }
)


def chant_key(kind: str, passcode: int) -> str:
    """Return the set key for an activation, attack, or summon chant."""
    key = f"chant/{kind}/{passcode}"
    event_for(key)
    return key


def event_for(key: str) -> Event:
    if key in EVENT_BY_STEM:
        return EVENT_BY_STEM[key]
    match = _CHANT_KEY.fullmatch(key)
    if match and int(match[2]) <= 0xFFFFFFFF:
        kind, passcode = match.groups()
        return Event(
            passcode,
            f"{kind.title()} chant · {passcode}",
            CHANT_TYPES[kind],
            "Chants",
            CHANT_FORMATS,
        )
    raise ValueError(f"Unknown sound event: {key!r}.")


def cue_filename(key: str, format: str) -> str:
    """Return the filename relative to the installation's sound folder."""
    if format not in event_for(key).formats:
        raise ValueError(f"Unsupported sound format: {format!r}.")
    return f"{key.removeprefix('chant/')}.{format}"


def event_key_for_path(relative: str) -> str:
    """Validate a journal path and return its event key."""
    stem, _, format = relative.removeprefix("sound/").rpartition(".")
    key = f"chant/{stem}" if "/" in stem else stem
    if relative != "sound/" + cue_filename(key, format):
        raise ValueError(f"Unrecognised sound path: {relative!r}.")
    return key
