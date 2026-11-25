import asyncio
import csv
from collections import defaultdict
from email.utils import formatdate
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from edopal.core.install import Install
from edopal.features.hdcards import artwork as art


def card(cid, name="", *, alias=0, kind=0x80002, rush=False):
    return SimpleNamespace(id=cid, name=name, alias=alias, type=kind, rush=rush)


def catalog(*cards):
    rows = {c.id: c for c in cards}
    official = defaultdict(list)
    for cid, c in sorted(rows.items()):
        if cid < 100_000_000:
            official[c.name].append(cid)
    return SimpleNamespace(cards=rows, official=official, fields=set(rows))


def picture(fmt="JPEG", size=(100, 150)):
    buf = BytesIO()
    Image.new("RGB", size, "navy").save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("suffix", [" (goat)", " GOAT", " (pRe-ErRaTA)", " pre-errata"])
def test_historical_order_and_pins(suffix, tmp_path):
    era, first, second = card(511000824, "Ring" + suffix), card(83555666, "Ring"), card(83555667, "Ring")
    data = catalog(era, first, second)
    assert art.is_historical(era.name)
    assert [cid for cid, _ in art.candidates(era, data)] == [second.id, first.id, era.id, era.id]
    assert [cid for cid, _ in art.candidates(era, data, True)] == [era.id, era.id, second.id, first.id]
    path = tmp_path / "manual_map.json"
    path.write_text('{"511000824":"123","_notes":"pins","22":"../bad.jpg"}')
    pins = art.load_pins(path)
    assert pins[era.id] == 123 and 22 not in pins
    assert art.candidates(era, data, False, pins)[0][0] == 123
    assert [p for p, _ in art.candidates(first, data)] == [first.id, first.id]


def test_unconfirmed_offset_and_red_eyes_wording():
    red = card(88264988, "Red-Eyes (pre-errata)")
    assert art.candidates(red, catalog(red), True)[0] == (red.id, f"{art.IGNIS}/{red.id}.jpg")
    assert [p for p, _ in art.candidates(red, catalog(red))] == [red.id, red.id]
    red.alias = red.id - 10
    assert art.candidates(red, catalog(red))[0][0] == red.alias
    assert not any(art.is_historical(name) for name in ("Goat Token", "Scapegoat", "Errata (GOAT) Other"))


def test_reviewed_field_prints():
    path = Path(__file__).parents[1] / "fixtures/hdcards/field_art_cases.csv"
    with path.open(encoding="utf-8", newline="") as f:
        cases = list(csv.DictReader(f))
    cards = [card(int(r["field_id"]), r["name"], alias=int(r["alias"])) for r in cases]
    for r in cases:
        if r["donor_id"]:
            alias = int(r["field_id"]) if r["donor_name"] == r["name"] + " (Anime)" else 0
            cards.append(card(int(r["donor_id"]), r["donor_name"], alias=alias))
        elif r["alias"] != "0":
            cards.append(card(int(r["alias"]), r["name"].split(" (")[0]))
    donors = art.field_donors(catalog(*reversed(cards)))
    for case in cases:
        expected = int(case["donor_id"]) if case["donor_id"] else None
        assert donors.get(int(case["field_id"])) == expected, case["name"]


class Response:
    def __init__(self, status=200, body=b"", *, length=None, retry=None):
        self.status, self.body = status, body
        self.content_length = length
        self.headers = {"Retry-After": retry} if retry is not None else {}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def iter_chunked(self, _):
        mid = len(self.body) // 2
        yield self.body[:mid]
        yield self.body[mid:]


@pytest.fixture
def server(monkeypatch):
    responses = defaultdict(list)
    calls = []
    now = [1_700_000_000.0]

    def get(_session, url, *, allow_redirects):
        assert not allow_redirects
        calls.append((url, now[0]))
        return responses[url].pop(0) if responses[url] else Response(404)

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr(art.aiohttp.ClientSession, "get", get)
    monkeypatch.setattr(art, "time", SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0]))
    monkeypatch.setattr(art.asyncio, "sleep", sleep)
    return SimpleNamespace(responses=responses, calls=calls, now=now)


def test_duplicate_urls_and_host_pacing(tmp_path, server):
    async def run():
        c1, c2 = card(511000824, "Ring (goat)"), card(83555667, "Ring")
        db = catalog(c1, c2)
        url = art.candidates(c1, db)[0][1]
        server.responses[url] = [Response(429, retry=formatdate(server.now[0] + 3, usegmt=True)), Response(200, picture())]
        async with art.ArtworkClient(tmp_path, Install(tmp_path / "game")) as client:
            a, b = await asyncio.gather(client.face(c1, db), client.face(c2, db))
            assert a == b and a.printing == c2.id and a.width == 100 and not a.fallback
            await client.face(card(777), db)
        assert server.calls[0][0] == server.calls[1][0] == url
        assert server.calls[1][1] - server.calls[0][1] >= 3
        assert server.calls[2][1] - server.calls[1][1] >= 1 / 15 - 1e-6
        assert server.calls[3][1] == server.calls[2][1]  # separate host

    asyncio.run(run())


def test_slow_transfers_overlap_up_to_host_limit_without_bunching_starts(tmp_path, server, monkeypatch):
    monkeypatch.setattr(art, "CONNECTIONS_PER_HOST", 3)
    cards = [card(i) for i in range(1, 9)]
    data = catalog(*cards)

    async def run():
        active = peak = 0
        full, release = asyncio.Event(), asyncio.Event()

        class Held(Response):
            async def __aenter__(self):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == 3:
                    full.set()
                await release.wait()
                return self

            async def __aexit__(self, *_):
                nonlocal active
                active -= 1

        for c in cards:
            url = art.candidates(c, data)[0][1]
            server.responses[url] = [Held(200, picture())]
        async with art.ArtworkClient(tmp_path, Install(tmp_path / "game")) as client:
            pending = asyncio.gather(*(client.face(c, data) for c in cards + [cards[0]]))
            try:
                await asyncio.wait_for(full.wait(), timeout=2)
                assert len(server.calls) == 3
                # All slots remain occupied past the remaining admission times.
                # When these bodies finish, queued requests must still start apart.
                server.now[0] += 2
            finally:
                release.set()
            results = await pending
            assert results[0] == results[-1]
        assert peak == 3
        assert len(server.calls) == len(cards)
        starts = [t for _, t in server.calls]
        assert all(b - a >= 1 / 15 - 1e-6 for a, b in pairwise(starts))

    asyncio.run(run())


def test_success_is_reused_on_restart_for_another_install_and_donor_print(tmp_path, server):
    printed, variant = card(83555667, "Ring"), card(511000824, "Ring (GOAT)")
    data = catalog(printed, variant)
    url = art.candidates(printed, data)[0][1]
    server.responses[url] = [Response(200, picture())]

    async def fetch(game, c):
        async with art.ArtworkClient(tmp_path / "cache", Install(tmp_path / game)) as client:
            return await client.face(c, data)

    first = asyncio.run(fetch("a", printed))
    second = asyncio.run(fetch("b", variant))
    assert first == second and second.printing == printed.id
    assert [request for request, _ in server.calls] == [url]


def test_long_retry_after_stops_queued_starts_while_other_host_proceeds(tmp_path, server, monkeypatch):
    monkeypatch.setattr(art, "CONNECTIONS_PER_HOST", 1)

    async def run():
        opened, release = asyncio.Event(), asyncio.Event()

        class Throttled(Response):
            async def __aenter__(self):
                opened.set()
                await release.wait()
                return self

        first, queued, other = f"{art.CDN}/cards/1.jpg", f"{art.CDN}/cards/2.jpg", f"{art.IGNIS}/3.jpg"
        server.responses[first] = [Throttled(429, retry="90")]
        server.responses[queued] = [Response(200, picture())]
        server.responses[other] = [Response(200, picture())]
        async with art.ArtworkClient(tmp_path, Install(tmp_path / "game")) as client:
            throttle = asyncio.create_task(client._fetch(1, first))
            await asyncio.wait_for(opened.wait(), timeout=2)
            waiting = asyncio.create_task(client._fetch(2, queued))
            try:
                assert (await asyncio.wait_for(client._fetch(3, other), timeout=2)).source == other
            finally:
                release.set()
            for task in (throttle, waiting):
                with pytest.raises(art.FetchError):
                    await task
        assert [url for url, _ in server.calls] == [first, other]

    asyncio.run(run())


@pytest.mark.parametrize("status", [404, 503])
def test_fallback_retry_deadline_depends_on_preferred_source_outcome(tmp_path, server, status):
    c, data = card(123), catalog(card(123))
    primary, fallback = [url for _, url in art.candidates(c, data)]
    server.responses[primary] = [Response(status), Response(status)]
    server.responses[fallback] = [Response(200, picture())]

    async def run():
        async with art.ArtworkClient(tmp_path, Install(tmp_path / "game")) as client:
            return await client.face(c, data)

    result = asyncio.run(run())
    assert result.fallback and result.source == fallback
    assert result.recheck_after == (server.now[0] + art.MISS_TTL if status == 404 else 0)


def test_only_all_404_persists_for_two_weeks_per_install_and_policy(tmp_path, server):
    c = card(733, "Bird (GOAT)")
    db = catalog(c, card(1, "Bird"))

    async def run(game, original=False):
        async with art.ArtworkClient(tmp_path, Install(tmp_path / game)) as client:
            return await client.face(c, db, original)

    asyncio.run(run("a"))
    assert len(server.calls) == 3
    server.now[0] += art.MISS_TTL - 60
    asyncio.run(run("a"))
    assert len(server.calls) == 3
    asyncio.run(run("b"))
    asyncio.run(run("a", True))
    assert len(server.calls) == 9
    server.now[0] += 70
    asyncio.run(run("a"))
    assert len(server.calls) == 12


def test_oversize_corrupt_and_transient_are_not_cached(tmp_path, server, monkeypatch):
    c, db = card(1), catalog(card(1))
    cdn, ignis = [url for _, url in art.candidates(c, db)]

    async def run():
        async with art.ArtworkClient(tmp_path, Install(tmp_path)) as client:
            return await client.face(c, db)

    server.responses[cdn] = [Response(200, length=art.MAX_BYTES + 1)]
    with pytest.raises(art.FetchError):
        asyncio.run(run())
    monkeypatch.setattr(art, "MAX_BYTES", 1024)
    server.responses[cdn] = [Response(200, b"0" * 1025)]
    with pytest.raises(art.FetchError):
        asyncio.run(run())
    server.responses[cdn] = [Response(200, b"<html>Oops</html>"), Response(503, retry="90")]
    with pytest.raises(art.FetchError):
        asyncio.run(run())
    server.responses[ignis] = [Response(200, picture())]
    result = asyncio.run(run())
    assert result.fallback and result.source == ignis
    with pytest.raises((OSError, ValueError)):
        art.image_dimensions(picture()[:-120])
    monkeypatch.setattr(art, "MAX_PIXELS", 500)
    with pytest.raises(ValueError):
        art.image_dimensions(picture())


def test_fields_try_local_published_then_remote_donor_then_crop(tmp_path, server):
    c = card(300102002, "Mythic Depths", kind=0x8080002)
    db = catalog(c)
    own_urls = [f"{art.CDN}/cards_cropped/{c.id}.jpg", f"{art.IGNIS}/field/{c.id}.png"]
    donor_urls = [f"{art.CDN}/cards_cropped/22702055.jpg", f"{art.IGNIS}/field/22702055.png"]

    async def run(client, donor, face):
        return await client.field(c, db, local_donor=lambda cid: donor if cid == 22702055 else None, face=lambda: face)

    async def scenarios():
        async with art.ArtworkClient(tmp_path / "published", Install(tmp_path)) as client:
            local = await run(client, picture("PNG"), None)
            assert local.source == "local:22702055" and local.extension == "png"
            assert [url for url, _ in server.calls] == own_urls
            server.responses[donor_urls[-1]] = [Response(200, picture("PNG"))]
            remote = await run(client, None, None)
            assert remote.source == donor_urls[-1] and remote.fallback
            assert [url for url, _ in server.calls] == own_urls + donor_urls
        async with art.ArtworkClient(tmp_path / "crop", Install(tmp_path)) as client:
            crop = await run(client, None, picture())
            assert (crop.source, crop.width, crop.height, crop.fallback) == ("derived", 74, 78, True)
        async with art.ArtworkClient(tmp_path / "crop", Install(tmp_path)) as client:
            server.calls.clear()
            assert await run(client, crop.data, None) is None
            assert (await run(client, picture(), None)).source == "local:22702055"
            assert not server.calls

    asyncio.run(scenarios())


def test_cancel_never_records_a_miss(tmp_path, server):
    async def run():
        c = card(123)
        async with art.ArtworkClient(tmp_path, Install(tmp_path), cancelled=lambda: True) as client:
            with pytest.raises(InterruptedError):
                await client.face(c, catalog(c))
        assert not server.calls

    asyncio.run(run())


def test_png_checks_idat_crc_and_iend():
    png = picture("PNG")
    tag = png.index(b"IDAT")
    length = int.from_bytes(png[tag - 4 : tag], "big")
    crc = tag + 4 + length
    damaged = bytearray(png)
    damaged[crc] ^= 0x01
    for broken in (bytes(damaged), png[:-1], png[:-12] + b"0" * 12):
        with pytest.raises(ValueError):
            art.image_dimensions(broken)
    assert art.image_dimensions(png) == (100, 150)


def test_retry_after_survives_immediate_restart(tmp_path, server):
    c = card(123)
    db = catalog(c)
    cdn, backup = [url for _, url in art.candidates(c, db)]
    server.responses[cdn] = [Response(429, retry="60"), Response(200, picture())]

    async def run():
        async with art.ArtworkClient(tmp_path, Install(tmp_path)) as client:
            return await client.face(c, db)

    with pytest.raises(art.FetchError):
        asyncio.run(run())
    with pytest.raises(art.FetchError):
        asyncio.run(run())
    assert [u for u, _ in server.calls] == [cdn, backup, backup]
    server.now[0] += 61
    assert asyncio.run(run()).source == cdn


def test_cancel_aborts_stalled_response(tmp_path, monkeypatch):
    calls = []

    class Slow(Response):
        async def __aenter__(self):
            calls.append("opened")
            try:
                await asyncio.Event().wait()
            finally:
                calls.append("closed")

    monkeypatch.setattr(art.aiohttp.ClientSession, "get", lambda *args, **kwargs: Slow())

    async def run():
        c = card(1)
        async with art.ArtworkClient(tmp_path, Install(tmp_path), cancelled=lambda: bool(calls)) as client:
            with pytest.raises(InterruptedError):
                await asyncio.wait_for(client.face(c, catalog(c)), timeout=2)
            assert client.db.execute("SELECT count(*) FROM misses").fetchone() == (0,)

    asyncio.run(run())
    assert calls == ["opened", "closed"]


def test_separate_clients_share_admission_times(tmp_path, server):
    first, second = card(1), card(2)
    data = catalog(first, second)
    for c in (first, second):
        server.responses[art.candidates(c, data)[0][1]] = [Response(200, picture())]

    async def run():
        async with (
            art.ArtworkClient(tmp_path, Install(tmp_path / "one")) as a,
            art.ArtworkClient(tmp_path, Install(tmp_path / "two")) as b,
        ):
            await asyncio.gather(a.face(first, data), b.face(second, data))

    asyncio.run(run())
    assert len(server.calls) == 2
    assert server.calls[1][1] - server.calls[0][1] >= 1 / art.CDN_RATE - 1e-6
