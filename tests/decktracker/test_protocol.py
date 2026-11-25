import struct
from collections import Counter

import pytest

from edopal.plugins.decktracker import protocol as p


def frame(kind, body=b""):
    return struct.pack("<HB", 1 + len(body), kind) + body


def join():
    return frame(0x12, struct.pack("<H2xI40s4B", 0x1354, 1234, b"ignored", 41, 0, 11, 0))


def host(*, no_shuffle=False, teams=(1, 1), flags=0, rules=0, handshake=4043399681):
    return frame(
        0x12,
        struct.pack(
            "<IBBBBB3xIBBHIIIiiiIIH6H2x",
            1,
            2,
            0,
            0,
            0,
            no_shuffle,
            8000,
            5,
            1,
            180,
            0,
            handshake,
            0x000B0029,
            *teams,
            3,
            flags,
            0,
            rules,
            40,
            60,
            0,
            15,
            0,
            15,
        ),
    )


def submit(cards=(11, 11, 22, 33), extra=(44,), side=(55,)):
    ids = (*cards, *extra, *side)
    return frame(2, struct.pack(f"<{2 + len(ids)}I", len(cards) + len(extra), len(side), *ids))


def game(msg, body=b""):
    return frame(1, bytes([msg]) + body)


def start(seat=0):
    sizes = (4, 1, 40, 15) if seat == 0 else (40, 15, 4, 1)
    return game(4, struct.pack("<BII4H", seat, 8000, 8000, *sizes))


def draw(player, *codes):
    return game(90, struct.pack("<BI", player, len(codes)) + b"".join(struct.pack("<II", c, 10) for c in codes))


def move(code, before, after):
    return game(50, struct.pack("<IBBIIBBIII", code, *before, *after, 0x40))


def session(seat=0, **room):
    s = p.Session()
    s.feed(True, join())
    s.feed(False, host(**room))
    s.feed(True, submit())
    s.feed(False, frame(0x15) + start(seat))
    return s


@pytest.mark.parametrize("seat", [0, 1])
def test_fragmented_stream_both_seats_and_visible_events(seat):
    s = session(seat)
    assert s.deck.remaining == Counter([11, 11, 22, 33])
    packets = draw(1 - seat, 998877) + draw(seat, 11, 22)
    for byte in packets:
        s.feed(False, bytes([byte]))
    assert s.deck.remaining == {11: 1, 33: 1}
    deck = (seat, 1, 0, 10)
    # Search -> own hand. Return a foreign face-up grave card to the top. Mill it.
    s.feed(False, move(11, deck, (seat, 2, 0, 10)))
    s.feed(False, move(77, (1 - seat, 16, 0, 1), (seat, 1, 1, 10)))
    assert s.deck.known == {0: 77} and s.deck.probability(77) == 1
    s.feed(False, move(77, (seat, 1, 1, 10), (seat, 16, 0, 1)))
    assert s.deck.remaining == {33: 1}


def test_reveals_private_sort_and_shuffle():
    s = session()
    s.feed(False, game(30, struct.pack("<BIIBBIIBBI", 0, 2, 22, 0, 1, 3, 33, 0, 1, 2)))
    assert s.deck.known == {0: 22, 1: 33} and s.deck.total == 4
    s.feed(False, draw(0, 22))
    assert s.deck.known == {0: 33}
    s.feed(False, move(0, (0, 1, 1, 10), (0, 1, 2, 10)))
    assert s.deck.remaining == {11: 2, 33: 1} and s.deck.probability(33) is None
    s.feed(False, game(38, struct.pack("<BIII", 0, 0, 33, 4)))
    assert s.deck.probability(33) == 1
    s.feed(False, game(32, b"\0"))
    assert s.deck.known == {} and s.deck.probability(33) == pytest.approx(1 / 3)


@pytest.mark.parametrize("room", [{"no_shuffle": True}, {"flags": 16}])
def test_pseudo_shuffle_never_implies_random_order(room):
    s = session(**room)
    s.feed(False, game(32, b"\0"))
    assert s.deck.probability(11) is None


def test_hidden_codes_indices_and_queries_do_not_affect_output():
    states = []
    for code, index in [(11, 0), (33, 2)]:
        s = session()
        s.feed(False, game(6, bytes([code, index])) + game(15, bytes([code, index])))
        s.feed(False, game(31, bytes([code, index])) + draw(1, code))
        s.feed(False, move(code, (0, 1, index, 10), (0, 1, 3, 10)))
        states.append(s.deck)
        # A code supplied for a hidden opponent hand is never trusted.
        s.feed(False, move(code, (1, 2, 0, 10), (0, 1, 4, 10)))
        assert s.deck is None
    assert states[0] == states[1]


@pytest.mark.parametrize("room", [{"teams": (2, 1)}, {"rules": 2}, {"handshake": 0}, {"flags": 128}])
def test_unsupported_room(room):
    s = session(**room)
    assert s.deck is None and "Unsupported" in s.status


def test_observer_missing_baseline_rejected_submission_and_siding():
    assert session(0x10).deck is None
    s = session()
    s.feed(False, frame(7) + frame(0x15) + start())
    assert s.deck is None
    s.feed(True, submit((99, 99, 22, 33)))
    s.feed(False, frame(2, b"\x03") + frame(0x15) + start())
    assert s.deck is None
    s.feed(True, submit((77, 77, 22, 33)))
    s.feed(False, frame(0x15) + start(1))
    assert s.deck.remaining[77] == 2
    s.feed(False, frame(0xF1) + frame(0x15) + start())
    assert s.deck.starting == Counter([11, 11, 22, 33])


@pytest.mark.parametrize(
    "event",
    [game(90, struct.pack("<BI", 0, 100)), game(37), game(162), game(251), game(190, struct.pack("<IBBII", 1, 0, 1, 0, 10))],
)
def test_history_loss_clears_stale_counts(event):
    s = session()
    s.feed(False, event + draw(0, 11))
    assert s.deck is None and s.status != "Live"


# Read-only extracts of MSG_START and seat 0's opening MSG_DRAW from completed .yrpX saves.
# The test supplies an artificial submission containing those draws; replays do not supply CTOS traffic.
@pytest.mark.parametrize(
    ("opening", "cards"),
    [
        (
            "00401f0000401f00002800020028000f00",
            "0005000000978d73000a0000008b89a7000a000000860cd8050a000000dad45a020a0000009b680d040a000000",
        ),
        (
            "00401f0000401f000028000f0028000f00",
            "0005000000dad45a020a000000a87242050a0000009b5458030a000000ef2751000a000000d41c151e0a000000",
        ),
    ],
)
def test_completed_replay_opening(opening, cards):
    start_body, draw_body = bytes.fromhex(opening), bytes.fromhex(cards)
    opening_ids = [code for code, _ in struct.iter_unpack("<II", draw_body[5:])]
    extra_size = struct.unpack_from("<H", start_body, 11)[0]
    s = p.Session()
    s.feed(True, join())
    s.feed(False, host())
    s.feed(True, submit([*opening_ids, *range(1, 36)], [100] * extra_size))
    s.feed(False, frame(0x15) + game(4, start_body) + game(90, draw_body))
    assert s.deck.total == 35 and s.deck.remaining == Counter(range(1, 36))


def test_future_faceup_top_waits_for_actual_draw():
    s = session()
    s.feed(False, game(38, struct.pack("<BIII", 0, 2, 33, 4)))
    assert s.deck.known == {}
    s.feed(False, draw(0, 11))
    assert s.deck.known == {}
    s.feed(False, draw(0, 22))
    assert s.deck.known == {0: 33}


def test_framer_matches_client_empty_frame_behavior():
    s = p.Session()
    s.feed(True, b"\0\0" + join())
    s.feed(False, b"\0\0" + host())
    s.feed(True, submit())
    s.feed(False, frame(0x15) + start())
    assert s.deck.total == 4


def test_odds_resume_after_entire_private_prefix_is_drawn():
    s = session()
    for hidden_index in (3, 2, 1):
        s.feed(False, move(0, (0, 1, hidden_index, 10), (0, 1, 3, 10)))
    assert s.deck.probability(33) is None
    s.feed(False, draw(0, 11, 22))
    assert s.deck.probability(33) is None
    s.feed(False, draw(0, 33))
    assert s.deck.random_order and s.deck.remaining == {11: 1}


def test_hidden_departure_pauses_only_current_duel():
    s = session()
    s.feed(False, move(0, (0, 1, 2, 10), (1, 2, 0, 10)))
    assert s.deck is None
    s.feed(False, frame(7))
    s.feed(True, submit((77, 77, 22, 33)))
    s.feed(False, frame(0x15) + start(1))
    assert s.deck.remaining[77] == 2


def test_stock_create_game_and_type_checked_deck_split():
    s = p.Session({11: 1, 22: 2, 33: 4, 44: 0x41})
    # CTOS_CreateGame starts with the same 68-byte HostInfo as STOC_JoinGame.
    s.feed(True, frame(0x11, host()[3:] + bytes(280)))
    s.feed(False, host())
    s.feed(True, submit())
    s.feed(False, frame(0x15) + start())
    assert s.deck.total == 4
    s.feed(False, frame(7))
    s.feed(True, submit((44, 11, 22, 33), (11,)))
    s.feed(False, frame(0x15) + start())
    assert s.deck is None and "types" in s.status


def test_client_version_and_seat_gate():
    s = p.Session()
    s.feed(True, join()[:-4] + bytes([40, 0, 11, 0]))
    s.feed(False, host())
    s.feed(True, submit())
    s.feed(False, frame(0x15) + start() + draw(0, 11))
    assert s.deck is None and "Unsupported" in s.status
