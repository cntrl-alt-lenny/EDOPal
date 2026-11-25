"""EDOPro 41.0.2 + ocgcore 11.0, little-endian native duel protocol.

Layout references: gframe/{network.h,menu_handler.cpp,generic_duel.cpp,duelclient.cpp}
at edopro tag 41.0.2 and {common.h,processor.cpp,operations.cpp} at core 158aebe.
A separate Session belongs to each human connection. Never retain raw packets in snapshots.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping

from .model import DeckState, LostHistory

CTOS_UPDATE_DECK, CTOS_CREATE_GAME, CTOS_JOIN_GAME = 0x02, 0x11, 0x12
STOC_GAME_MSG, STOC_ERROR_MSG, STOC_CHANGE_SIDE = 0x01, 0x02, 0x07
STOC_JOIN_GAME, STOC_DUEL_START, STOC_DUEL_END, STOC_REMATCH = 0x12, 0x15, 0x16, 0xF1
MSG_START, MSG_WIN, MSG_CONFIRM_DECKTOP, MSG_SHUFFLE_DECK = 4, 5, 30, 32
MSG_SWAP_GRAVE_DECK, MSG_REVERSE_DECK, MSG_DECK_TOP, MSG_MOVE, MSG_SWAP = 35, 37, 38, 50, 55
MSG_DRAW, MSG_TAG_SWAP, MSG_RELOAD_FIELD, MSG_REMOVE_CARDS = 90, 161, 162, 190
DECK, HAND, GRAVE, REMOVED, EXTRA, OVERLAY = 1, 2, 16, 32, 64, 128
VERSION = bytes((41, 0, 11, 0))
HANDSHAKE = 4043399681
LOCATION = struct.Struct("<BBII")

# Known events in core 158aebe that do not change Main Deck composition/order.
# Includes card queries, selection dialogs and unordered confirmations; ignore their contents.
# fmt: off
PASSIVE = frozenset((
    1, 2, 3, 6, 7, 8,                              # hints and UI refreshes
    10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26,  # decisions
    31, 33, 36, 39, 40, 41, 42, 53, 54, 56,        # confirmations, non-deck zones and phases
    60, 61, 62, 63, 64, 65, 70, 71, 72, 73, 74, 75, 76, 80, 81, 83,  # summons and chains
    91, 92, 93, 94, 95, 96, 97, 100, 101, 102, 110, 111, 112, 113, 114,  # stats and combat
    120, 121, 122, 123, 130, 131, 132, 133, 140, 141, 142, 143, 160, 163, 164, 165, 170,
))
# fmt: on


class Framer:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes):
        self.buffer.extend(data)
        start = 0
        while len(self.buffer) - start >= 2:
            length = struct.unpack_from("<H", self.buffer, start)[0]
            if not length:
                start += 2
                continue
            end = start + 2 + length
            if end > len(self.buffer):
                break
            yield self.buffer[start + 2], bytes(self.buffer[start + 3 : end])
            start = end
        del self.buffer[:start]


def _size(data, expected):
    if len(data) != expected:
        raise LostHistory("Unexpected duel packet layout.")


def _visible(loc, seat):
    player, zone, _, pos = loc
    if zone == GRAVE or zone & OVERLAY:
        return True
    if zone in (HAND, 4, 8, EXTRA) and player == seat:
        return True
    return bool(zone in (4, 8, REMOVED, EXTRA) and pos & 5)


class Session:
    def __init__(self, card_types: Mapping[int, int] | None = None):
        self.card_types = card_types or {}
        self.frames = (Framer(), Framer())  # server, client
        self.client_ok = self.room_ok = self.armed = self.failed = False
        self.pending_top: tuple[int, int] | None = None
        self.shuffled = False
        self.submitted = self.original = ()
        self.seat = None
        self.deck: DeckState | None = None
        self.status = "Waiting for the room"
        self.revision = 0

    def _reset(self, reason):
        self.pending_top = None
        self.deck = self.seat = None
        self.status = reason
        self.revision += 1

    def feed(self, from_client: bool, data: bytes):
        if self.failed:
            return
        try:
            for kind, body in self.frames[from_client].feed(data):
                if from_client:
                    self._client(kind, body)
                else:
                    self._server(kind, body)
        except (struct.error, IndexError, LostHistory) as exc:
            self.failed = True
            self._reset(str(exc) if isinstance(exc, LostHistory) else "Incomplete duel packet.")

    def _client(self, kind, body):
        if kind == CTOS_JOIN_GAME:
            self.client_ok = len(body) == 52 and body[48:52] == VERSION
        elif kind == CTOS_CREATE_GAME:
            self.client_ok = len(body) == 348 and body[28:32] == VERSION
        elif kind == CTOS_UPDATE_DECK:
            count, side = struct.unpack_from("<II", body)
            _size(body, 8 + 4 * (count + side))
            self.submitted = struct.unpack_from(f"<{count}I", body, 8)

    def _server(self, kind, body):
        if kind == STOC_JOIN_GAME:
            self.room_ok = (
                self.client_ok
                and len(body) == 68
                and body[28:32] == VERSION
                and struct.unpack_from("<I", body, 24)[0] == HANDSHAKE
                and struct.unpack_from("<ii", body, 32) == (1, 1)
                and not struct.unpack_from("<H", body, 52)[0]  # special rule scripts
                and not body[44] & 0x80  # relay teams
            )
            self.shuffled = self.room_ok and not (body[8] or body[44] & 0x10)
            self.ritual_extra = self.room_ok and bool(body[20] & 8)
            self._reset("Waiting for the duel" if self.room_ok else "Unsupported room or protocol")
        elif kind == STOC_ERROR_MSG and body[:1] in (b"\x02", b"\x03"):
            self.submitted = ()
        elif kind == STOC_DUEL_START:
            self.armed = self.room_ok
            if self.armed:
                self._reset("Waiting for the opening draw")
        elif kind in (STOC_CHANGE_SIDE, STOC_REMATCH, STOC_DUEL_END):
            self.armed = False
            self.submitted = self.original if kind == STOC_REMATCH else ()
            self._reset("Side decking" if kind == STOC_CHANGE_SIDE else "Duel finished")
        elif kind == STOC_GAME_MSG and self.armed:
            msg = body[0]
            if msg == MSG_START:
                self._start(body[1:])
            elif self.deck is not None:
                try:
                    if self._game(msg, body[1:]):
                        self.revision += 1
                except LostHistory as exc:
                    # A fresh seat-specific START after siding can establish a new baseline.
                    self._reset(str(exc))

    def _start(self, body):
        seat, _, _, m0, e0, m1, e1 = struct.unpack("<BII4H", body)
        self.pending_top = None
        self.seat = seat
        if seat not in (0, 1):
            self._reset("Observing · tracking is off")
            return
        main, extra = (m0, e0) if seat == 0 else (m1, e1)
        if not main or main + extra != len(self.submitted):
            self._reset("Submitted deck does not match the starting piles")
            return
        for i, code in enumerate(self.submitted):
            typ = self.card_types.get(code)
            if typ is None:
                if self.ritual_extra:
                    self._reset("Missing card types for this room’s Ritual rules")
                    return
                continue
            in_extra = bool(
                typ & (0x40 | 0x2000 | 0x800000) or typ & 1 and (typ & 0x4000000 or self.ritual_extra and typ & 0x80)
            )
            if in_extra != (i >= main):
                self._reset("Card types disagree with the starting piles")
                return
        if not self.original:
            self.original = self.submitted
        self.deck = DeckState.start(self.submitted[:main], shuffled=self.shuffled)
        self.status = "Live"
        self.revision += 1

    def _consumed(self, count):
        if self.pending_top is None:
            return
        offset, code = self.pending_top
        self.pending_top = (offset - count, code) if offset > count else None
        if offset == count:
            self.deck.reveal(0, code)

    def _game(self, msg, body):
        deck = self.deck
        if msg in PASSIVE:
            return False
        if msg in (MSG_DRAW, MSG_CONFIRM_DECKTOP):
            player, count = struct.unpack_from("<BI", body)
            row = struct.Struct("<II" if msg == MSG_DRAW else "<IBBI")
            _size(body, 5 + count * row.size)
            if player != self.seat:
                return False
            for offset, (code, *_) in enumerate(row.iter_unpack(body[5:])):
                if msg == MSG_DRAW:
                    deck.remove(code, drawn=True)
                else:
                    deck.reveal(offset, code)
            if msg == MSG_DRAW:
                self._consumed(count)
        elif msg == MSG_MOVE:
            _size(body, 28)
            before, after = LOCATION.unpack_from(body, 4), LOCATION.unpack_from(body, 14)
            leaving, entering = before[:2] == (self.seat, DECK), after[:2] == (self.seat, DECK)
            if not (leaving or entering):
                return False
            if leaving and entering:
                # Private sorts encode the original indices. Never reconstruct them.
                self.pending_top = None
                if body[:4] == bytes(4) and after[2] == deck.total - 1:
                    deck.private_top_move()
                else:
                    deck.forget_order()
            else:
                other = after if leaving else before
                code = struct.unpack_from("<I", body)[0] if _visible(other, self.seat) else 0
                if leaving:
                    was_top = before[2] == deck.total - 1
                    deck.remove(code)
                    if was_top:
                        self._consumed(1)
                    else:
                        self.pending_top = None
                else:
                    self.pending_top = None
                    deck.insert(code, after[2])
        elif msg == MSG_DECK_TOP:
            player, offset, code, pos = struct.unpack("<BIII", body)
            if player != self.seat:
                return False
            # A nonzero offset pre-announces the card exposed *after* pending moves.
            # Do not surface that future identity. Explicit multi-card confirms are visible.
            self.pending_top = None
            if not pos & 4:
                return False
            if offset:
                if offset >= deck.total:
                    raise LostHistory("Reveal outside the Main Deck.")
                self.pending_top = (offset, code)
                return False
            deck.reveal(0, code)
        elif msg == MSG_SHUFFLE_DECK:
            (player,) = struct.unpack("<B", body)
            if player != self.seat:
                return False
            self.pending_top = None
            deck.forget_order(shuffled=self.shuffled)
        elif msg == MSG_WIN:
            self._reset("Duel finished")
        elif msg == MSG_SWAP:
            _size(body, 28)
            if all(LOCATION.unpack_from(body, i)[:2] != (self.seat, DECK) for i in (4, 18)):
                return False
            self._reset("Tracking paused after a deck swap")
        elif msg == MSG_REMOVE_CARDS:
            (count,) = struct.unpack_from("<I", body)
            _size(body, 4 + count * LOCATION.size)
            if all(loc[:2] != (self.seat, DECK) for loc in LOCATION.iter_unpack(body[4:])):
                return False
            self._reset("Tracking paused after an unidentified removal")
        elif msg == MSG_SWAP_GRAVE_DECK and body and body[0] != self.seat:
            return False
        else:
            self._reset("Tracking paused after an unsupported deck event")
        return True
