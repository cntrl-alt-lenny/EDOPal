"""Replay the HAT responses recorded from Format Library on 2026-11-25."""

from __future__ import annotations

import json
from email.utils import formatdate
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from edopal.features.formats import http_cache
from edopal.features.formats.client import (
    EVENT_TTL,
    WEEK,
    Deck,
    DeckError,
    FormatLibrary,
    ItemUnavailable,
    parse_events,
    parse_standings,
)
from edopal.features.formats.deck import read_ydk

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def network(monkeypatch):
    class Replay:
        now = 1_800_000_000.0

        def __init__(self):
            self.responses = []
            self.requests = []

        def get(self, url, etag, limit):
            self.requests.append((url, etag))
            assert self.responses, f"Unexpected GET {url}"
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        def fixture(self, name):
            self.responses.append((200, {"ETag": f'"{name}"'}, (FIXTURES / name).read_bytes()))

    replay = Replay()
    monkeypatch.setattr(http_cache, "_get", replay.get)
    monkeypatch.setattr(http_cache.time, "time", lambda: replay.now)
    return replay


def test_recorded_tournament_journey(tmp_path, network):
    fl = FormatLibrary(tmp_path)
    network.fixture("hat-events.json")
    page = fl.events("HAT")
    assert page.has_next and not page.stale and len(page.events) == 12
    assert parse_qs(urlsplit(network.requests[0][0]).query) == {
        "page": ["1"],
        "limit": ["12"],
        "sort": ["startedAt:desc"],
        "filter": ["format:eq:HAT"],
    }
    event = page.events[0]
    assert (event.code, event.name, event.date, event.players) == ("PPAY25", "Pleiades Payout 25", "2026-10-23", 14)
    network.fixture("PPAY25.json")
    decks = fl.standings(event)
    assert [d.place for d in decks] == [1, 2, 3, 4]
    winner = Deck(88769, "Mermail", "goodmorning", 1)
    assert decks[0] == winner
    network.fixture("88769.json")
    preview = fl.preview(winner)
    assert [sum(count for _, count in section) for section in preview.sections] == [40, 15, 15]
    network.fixture("88769.png")
    assert fl.thumbnail(winner).startswith(b"\x89PNG")
    network.fixture("88769.ydk")
    assert read_ydk(fl.download(winner)) == preview.original
    assert [urlsplit(url).path for url, _ in network.requests] == [
        "/api/events",
        "/api/events/PPAY25",
        "/api/decks/88769",
        "/images/decks/thumbnails/88769.png",
        "/api/decks/download/88769",
    ]
    assert FormatLibrary(tmp_path).download(winner) == (FIXTURES / "88769.ydk").read_bytes()


def test_catalogue_week_and_event_validator(tmp_path, network):
    fl = FormatLibrary(tmp_path)
    network.fixture("formats.json")
    assert "Goat" in fl.formats()
    network.fixture("hat-events.json")
    original = fl.events("HAT")
    network.now += EVENT_TTL - 1
    assert fl.events("HAT") == original
    network.now += 1
    network.responses.append((304, {}, b""))
    assert fl.events("HAT") == original
    assert network.requests[-1][1] == '"hat-events.json"'
    assert "Goat" in fl.formats()
    network.now += WEEK - EVENT_TTL
    network.responses.append((304, {}, b""))
    assert "Goat" in fl.formats()
    assert network.requests[-1][1] == '"formats.json"'


def test_pagination_filter_escaping_and_withdrawn_rows(tmp_path, network):
    fl = FormatLibrary(tmp_path)
    rows = json.loads((FIXTURES / "hat-events.json").read_text())
    rows[0]["display"] = False
    rows[1] = None
    assert len(parse_events(rows).events) == 10 and parse_events(rows).has_next
    network.responses.append((200, {}, b"[]"))
    assert not fl.events("Wind-Up", 2).has_next
    assert parse_qs(urlsplit(network.requests[0][0]).query)["page"] == ["2"]
    assert "format%3Aeq%3AWind-Up" in network.requests[0][0]
    with pytest.raises(DeckError):
        fl.events("HAT,format:eq:Edison")
    decks = json.loads((FIXTURES / "PPAY25.json").read_text())
    rows = decks["topDecks"]
    rows[0]["display"] = False
    rows.append(rows[1])
    assert [d.place for d in parse_standings(decks)] == [2, 3, 4]
    assert parse_standings({"topDecks": None}) == ()


@pytest.mark.parametrize("style", ["seconds", "http-date", "missing"])
def test_backoff_persists_and_serves_offline_events(tmp_path, network, style):
    fl = FormatLibrary(tmp_path)
    network.fixture("hat-events.json")
    original = fl.events("HAT")
    network.now += EVENT_TTL
    headers = {"Retry-After": "90"} if style == "seconds" else {"Retry-After": formatdate(network.now + 90, usegmt=True)}
    if style == "missing":
        headers = {}
    duration = 60 if style == "missing" else 90
    network.responses.append((429, headers, b""))
    page = fl.events("HAT")
    assert page.stale and page.events == original.events
    network.now += duration - 1
    with pytest.raises(http_cache.SourceUnavailable, match="1 s"):
        FormatLibrary(tmp_path).events("Goat")
    assert len(network.requests) == 2
    network.now += 1
    network.responses.append((200, {}, b"[]"))
    assert not fl.events("Goat").stale


def test_offline_and_malformed_responses_keep_last_success(tmp_path, network):
    fl = FormatLibrary(tmp_path)
    network.fixture("hat-events.json")
    first = fl.events("HAT")
    network.now += EVENT_TTL
    network.responses.append((200, {}, b"<html>maintenance</html>"))
    assert fl.events("HAT").events == first.events and fl.stale
    network.responses.append(URLError("offline"))
    assert fl.events("HAT").stale
    with pytest.raises(http_cache.SourceUnavailable):
        fl.events("Cat")
    assert len(network.requests) == 3


@pytest.mark.parametrize("status", [403, 404, 410, "private"])
def test_withdrawal_discards_old_cached_deck(tmp_path, network, status):
    fl = FormatLibrary(tmp_path)
    winner = Deck(88769, "Mermail", "goodmorning", 1)
    network.fixture("88769.json")
    fl.preview(winner)
    network.now += EVENT_TTL
    if status == "private":
        network.responses.append((200, {}, b'{"id": 88769, "display": false}'))
    else:
        network.responses.append((status, {}, b""))
    with pytest.raises(ItemUnavailable):
        fl.preview(winner)
    network.responses.append(URLError("offline"))
    with pytest.raises(http_cache.SourceUnavailable):
        fl.preview(winner)


def test_forgetting_changed_deck_refetches_detail_and_source(tmp_path, network):
    fl = FormatLibrary(tmp_path)
    deck = Deck(88769, "Mermail", "goodmorning", 1)
    for _ in range(2):
        network.fixture("88769.json")
        fl.preview(deck)
        network.fixture("88769.ydk")
        fl.download(deck)
        fl.forget_deck(deck)
    assert len(network.requests) == 4


def test_partial_ydk_download_rejected(monkeypatch, tmp_path):
    class ShortResponse:
        headers = {"Content-Length": "647"}
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, _limit):
            return b"#main\n37781520\n"

    monkeypatch.setattr(http_cache, "urlopen", lambda *args, **kwargs: ShortResponse())
    with pytest.raises(http_cache.SourceUnavailable, match="interrupted"):
        FormatLibrary(tmp_path).download(Deck(88769, "Mermail", "goodmorning", 1))
