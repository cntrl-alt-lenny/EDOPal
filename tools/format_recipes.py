"""Recommended host settings transcribed from the EDOPro 41.0.2 historical rules review."""

from __future__ import annotations

from dataclasses import dataclass

SWITCH = {
    1: "OCG Ignition Priority",
    2: "First turn draw",
    4: "Pendulum Zones",
    6: "Extra Monster Zones",
    7: "Fusion Synchro Xyz summonable anywhere",
    17: "Unlimited hand size",
    20: "Traps must wait resolving",
    21: "6 substeps Battle Step",
    22: "Trigger even if private knowledge",
    24: "0 ATK vs 0 ATK destroys both monsters",
    25: "Store attack replays",
    26: "Only 1 Chain in damage substep",
    27: "Can change position if summoned by your opponent",
    28: "TCG SEGOC",
    29: "SEGOC: First triggered effect first in chain",
    30: "GOAT Ignition Priority",
}
PRESET = (
    "GOAT",
    "Master Rule 1",
    "Master Rule 2",
    "Master Rule 3",
    "Master Rule 4",
    "Master Rules (2020)",
)
FILTERS = (
    ("No Xyz", "No Pendulum", "No Link"),
    ("No Xyz", "No Pendulum", "No Link"),
    ("No Pendulum", "No Link"),
    ("No Link",),
    (),
    (),
)


@dataclass(frozen=True)
class Group:
    names: str
    preset: int
    fidelity: str
    caveat: str
    on: tuple[int, ...] = ()
    off: tuple[int, ...] = ()
    extra_filters: tuple[str, ...] = ()
    early: bool = False


GROUPS = (
    Group(
        "Goat",
        0,
        "Close",
        "The GOAT preset includes the first turn draw. The 999-card limit stands in for an uncapped deck. Community rulings still apply.",
        early=True,
    ),
    Group(
        "Yugi-Kaiba|Critter|Treasure|Imperial|Android|Joey-Pegasus|Fiber|Yata|Scientist|Vampire|Chaos|Warrior|Cyber",
        0,
        "Approximate",
        "The 2005 preset covers several earlier rulebooks. World Championship and regional rulings can differ.",
        early=True,
    ),
    Group(
        "Reaper|Chaos Return|Stein|Chimeratech|Demise|Trooper|Zombie|Perfect Circle|DAD Return|Gladiator",
        0,
        "Approximate",
        "The early deck sizes match. The dates of changes to attack replays, Traps and triggers are uncertain.",
        early=True,
    ),
    Group(
        "TeleDAD|Cat|Lightsworn|Edison|Frog|X-Saber|Starstrike|Trishula",
        1,
        "Approximate",
        "Some Damage Step windows, LP costs and card rulings differ. Keep the preset’s other settings, including the unchecked single-chain Damage Step option.",
        (21, 22, 24, 28, 29, 30),
        (1,),
    ),
    Group(
        "Tengu",
        2,
        "Close",
        "Xyz Summons and early TCG priority apply. Damage Step timing and individual trigger locations can differ.",
        (21, 28, 29, 30),
    ),
    Group(
        "Dino Rabbit|Wind-Up|Miami|Meadowlands|Baby Ruler|Ravine Ruler|Fire-Water|Vegas|HAT",
        2,
        "Close",
        "Keep ignition priority off. The first player draws and only one Field Spell stays face up. Damage Step timing and Union effects can differ.",
        (21, 28, 29),
    ),
    Group(
        "Shaddoll|London|Burning Abyss|Charleston|Nekroz|Clown|Performapal|Dracoslayer|Monarch|Invasion|Grass Zoo|Draco Zoo",
        3,
        "Close",
        "Separate scales, two Field Spells and the opening draw rule match. Individual card texts and rulings can differ.",
        (28, 29),
    ),
    Group(
        "Link Zoo|Decode|SPYRAL|Magician|Secaucus|Danger 1|Danger 2|Sky Striker|Thunder Dragon|Lunalight|TOSS|Rokket",
        4,
        "Close",
        "Link placement and integrated scales match. Current card scripts and the simulator clock can differ from tournament play.",
        (28,),
    ),
    Group(
        "Adamancipator|Infernoble|Virtual World|Dragon Link|Tri-Brigade",
        4,
        "Close",
        "Fusion, Synchro and Xyz Monsters can use ordinary zones. Adoption of these changes at individual Remote Duel events is uncertain.",
        (7, 28),
    ),
    Group(
        "Bird-Up|Phoenix|Despia|Elements|Darkwing|Tearlaments|Hypernova|Kashtira|Overlord|Fire King|Snake-Eye|Fiendsmith|Lacrima|Ryzeal|Maliss|Justice|Mjollnir|Doom|Protocol",
        5,
        "Close",
        "Card scripts follow current OCG rulings. The response clock differs from a tournament match timer.",
        (28,),
    ),
    Group(
        "Tokyo Dome",
        1,
        "Approximate",
        "The reconstructed LP-based deck-out result, early battle resolution and Trap behaviour require agreement.",
        (17,),
        (2,),
        ("No Synchro",),
        True,
    ),
    Group(
        "Exodia|OCG Treasure|Snake|Cannon|OCG Imperial|Mirror|OCG Android|Makyura|OCG Yata|Demon|Goblin|Envoy|OCG Chaos|Catapult|Whale|OCG Goat|Victory|Baboon|OCG Airblade|Destiny|Armageddon",
        1,
        "Approximate",
        "Early OCG priority, individual switches and the period’s Side Deck rules need confirmation.",
        (21,),
        extra_filters=("No Synchro",),
        early=True,
    ),
    Group(
        "Dark Cat|OCG TeleDAD|Dark Strike|Lightlord|Infernity|Crow",
        1,
        "Close",
        "The board, first draw and OCG priority match. Damage Step timing and the 0 ATK cutoff are uncertain.",
        (21,),
    ),
    Group(
        "Nexus|Agent|Inzektor|Cosmo|OCG Baby Ruler|OCG Ravine Ruler|Incarnate",
        2,
        "Close",
        "Xyz, the opening draw and a single Field Spell match. Damage Step timing and period card scripts can differ.",
        (21,),
    ),
    Group(
        "Advanced|Traditional",
        5,
        "Close",
        "Choose the current matching game list. The game and Format Library can update on different dates.",
        (28,),
    ),
    Group(
        "Genesys",
        5,
        "Approximate",
        "Agree on the points list and check the 100-point budget across the deck yourselves.",
        (28,),
        (4, 6, 7),
        ("No Pendulum", "No Link"),
    ),
)
BY_NAME = {name: group for group in GROUPS for name in group.names.split("|")}


def recipe(row: dict) -> dict:
    name = row["name"]
    g = BY_NAME[name]
    side = 10 if name == "Tokyo Dome" else 15
    caveats = [g.caveat]
    if g.early and (row["category"] == "TCG" or name == "Tokyo Dome"):
        caveats.append(f"Agree to use either no Side Deck or exactly {side} cards.")
    if name == "Trishula":
        caveats.append("The 0 ATK setting assumes this event preceded the ruling change.")
    return dict(
        id=row["id"],
        client="41.0.2",
        base=PRESET[g.preset],
        switches=[dict(label=SWITCH[n], on=n in g.on) for n in sorted((*g.on, *g.off))],
        types=[*g.extra_filters, *FILTERS[g.preset]],
        lp=8000,
        hand=5,
        draw=1,
        main=[40, 999 if g.early else 60],
        extra=[0, 999 if g.early else 15],
        side=[0, side],
        fidelity=g.fidelity,
        caveats=caveats,
    )
