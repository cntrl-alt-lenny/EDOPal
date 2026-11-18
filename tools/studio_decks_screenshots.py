"""Offline Tournament decks states for the Studio screenshot runner."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from studio import theme
from studio.context import Installation
from studio.pages.decks import DecksPage
from studio.pages.mydecks_library import Collection
from studio.shell import StudioWindow
from tournament_decks import DeckError, publish_deck

EVENTS = (
    ("GFC-41", "Goat Format Championship 41"),
    ("GGP-BER", "Berlin Goat Grand Prix"),
    ("RFL-7", "Retro Format League Open"),
    ("SC-2025", "Summer Championship"),
    ("GFC-40", "Goat Format Championship 40"),
    ("NGO-12", "Northern Goat Open"),
    ("MILAN", "Milan Goat Grand Prix"),
    ("RC-8", "Retro Circuit Invitational"),
    ("GFC-39", "Goat Format Championship 39"),
    ("WG-6", "Weekend Goat League Final"),
    ("FG-21", "Friday Goat Cup"),
    ("OR-25", "October Regionals"),
)
STANDINGS = (
    ("Kai Sato", "Chaos Control"),
    ("Morgan Ellis", "Goat Control"),
    ("Samira Khan", "Warrior Toolbox"),
    ("Jules Moreau", "Chaos Turbo"),
    ("Esther Lin", "Reasoning Gate"),
    ("Jamie Rivera", "Empty Jar"),
    ("Rowan Bell", "Chaos Return"),
    ("Mina Park", "Goat Control"),
)
CATALOG = (
    ("Dark Magician", 1, 3),
    ("Blue-Eyes White Dragon", 1, 3),
    ("Dark Magician Girl", 1, 2),
    ("Magician of Faith", 1, 3),
    ("Tsukuyomi", 1, 3),
    ("Sangan", 1, 1),
    ("Mystic Tomato", 1, 3),
    ("Skilled Dark Magician", 1, 3),
    ("Book of Moon", 2, 3),
    ("Metamorphosis", 2, 3),
    ("Mystical Space Typhoon", 2, 1),
    ("Scapegoat", 2, 3),
    ("Nobleman of Crossout", 2, 3),
    ("Sakuretsu Armor", 4, 3),
    ("Call of the Haunted", 4, 1),
    ("Torrential Tribute", 4, 1),
    ("Mirror Force", 4, 1),
)
EXTRA = ("Thousand-Eyes Restrict", "Dark Balter the Terrible", "Ryu Senshi")
SIDE = ("Royal Decree", "Dust Tornado", "Kycoo the Ghost Destroyer", "Brain Control")


def _sections() -> tuple[list[dict], ...]:
    main = [
        {"name": name, "artworkId": code}
        for code, (name, _, count) in enumerate(CATALOG, 1000)
        for _ in range(count)
    ]
    extra = [{"name": name, "artworkId": i} for i, name in enumerate(EXTRA, 1040) for _ in range(2)]
    side = [{"name": name, "artworkId": i} for i, name in enumerate(SIDE, 1050) for _ in range(3)]
    return main, extra, side


def _published_deck() -> str:
    return "\n".join(
        line
        for marker, section in zip(("#main", "#extra", "!side"), _sections(), strict=True)
        for line in (marker, *(str(card["artworkId"]) for card in section))
    )


class Archive:
    def __init__(self) -> None:
        self.goat_open = False
        self.entered = Event()
        self.release = Event()
        self.event_release: Event | None = None
        self.hold: int | None = None
        self.fail: int | None = None
        self.details_fail = False

    def response(self, url: str, *, limit: int) -> bytes:
        parsed = urlsplit(url)
        path = parsed.path.removeprefix("/api/")
        if path == "formats":
            value = [
                {"name": name} for name in ("Goat", "Edison", "Tengu", "HAT", "Perfect Circle")
            ]
        elif path == "events":
            if self.event_release is not None:
                self.event_release.wait(5)
            args = parse_qs(parsed.query)
            filters = args.get("filter", [""])[0]
            if "name:or:Offline" in filters:
                raise DeckError(
                    "Check your connection to Format Library and try again."
                ) from URLError("offline")
            if "name:or:Error" in filters:
                raise DeckError("Format Library returned an unexpected page. Try again shortly.")
            value = (
                [
                    {
                        "abbreviation": code,
                        "name": name,
                        "formatName": "Goat",
                        "startedAt": f"2025-{11 - i // 2:02}-{'21' if i % 2 == 0 else '08'}",
                        "size": 96,
                    }
                    for i, (code, name) in enumerate(EVENTS)
                ]
                if "name:or:" not in filters and args["page"] == ["1"]
                else []
            )
        elif path.startswith("events/"):
            value = {
                "topDecks": [
                    {"id": i, "deckTypeName": deck, "builderName": player, "placement": i}
                    for i, (player, deck) in enumerate(STANDINGS, 1)
                ]
                if path != "events/GGP-BER"
                else []
            }
        elif path.startswith("decks/download/"):
            ident = int(path.rsplit("/", 1)[1])
            if ident == self.hold:
                self.entered.set()
                self.release.wait(5)
            if ident == self.fail:
                raise DeckError("Format Library couldn’t provide this deck. Try again shortly.")
            return _published_deck().encode()
        elif path.startswith("decks/"):
            if self.details_fail:
                raise DeckError("The published decklist is temporarily unavailable.")
            value = {
                "id": int(path.rsplit("/", 1)[1]),
                "display": True,
                "ydk": _published_deck(),
                **dict(zip(("main", "extra", "side"), _sections(), strict=True)),
            }
        else:
            raise AssertionError(f"Unexpected Format Library route {path}")
        return json.dumps(value).encode()


def _install_preview_cards(game: Path) -> None:
    with closing(sqlite3.connect(game / "cards.cdb")) as db, db:
        db.execute("ALTER TABLE datas ADD COLUMN alias INTEGER DEFAULT 0")
        for i, (name, kind, _) in enumerate(CATALOG, 1000):
            db.execute("UPDATE datas SET type=? WHERE id=?", (kind, i))
            db.execute("UPDATE texts SET name=? WHERE id=?", (name, i))
        for i, name in enumerate(EXTRA, 1040):
            db.execute("UPDATE texts SET name=? WHERE id=?", (name, i))
        for i, name in enumerate(SIDE, 1050):
            db.execute("UPDATE datas SET type=? WHERE id=?", ((4, 4, 1, 2)[i - 1050], i))
            db.execute("UPDATE texts SET name=? WHERE id=?", (name, i))
        for code, name, alias in (
            (5001, "Dark Magician (GOAT)", 1000),
            (5002, "Blue-Eyes White Dragon (GOAT)", 1001),
            (5003, "Dark Magician Girl (Pre-Errata)", 1002),
        ):
            db.execute("INSERT INTO datas VALUES (?, 1, ?)", (code, alias))
            db.execute("INSERT INTO texts VALUES (?, ?)", (code, name))
    limits = [
        f"{i} 3"
        for i in (*range(1003, 1016), *range(1040, 1043), *range(1050, 1054), 5001, 5002, 5003)
    ]
    (game / "lflist.conf").write_text(
        "\n".join(("!2005.4 GOAT", "$whitelist", *limits, "1016 0")), encoding="utf-8"
    )
    pics = game / "pics"
    for section in _sections():
        for card in section:
            (pics / f"{card['artworkId']}.jpg").unlink(missing_ok=True)
    # Use three complete card thumbnails already shown in the bundled deck-editor reference.
    with Image.open(theme.ASSETS / "window/gallery/large/abyss-deck.png") as reference:
        for i, left in enumerate((275, 318, 360)):
            reference.crop((left, 144, left + 37, 199)).convert("RGB").save(
                pics / f"{1000 + i}.jpg"
            )


def render_decks(
    window: StudioWindow,
    selected: Installation,
    shot: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
    *,
    overview: str | None = None,
) -> None:
    archive = Archive()
    page = window.pages["decks"]
    assert isinstance(page, DecksPage)
    browser = page.browser
    game = Path(selected.path)

    def capture(name: str) -> None:
        page.slide.setCurrentTime(theme.MOTION_MS)
        shot(f"decks-{name}")

    def select(row: int) -> None:
        browser.events.setCurrentItem(browser.events.topLevelItem(row))
        settle(lambda: "standings" not in page.reads and "goat" not in page.reads)

    def search(term: str) -> None:
        browser.search.setText(term)
        browser.submit.click()
        settle(lambda: "events" not in page.reads and "standings" not in page.reads)

    def thumbnails_ready() -> bool:
        gallery = page.preview.contents.grid
        tiles = gallery.tiles
        visible = [
            tile.path
            for row, tile in enumerate(tiles.items)
            if gallery.cards[row].artwork
            and gallery.viewport().rect().intersects(gallery.visualRect(tiles.index(row, 0)))
        ]
        return bool(visible) and all(path in tiles.cache for path in visible)

    with (
        patch("tournament_decks._fetch", archive.response),
        patch("studio.pages.decks_flow._game_running", lambda _: archive.goat_open),
    ):
        if overview:
            window.navigate("decks")
            settle(lambda: not window.ctx.tasks.jobs)
            shot(overview)
            return
        settle(lambda: not window.ctx.tasks.jobs)
        _install_preview_cards(game)
        window.ctx.set_installation(Installation())
        window.navigate("decks")
        capture("01-no-installation")
        archive.event_release = Event()
        window.ctx.set_installation(Installation(selected.path))
        capture("15-loading")
        archive.event_release.set()
        settle(lambda: browser.standings.topLevelItemCount() > 0)
        capture("02-awaiting-cards")
        window.ctx.set_installation(selected)
        settle(lambda: browser.standings.topLevelItemCount() > 0 and page.goat_available)
        capture("03-standings")
        archive.details_fail = True
        browser.preview.click()
        settle(page.preview.message.retry.isVisible)
        capture("16-preview-error")
        archive.details_fail = False
        page.preview.message.retry.click()
        settle(thumbnails_ready)
        capture("04-preview")
        page.preview.contents.sections.buttons[1].click()
        capture("05-extra")
        page.preview.contents.sections.buttons[2].click()
        capture("17-side")
        page.preview.back.click()

        select(1)
        capture("06-no-public-decks")
        search("nothing matches")
        capture("07-no-events")
        search("Offline")
        capture("08-offline")
        search("Error")
        capture("09-error")
        search("")
        settle(lambda: page.goat_available)

        archive.goat_open = True
        browser.save.click()
        settle(lambda: page.view is page.results and not window.ctx.busy)
        capture("10-close-edopro")
        archive.goat_open = False
        page.results.back.click()
        browser.goat.setChecked(True)
        browser.select_all.click()
        event = page.selection.event
        assert event
        first = page.selection.decks[0]
        prior = publish_deck(game, event, first, (("1006",), (), ()), goat_versions=True)
        prior.path.write_text("#main\n1003\n#extra\n!side\n", encoding="utf-8")
        archive.hold = 3
        browser.save.click()
        settle(lambda: archive.entered.is_set() and page.import_view.progress.bar.value() == 2)
        capture("11-saving")
        page.import_view.progress.cancel.click()
        archive.release.set()
        settle(lambda: page.view is page.results and not window.ctx.busy)
        capture("12-partial-receipt")
        page.results.review.click()
        capture("13-goat-review")
        page.review.back.click()
        page.results.back.click()

        browser.standings.setCurrentItem(browser.standings.topLevelItem(2))
        browser.save.click()
        settle(lambda: page.view is page.results and not window.ctx.busy)
        capture("14-saved")
        page.results.open_saved.click()
        mydecks = window.pages["mydecks"]
        settle(lambda: isinstance(mydecks.view, Collection))
        window.animation.setCurrentTime(theme.MOTION_MS)
        shot("decks-20-selected-in-my-decks")
        window.navigate("decks")
        with patch.object(window.ctx, "confirm", return_value=True):
            page.results.undo.click()
            settle(lambda: page.results.title.value == "Import undone")
        capture("18-undone")
        page.results.back.click()
        archive.fail = 5
        browser.standings.setCurrentItem(browser.standings.topLevelItem(3))
        browser.standings.topLevelItem(4).setSelected(True)
        browser.save.click()
        settle(lambda: page.view is page.results and not window.ctx.busy)
        page.results.rows.setCurrentItem(page.results.rows.topLevelItem(1))
        capture("19-partial-error")
        window.ctx.set_installation(selected)
        settle(lambda: not window.ctx.tasks.jobs)
