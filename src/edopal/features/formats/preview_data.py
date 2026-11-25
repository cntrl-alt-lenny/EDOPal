"""Recorded tournament states for the development screenshot harness."""

import json
from pathlib import Path

from edopal.core.jobs import JobContext

from .client import EventPage, parse_events, parse_preview, parse_standings
from .deck import preview_metadata, read_cards
from .service import Preview, Standings

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "formats" / "fixtures"


def apply_state(page, name):
    def recorded(filename):
        return json.loads((FIXTURES / filename).read_bytes())

    ctx = JobContext()
    rows = page.service.profiles(ctx)
    page._profiles_ready(rows)
    if name in ("ocg-filter", "search", "stock", "unsupported"):
        page._switch("all")
        if name == "ocg-filter":
            page._set_region("OCG")
            page.search.setText("OCG Goat")
        else:
            page.search.setText({"search": "Ruler", "stock": "Advanced", "unsupported": "Genesys"}[name])
        page._status_ready({"stock": "stock-missing", "unsupported": "unsupported"}.get(name, "available"))
        return
    page._status_ready("available")
    events = parse_events(recorded("hat-events.json"))
    page._events_ready(1, events)
    page.selected_event = events.events[0]
    decks = parse_standings(recorded("PPAY25.json"))
    page._standings_ready(Standings(decks))
    if name in ("default", "selected", "events", "standings", "popular", "all"):
        if name in ("all", "popular"):
            page._switch(name)
    elif name in ("host", "host-settings"):
        page._host()
    elif name in ("installed", "done"):
        page._status_ready("installed")
        page._undo_ready(True)
        page._notice("HAT banlist installed.")
    elif name in ("deck", "preview", "deck-preview", "hover"):
        page.deck = next(deck for deck in decks if deck.id == 88767)
        detail = parse_preview(recorded("88767.json"), page.deck.id)
        limits = page.service.pack.whitelist("hat", ctx).limits
        install = page.core.installs.current()
        cards = read_cards(install.root)[0] if install else {}
        caps, counts = preview_metadata(detail, limits, cards)
        page._preview_ready(Preview(detail, limits=caps, composition=counts))
        if name == "hover":
            page.grid.active = next(i for i, (code, _) in enumerate(detail.printings[0]) if code == 14087893)
    elif name == "empty":
        page._events_ready(1, EventPage((), False))
        page._clear_standings()
    elif name in ("loading", "working", "error"):
        page.events_table.setRowCount(0)
        page._clear_standings()
        page._event_message("Loading Events…" if name != "error" else "Couldn’t reach Format Library.", name == "error")
    else:
        raise ValueError(f"Unknown Formats preview state: {name}")
