"""Deterministic states for the development screenshot harness."""

from __future__ import annotations

import json
from pathlib import Path

from .builder.profiles import profiles
from .client import EventPage, parse_events, parse_preview, parse_standings
from .service import Preview, Standings

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "formats" / "fixtures"


def apply_state(page, name):
    def recorded(filename):
        return json.loads((FIXTURES / filename).read_text(encoding="utf-8"))

    rows = profiles()
    if name == "stock":
        row = next(row for row in rows if row["id"] == "advanced")
        row.update(display="Current stock TCG", hash="")
    page._profiles_ready(rows)
    if name in ("ocg-filter", "search", "stock", "unsupported"):
        if name == "ocg-filter":
            page._set_region("OCG")
            model = page.picker.model()
            for row in range(model.rowCount()):
                index = model.index(row, 0)
                if index.data() == "OCG Goat":
                    page.picker.setCurrentIndex(index)
                    break
        else:
            page.search.setText({"search": "Ruler", "stock": "Advanced", "unsupported": "Genesys"}[name])
        page._status_ready({"stock": "stock-missing", "unsupported": "unsupported"}.get(name, "available"))
        page._host()
        return
    page._status_ready("available")
    events = parse_events(recorded("hat-events.json"))
    page._events_ready(1, events)
    if name in ("events", "selected", "default"):
        return
    if name in ("host", "host-settings"):
        page._host()
    elif name in ("installed", "done"):
        page._status_ready("installed")
        page._undo_ready(True)
        page._notice("HAT banlist installed. Restart EDOPro and choose EDOPal 2014-07 HAT.")
    elif name in ("standings", "deck", "preview", "deck-preview"):
        page.selected_event = events.events[0]
        decks = parse_standings(recorded("PPAY25.json"))
        page._standings_ready(Standings(decks))
        if name != "standings":
            page.deck = decks[0]
            detail = parse_preview(recorded("88769.json"), page.deck.id)
            page._preview_ready(Preview(detail, (FIXTURES / "88769.png").read_bytes()))
    elif name == "empty":
        page._events_ready(1, EventPage((), False))
    elif name in ("loading", "working"):
        page._empty("Loading recent events…")
    elif name == "error":
        page._empty("Couldn’t reach Format Library. Check your connection and try again.", page._return_events)
    else:
        raise ValueError(f"Unknown Formats preview state: {name}")
