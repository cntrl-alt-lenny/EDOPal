"""EDOPro host profiles and offline catalogue summaries."""

from __future__ import annotations

from copy import deepcopy

from .recipes import recipe
from .release import RECORDS

STOCK = {"Advanced": "0TCG.lflist.conf", "Traditional": "Traditional.lflist.conf"}
GENESYS = "EDOPro cannot enforce the combined Genesys points budget across a deck."
GAPS = {
    "Goat": ["EDOPro caps the historical Main and Fusion Decks at 999. Use community rulings for disputed interactions."],
    "Edison": ["Damage Step windows and some LP costs differ. Check Goyo Guardian and Brionac against Edison rulings."],
    "HAT": [
        "Machina Gearframe can unequip in Defense Position. HAT requires Attack Position.",
        "Some Damage Step timing differs from the 2014 WCQ.",
    ],
    "Tengu": ["Some simultaneous triggers and Damage Step timings differ from early Xyz play."],
    "Wind-Up": ["Some Damage Step windows and Union effects differ from 2012 tournament rulings."],
    "Cat": ["Some LP costs and Damage Step timings differ. Check Rescue Cat and Dark Strike Fighter against 2009 rulings."],
    "Trooper": ["Attack replays, Traps and trigger timing can differ under the GOAT preset."],
    "Chaos": ["The GOAT preset approximates the earlier TCG rulebook. Championship rulings can differ."],
}


def profile_settings(row: dict) -> dict:
    name, region = row["name"], row["region"]
    if name == "Genesys":
        return dict(status="unsupported", playable=False, reason=GENESYS, known_gaps=[GENESYS], host={}, display="Genesys")
    old = recipe(dict(name=name, category=region, id=row["fl_id"]))
    options = ["Allowed Cards → TCG" if name in STOCK else "Allowed Cards → Anything goes", "Deck content checks on"]
    if old["base"] in ("GOAT", "Master Rule 1", "Master Rule 2") and name != "Tokyo Dome":
        options += ["First turn draw on", "One active Field Spell"]
    if old["base"] == "Master Rule 2" and region == "TCG" and name != "Tengu":
        options.append("Ignition priority off")
    options += [switch["label"] + (" on" if switch["on"] else " off") for switch in old["switches"]]
    options.extend(old["types"])
    if old["main"][1] == 999 and (region == "TCG" or name == "Tokyo Dome"):
        options.append(f"Use 0 or exactly {old['side'][1]} Side Deck cards")
    host = {key: old[key] for key in ("lp", "hand", "draw", "main", "extra", "side")}
    host.update(rule=old["base"], options=options)
    gaps = list(GAPS.get(name) or old["caveats"])
    if region == "OCG":
        gaps.append("This OCG pool uses current card printings. Historical OCG effects have not been reconstructed.")
    fields = dict(host=host, known_gaps=gaps, fidelity=old["fidelity"], playable=True)
    if name in STOCK:
        return dict(
            **fields,
            status="stock",
            stock_lflist="repositories/lflists/" + STOCK[name],
            display=row.get("display") or ("Traditional" if name == "Traditional" else "TCG Advanced"),
        )
    return dict(
        **fields,
        status="historical",
        lflist=f"lflists/{row['id']}.lflist.conf",
        display="2005.4 GOAT" if name == "Goat" else f"EDOPal {row['date'][:7]} {name}",
    )


def profiles() -> list[dict]:
    return [deepcopy(row) | profile_settings(row) for row in RECORDS]
