"""Shared state for the lazy page, shell badge and game lifecycle hooks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, replace
from inspect import signature

from edopal.core import Core
from edopal.core.install import RunState
from edopal.core.jobs import JobContext
from edopal.core.storage import GameRunningError

from .catalog import check_updates, read_catalog
from .coverage import coverage
from .sync import sync


@dataclass(frozen=True)
class ViewState:
    phase: str = "idle"
    total: int = 0
    hd: int = 0
    fields: int = 0
    new: int = 0
    need: int = 0
    done: int = 0
    count: int = 0
    detail: str = ""
    downloaded: int = 0
    unavailable: int = 0
    errors: int = 0
    shadowed: int = 0
    can_undo: bool = False
    deck_only: bool = False
    original: bool = False
    cancelled: bool = False


def _inspect(core, install, ctx):
    catalog = read_catalog(install, cancelled=lambda: ctx.cancelled)
    art = coverage(install, catalog, cancelled=lambda: ctx.cancelled, cache=core.paths.cache)
    if ctx.cancelled:
        raise CancelledError
    notice = check_updates(core.settings, install, catalog, art)
    values = dict(
        total=len(catalog.cards),
        hd=len(art.hd_faces),
        fields=len(catalog.fields),
        new=len(notice.new),
        need=len(notice.needs),
        can_undo=bool(core.storage.last_receipt(install, "hdcards")),
    )
    if not install.ready and not catalog.cards:
        values.update(detail="Open EDOPro to download its card databases.")
    elif not catalog.stable() or not art.complete:
        values.update(phase="error", detail="The card database scan was incomplete. Check the game folder, then try again.")
    return notice.needs, values


class Controller:
    def __init__(self, core: Core):
        self.core = core
        self.state = ViewState(
            deck_only=bool(core.settings.get("hdcards", "deck_only", False)),
            original=bool(core.settings.get("hdcards", "original", False)),
        )
        self._listeners: list[Callable[[ViewState], None]] = []
        self._install = None
        self._handle = None
        self._generation = 0
        self._notice_ids = set()

    def subscribe(self, listener):
        self._listeners.append(listener)
        listener(self.state)

    def _set(self, **values):
        self.state = replace(self.state, **values)
        for callback in self._listeners:
            callback(self.state)

    def set_options(self, deck_only: bool, original: bool):
        for key, value in (("deck_only", deck_only), ("original", original)):
            if value != getattr(self.state, key):
                self.core.settings.set("hdcards", key, value)
        self._set(deck_only=deck_only, original=original)

    def _run(self, phase, detail, job):
        self._generation += 1
        generation = self._generation
        game = self._install
        self._set(phase=phase, detail=detail, done=0, count=0, cancelled=False)

        def current():
            return generation == self._generation and game == self.core.installs.current()

        def done(output):
            if current():
                needs, values = output
                if needs is not None:
                    self._notice_ids = needs
                self._set(**values)

        def error(exc):
            if current():
                if isinstance(exc, (InterruptedError, CancelledError)):
                    self._set(phase="done", detail="", downloaded=0, unavailable=0, errors=0, shadowed=0, cancelled=True)
                else:
                    self._set(phase="error", detail=str(exc) or "Could not check this game folder.")

        def progress(done_count, total, text):
            if current():
                self._set(done=done_count, count=total, detail=text)

        self._handle = self.core.jobs.run(job, key="hdcards", on_done=done, on_error=error, on_progress=progress)

    def refresh(self):
        core = self.core
        game = core.installs.current()
        if game != self._install:
            self.cancel()
            self._generation += 1
            self._install = game
            self._notice_ids = set()
            self.state = ViewState(deck_only=self.state.deck_only, original=self.state.original)
        elif self.state.phase in ("syncing", "undoing"):
            return  # Both jobs end by inspecting the installation.
        if not game:
            self._set(phase="no-install")
            return

        def check(ctx):
            needs, values = _inspect(core, game, ctx)
            return needs, {"phase": "idle", "detail": "", **values}

        self._run("checking", "Checking card artwork…", check)

    def start_sync(self, new_only=False, confirmed_closed=False):
        game, core, options = self._install, self.core, self.state
        if not game or game != core.installs.current() or options.phase in ("checking", "syncing", "undoing"):
            return
        only_ids = self._notice_ids.copy() if new_only else None

        def download(ctx):
            catalog = read_catalog(game, cancelled=lambda: ctx.cancelled)
            art = coverage(game, catalog, cancelled=lambda: ctx.cancelled, cache=core.paths.cache)
            if ctx.cancelled:
                raise CancelledError
            if not (catalog.stable() and art.complete):
                raise ValueError("Could not read all of the selected game’s card databases.")
            result = asyncio.run(
                sync(
                    core,
                    game,
                    catalog,
                    art,
                    ctx,
                    original=options.original,
                    deck_only=options.deck_only,
                    only_ids=only_ids,
                    confirmed_closed=confirmed_closed,
                )
            )
            # Cancellation may have committed finished files. Measure them without advancing the catalog baseline.
            if ctx.cancelled:
                art = coverage(game, catalog, cache=core.paths.cache)
                return None, dict(
                    phase="done",
                    detail="",
                    hd=len(art.hd_faces),
                    **vars(result),
                    can_undo=bool(core.storage.last_receipt(game, "hdcards")),
                )
            ctx.progress(0, 0, "Checking installed artwork…")
            # The changeset has finished. Keep its result visible if Cancel is clicked during verification.
            needs, values = _inspect(core, game, JobContext())
            return needs, {"phase": "done", "detail": "", **vars(result), **values}

        self._run("syncing", "Checking card artwork…", download)

    def cancel(self):
        if self._handle and self._handle.running:
            self._handle.cancel()

    def undo(self, confirmed_closed=False):
        game, core = self._install, self.core
        if (
            not game
            or game != core.installs.current()
            or not self.state.can_undo
            or self.state.phase in ("syncing", "undoing")
        ):
            return

        def restore(ctx):
            state = core.installs.run_state()
            if state == RunState.RUNNING or (state == RunState.UNKNOWN and not confirmed_closed):
                raise GameRunningError("Close EDOPro before undoing the last sync.")
            undo = core.storage.undo_last
            # Older Storage implementations expose the two-argument undo contract.
            supports_confirmation = confirmed_closed and "confirmed_closed" in signature(undo).parameters
            confirmation = {"confirmed_closed": True} if supports_confirmation else {}
            restored = undo(game, "hdcards", **confirmation)
            needs, values = _inspect(core, game, ctx)
            count = len(restored.restored)
            detail = f"{count:,} {'file' if count == 1 else 'files'} restored"
            if conflicts := len(restored.conflicts):
                detail += f" · {conflicts:,} edited {'file' if conflicts == 1 else 'files'} kept"
            return needs, {"phase": "idle", "detail": detail, **values}

        self._run("undoing", "Restoring previous artwork…", restore)
