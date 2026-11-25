"""Plan, stage and publish artwork for a single selected installation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import CancelledError
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import NamedTuple

from edopal.core import Core
from edopal.core.install import Install, RunState
from edopal.core.jobs import JobContext
from edopal.core.storage import GameRunningError

from . import library
from .artwork import (
    CONNECTIONS_PER_HOST,
    ArtworkClient,
    FetchError,
    ImageAsset,
    candidates,
    field_donors,
    is_historical,
    load_pins,
)
from .catalog import Catalog, deck_ids
from .coverage import Asset, ChangedArtworkError, Coverage, coverage
from .sources import MAPPED, RUSH_BASE, RUSH_ORR


@dataclass
class Result:
    downloaded: int = 0
    unavailable: int = 0
    errors: int = 0
    shadowed: int = 0
    cancelled: bool = False


class Record(NamedTuple):
    digest: str
    policy: str
    source: str
    printing: int
    width: int
    height: int
    fallback: bool
    recheck_after: float = 0


def _open_index(core, install):
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(install.root.resolve())))).hexdigest()
    folder = core.paths.data / "hdcards"
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / f"{key}.sqlite3")
    db.execute(
        "CREATE TABLE IF NOT EXISTS art (id INTEGER, kind TEXT, digest TEXT, policy TEXT, source TEXT, "
        "printing INTEGER, width INTEGER, height INTEGER, fallback INTEGER, recheck_after REAL NOT NULL DEFAULT 0, "
        "PRIMARY KEY(id,kind,digest))"
    )

    # Serialize migration with other processes opening this installation's index.
    def needs_migration():
        return "recheck_after" not in {row[1] for row in db.execute("PRAGMA table_info(art)")}

    if needs_migration():
        with db:
            db.execute("BEGIN IMMEDIATE")
            if needs_migration():
                db.execute("ALTER TABLE art ADD COLUMN recheck_after REAL NOT NULL DEFAULT 0")
    library.create_tables(db)
    return db


def _stamp(path):
    try:
        info = path.lstat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    except FileNotFoundError:
        return None


def _visible(art, kind, cid):
    return (art.faces if kind == "face" else art.fields).get(cid)


@dataclass(frozen=True)
class Item:
    kind: str
    cid: int
    policy: str
    current: Asset | None
    prior: Record | None


def _digest_ids(catalog, previous, ids, pins, rush=False):
    """Which existing HD faces need a content match to decide their printing?"""
    required = set()
    for (cid, kind), records in previous.items():
        if cid not in ids or kind != "face":
            continue
        card = catalog.cards[cid]
        if cid in pins or is_historical(card.name):
            required.add((cid, kind))
            continue
        # An unrecognized HD face is user artwork. When every owned digest also
        # has a settled ordinary-print policy, all possibilities lead to a skip.
        policy = repr((False, candidates(card, catalog, False, pins, rush)))
        if any(record.policy != policy or record.fallback for record in records.values()):
            required.add((cid, kind))
    return required


def plan(catalog, art, ids, previous, original, pins):
    """Plan from a complete snapshot and provenance records keyed by (id, kind, digest)."""
    ids = set(ids)
    records = defaultdict(dict)
    for (cid, kind, digest), record in previous.items():
        records[cid, kind][digest] = record
    return _plan_batch(
        catalog, art, ids, records, original, pins, field_donors(catalog), _digest_ids(catalog, records, ids, pins)
    )


def _plan_batch(catalog, art, ids, previous, original, pins, donors, digest_ids, rush=False):
    work, shadowed = [], 0
    now = time.time()
    for cid in sorted(ids):
        card = catalog.cards[cid]
        items = []
        for kind in ("face", "field") if cid in catalog.fields else ("face",):
            if (kind, cid) in art.blocked:
                shadowed += 1
                continue
            current = _visible(art, kind, cid)
            known = previous.get((cid, kind))
            if kind == "face":
                historical = is_historical(card.name)
                ordinary_hd = current and current.is_hd and cid not in pins and not historical
                if ordinary_hd and (cid, kind) not in digest_ids:
                    continue
                policy = repr((original and historical, candidates(card, catalog, original, pins, rush)))
            else:
                donor = donors.get(cid)
                local_donor = art.fields.get(donor)
                policy = repr((donor, local_donor.digest if local_donor else None, card.type, card.rush))
            prior = known.get(current.digest) if known and current else None
            if kind == "face" and current and current.is_hd:
                if cid in MAPPED and not prior:
                    continue  # A reviewed donor can fill a gap without replacing a local HD override.
                if prior and prior.source.startswith((RUSH_BASE, RUSH_ORR)):
                    continue  # Disabling the source leaves installed community HD intact.
            if kind == "face" and ordinary_hd and not prior:
                continue
            if prior and prior.policy == policy and (not prior.fallback or prior.recheck_after > now):
                continue
            items.append(Item(kind, cid, policy, current, prior))
        if items:
            work.append((card, items))
    return work, shadowed


@dataclass
class Pending:
    file: Path
    target: Path
    png: Path
    stamps: tuple
    original: Asset | None


def _stage(item: Item, incoming: ImageAsset, folder: Path, game: Install) -> tuple[Record, Pending | None]:
    current, prior = item.current, item.prior
    digest = hashlib.sha256(incoming.data).hexdigest()
    record = Record(
        digest,
        item.policy,
        incoming.source,
        incoming.printing,
        incoming.width,
        incoming.height,
        incoming.fallback,
        incoming.recheck_after,
    )
    if current:
        if current.digest == digest:
            return record, None
        owned = prior and prior.digest == current.digest and prior.source != "custom"
        derived = item.kind == "field" and (
            (owned and prior.source == "derived") or b"EDOPro field source\0derived" in current.read_bytes()[:128]
        )
        preserve = not owned and not derived and current.width * current.height >= incoming.width * incoming.height
        if preserve or (incoming.source == "derived" and not derived):
            if owned:
                return prior._replace(policy=item.policy, fallback=True), None
            return record._replace(
                digest=current.digest, source="custom", printing=item.cid, width=current.width, height=current.height
            ), None
    base = Path("pics/field" if item.kind == "field" else "pics") / str(item.cid)
    target = base.with_suffix("." + incoming.extension)
    png = base.with_suffix(".png")
    scratch = folder / f"{item.kind}-{target.name}"
    scratch.write_bytes(incoming.data)
    return record, Pending(scratch, target, png, tuple(_stamp(game.root / p) for p in (target, png)), current)


def _publish(core, game, catalog, staged, records, result, confirmed_closed):
    if not staged:
        return []
    if not catalog.stable():
        raise RuntimeError("The card databases changed during sync. Check the game folder and sync again.")
    ids = {cid for cid, _ in staged}
    scope = replace(catalog, cards={cid: catalog.cards[cid] for cid in ids}, fields=catalog.fields & ids)
    latest = coverage(game, scope, cache=core.paths.cache)
    if not latest.complete:
        raise OSError("Could not verify installed artwork before saving.")
    for (cid, kind), pending in list(staged.items()):
        stamps = tuple(_stamp(game.root / path) for path in (pending.target, pending.png))
        if (kind, cid) in latest.blocked:
            result.shadowed += 1
        elif pending.stamps != stamps or _visible(latest, kind, cid) != pending.original:
            result.errors += 1
        else:
            continue
        del staged[cid, kind], records[cid, kind]
    if not staged:
        return []
    if core.installs.current() != game:
        result.cancelled = True
        records.clear()
        return []
    state = core.installs.run_state()
    if state == RunState.RUNNING or (state == RunState.UNKNOWN and not confirmed_closed):
        raise GameRunningError("Close EDOPro before saving artwork.")
    pairs = library.prepare_pairs(staged, records)
    with core.storage.changeset(game, "hdcards", "Sync HD cards", confirmed_closed=confirmed_closed) as changes:
        for pending in staged.values():
            changes.copy_in(pending.file, pending.target.as_posix())
            if pending.target != pending.png and pending.stamps[1] is not None:
                changes.remove(pending.png.as_posix())  # Otherwise this root PNG conceals our new JPEG.
    result.downloaded = len(staged)
    return pairs


async def sync(
    core: Core,
    game: Install,
    catalog: Catalog,
    art: Coverage | None,
    ctx: JobContext,
    *,
    original=False,
    deck_only=False,
    only_ids=None,
    confirmed_closed=False,
    rush=False,
    only_failed=None,
    run_type=None,
    verify_faces=False,
) -> Result:
    """Sync a supplied coverage snapshot, or pass art=None to scan alongside downloads.

    Stage outside the game until stability checks finish. Completed eligible files
    share one undoable changeset even when the user cancels.
    """
    result = Result()
    ids = set(catalog.cards) if only_ids is None else catalog.cards.keys() & only_ids
    if deck_only:
        ids &= deck_ids(game)
    if only_failed is not None:
        ids &= {cid for cid, kind in only_failed}
    pins = load_pins(core.paths.config / "manual_map.json")
    core.paths.cache.mkdir(parents=True, exist_ok=True)
    with closing(_open_index(core, game)) as db, TemporaryDirectory(prefix="hdcards-", dir=core.paths.cache) as tmp:
        previous = defaultdict(dict)
        for cid, kind, digest, *values in db.execute("SELECT * FROM art"):
            previous[cid, kind][digest] = Record(digest, *values)

        donors = field_donors(catalog)
        digest_ids = _digest_ids(catalog, previous, ids, pins, rush)
        records, staged = {}, {}
        queue = asyncio.Queue()
        ready = asyncio.Event()
        total = completed = 0
        started_utc = time.time()
        started = time.monotonic()
        retry = []
        finished = set()
        exhausted = set()
        last_progress = started
        stop = Event()

        def cancelled():
            return ctx.cancelled or stop.is_set()

        def enqueue(batch, work, shadowed):
            nonlocal total
            if cancelled():
                return
            result.shadowed += shadowed
            previous_total = total
            for card, items in work:
                if only_failed is not None:
                    items = [item for item in items if (card.id, item.kind) in only_failed]
                if items:
                    queue.put_nowait((card, items, batch))
                    total += 1
            if total > previous_total:
                ready.set()

        async def discover():
            try:
                if art is not None:
                    if art.complete:
                        enqueue(art, *_plan_batch(catalog, art, ids, previous, original, pins, donors, digest_ids, rush))
                    return art.complete
                loop = asyncio.get_running_loop()

                def report(batch, resolved):
                    work, shadowed = _plan_batch(
                        catalog, batch, ids.intersection(resolved), previous, original, pins, donors, digest_ids, rush
                    )
                    loop.call_soon_threadsafe(enqueue, batch, work, shadowed)

                scope = catalog
                if ids != catalog.cards.keys():
                    fields = catalog.fields & ids
                    scope = replace(
                        catalog,
                        cards={cid: catalog.cards[cid] for cid in ids},
                        fields=fields | {donors[cid] for cid in fields if cid in donors},
                    )

                def scan():
                    try:
                        return coverage(
                            game,
                            scope,
                            cache=core.paths.cache,
                            cancelled=cancelled,
                            on_batch=report,
                            digest_ids=digest_ids,
                            verify_faces=verify_faces,
                        ).complete
                    except CancelledError:
                        if not cancelled():
                            raise
                        # Finished items still undergo the independent pre-publication scan.
                        return True

                scanner = asyncio.create_task(asyncio.to_thread(scan))
                try:
                    valid = await asyncio.shield(scanner)
                finally:
                    if not scanner.done():
                        stop.set()
                        await scanner
                if not valid:
                    stop.set()
                return valid
            finally:
                queue.put_nowait(None)
                ready.set()

        def local(batch, cid, kind):
            key = (cid, kind)
            if key in staged:
                return None if records[key].source == "derived" else staged[key].file.read_bytes()
            asset = _visible(batch, kind, cid)
            known = previous.get(key)
            record = known.get(asset.digest) if known and asset else None
            if asset and not (record and record.source == "derived"):
                return asset.read_bytes()
            return None

        async def worker(client):
            nonlocal completed, last_progress
            while not cancelled():
                job = await queue.get()
                if job is None:
                    queue.put_nowait(None)
                    return
                card, items, batch = job
                for item in items:
                    if cancelled():
                        return
                    key = card.id, item.kind
                    try:
                        if item.kind == "face":
                            incoming = await client.face(card, catalog, original)
                        else:
                            incoming = await client.field(
                                card,
                                catalog,
                                local_donor=lambda cid, b=batch: local(b, cid, "field"),
                                face=lambda b=batch, cid=card.id: local(b, cid, "face"),
                            )
                        finished.add(key)
                        if incoming is None:
                            result.unavailable += 1
                            continue
                        records[key], pending = _stage(item, incoming, Path(tmp), game)
                        if pending:
                            staged[key] = pending
                    except FetchError:
                        retry.append((card, [item], batch))
                    except ChangedArtworkError:
                        result.errors += 1
                    except InterruptedError:
                        if not cancelled():
                            raise
                        return
                completed += 1
                now = time.monotonic()
                if completed == 1 or completed == total or now - last_progress >= 0.1:
                    # A warm 404 cache can resolve thousands of cards per second.
                    last_progress = now
                    rate = completed / max(0.01, now - started)
                    ctx.progress(completed, total, f"{rate:,.1f} cards/s")

        async with asyncio.TaskGroup() as tasks:
            scan = tasks.create_task(discover())
            await ready.wait()
            if total and not cancelled():
                ctx.progress(0, total, "Contacting artwork sources…")
                async with ArtworkClient(
                    core.paths.cache, game, cancelled=cancelled, pins=pins, rush=rush, attempts=1
                ) as client:
                    async with asyncio.TaskGroup() as workers:
                        for _ in range(CONNECTIONS_PER_HOST + 4):
                            workers.create_task(worker(client))
                    # Only transient download failures re-enter the queue. All-404 results keep their cooldown.
                    for attempt, delay in enumerate((1, 2), 1):
                        if not retry or cancelled() or not await scan:
                            break
                        ctx.progress(0, len(retry), f"Retrying failed downloads · pass {attempt} of 2")
                        for _ in range(delay * 10):
                            if cancelled():
                                break
                            await asyncio.sleep(0.1)
                        if cancelled():
                            break
                        batch_retry, retry = retry, []
                        queue = asyncio.Queue()
                        for job in batch_retry:
                            queue.put_nowait(job)
                        queue.put_nowait(None)
                        completed, total = 0, len(batch_retry)
                        async with asyncio.TaskGroup() as workers:
                            for _ in range(min(total, CONNECTIONS_PER_HOST + 4)):
                                workers.create_task(worker(client))
                        if attempt == 2 and not cancelled():
                            exhausted = {(card.id, items[0].kind) for card, items, _ in retry}
                    result.errors += len(exhausted)

        if not (scan.result() and catalog.stable()):
            raise ValueError("Could not read all of the selected game’s card databases and artwork.")
        result.cancelled = ctx.cancelled
        if core.installs.current() != game:
            result.cancelled = True
            return result
        pairs = _publish(core, game, catalog, staged, records, result, confirmed_closed)
        kind = run_type or ("Deck-only" if deck_only else "Full")
        outcome = (
            "Cancelled"
            if result.cancelled
            else "Failed"
            if result.errors
            else (f"{result.unavailable:,} unavailable" if result.unavailable else "Complete")
        )
        cards = len({cid for cid, kind in staged if kind == "face"}) if result.downloaded else 0
        with db:
            if not result.cancelled:
                finished.update(
                    (cid, k)
                    for cid, k in db.execute("SELECT * FROM failed")
                    if cid in ids and (only_failed is None or (cid, k) in only_failed)
                )
            db.executemany("DELETE FROM failed WHERE id=? AND kind=?", finished)
            db.executemany("INSERT OR IGNORE INTO failed VALUES (?,?)", exhausted)
            if pairs:
                db.executemany("INSERT OR REPLACE INTO pairs VALUES(?,?,?,?)", pairs)
            library.record_run(
                db, library.Run(started_utc, kind, cards, result.errors, round(time.monotonic() - started), outcome)
            )
            db.executemany(
                "INSERT OR REPLACE INTO art VALUES (?,?,?,?,?,?,?,?,?,?)",
                ((cid, kind, *record) for (cid, kind), record in records.items()),
            )
    return result
