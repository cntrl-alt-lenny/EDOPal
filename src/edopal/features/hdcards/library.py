"""Per-install sync history, evidence and paired illustration close-ups."""

import json
import time
from dataclasses import astuple, dataclass
from datetime import UTC, datetime
from io import BytesIO

from PIL import Image

from .reviewed import MEASURED_RUSH, SEED_NAMES
from .sources import DIRECT, MIN_HEIGHT, MIN_WIDTH, is_hd


@dataclass(frozen=True)
class Run:
    started: float
    kind: str
    cards: int
    failed: int
    seconds: int
    result: str

    def cells(self):
        date = datetime.fromtimestamp(self.started, UTC)
        mins, secs = divmod(self.seconds, 60)
        return (
            date.strftime("%d %b %Y"),
            date.strftime("%H:%M"),
            self.kind,
            f"{self.cards:,}",
            f"{self.failed:,}",
            f"{mins}m {secs:02}s" if mins else f"{secs}s",
            self.result,
        )


def create_tables(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS runs (started REAL, kind TEXT, cards INTEGER, failed INTEGER, "
        "seconds INTEGER, result TEXT)"
    )
    db.execute("CREATE TABLE IF NOT EXISTS metrics (scope TEXT PRIMARY KEY, summary TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS failed (id INTEGER, kind TEXT, PRIMARY KEY(id,kind))")
    db.execute("CREATE TABLE IF NOT EXISTS witnessed (id INTEGER, name TEXT, PRIMARY KEY(id,name))")
    db.execute("CREATE TABLE IF NOT EXISTS pairs (id INTEGER PRIMARY KEY, digest TEXT, original BLOB, hd BLOB)")


def record_run(db, run):
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", astuple(run))


def closeup(data):
    with Image.open(BytesIO(data)) as im:
        w, h = im.size
        box = (0.30 * w, 0.37 * h - 0.2 * w * 0.77, 0.70 * w, 0.37 * h + 0.2 * w * 0.77)
        crop = im.convert("RGB").resize((240, 185), Image.Resampling.LANCZOS, box)
        out = BytesIO()
        crop.save(out, "JPEG", quality=91)
        return out.getvalue()


def prepare_pairs(staged, records):
    pairs = []
    for (cid, kind), item in staged.items():
        record = records[cid, kind]
        if kind != "face" or not item.original or item.original.is_hd or not is_hd(record.width, record.height):
            continue
        try:
            pairs.append((cid, record.digest, closeup(item.original.read_bytes()), closeup(item.file.read_bytes())))
        except (OSError, ValueError):
            continue
        if len(pairs) == 12:
            break
    return pairs


def inventory(db, catalog, art, scope, options):
    from .artwork import is_historical

    hd = art.hd_faces & scope
    known_from_sync = set()
    downloadable = set()
    incompatible = set()
    for cid, source, printing, digest in db.execute(
        "SELECT id, source, printing, digest FROM art WHERE kind='face' AND width>=? AND height>=? AND width<height",
        (MIN_WIDTH, MIN_HEIGHT),
    ):
        if cid not in scope:
            continue
        if options.original and is_historical(catalog.cards[cid].name) and printing != cid:
            if cid in hd and art.faces[cid].digest == digest:
                incompatible.add(cid)
            continue
        known_from_sync.add(cid)
        if source.startswith("https://") and (options.rush or "raw.githubusercontent.com" not in source):
            downloadable.add(cid)

    hd -= incompatible
    strict = {cid for cid in scope if options.original and is_historical(catalog.cards[cid].name)}
    seen = {
        cid
        for cid, name in db.execute("SELECT * FROM witnessed")
        if cid in scope and cid not in strict and catalog.cards[cid].name == name
    }
    new_witnesses = hd - seen - strict
    if new_witnesses:
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO witnessed VALUES (?,?)", ((cid, catalog.cards[cid].name) for cid in new_witnesses)
            )
    seeded = {cid for cid in scope & SEED_NAMES.keys() if catalog.cards[cid].name == SEED_NAMES[cid]}
    ready = ((DIRECT | (MEASURED_RUSH if options.rush else set())) & seeded | downloadable) - hd
    ready -= {cid for kind, cid in art.blocked if kind == "face"}
    known = seeded | known_from_sync | seen | hd
    groups = [[0, 0] for _ in range(3)]
    for cid in known:
        card = catalog.cards[cid]
        group = groups[1 if card.rush else 2 if card.unofficial else 0]
        group[0] += cid in hd
        group[1] += 1
    return dict(
        hd=len(hd),
        known=len(known),
        pending=len(scope - known),
        ready=len(ready),
        elsewhere=len(known - hd - ready),
        groups=tuple(map(tuple, groups)),
        fields=len(catalog.fields & scope),
        installed_fields=len(art.fields.keys() & scope),
        checked=time.time(),
    )


def last_measurement(db, options, values=None):
    key = repr((options.deck_only, options.rush, options.original))
    if values is not None:
        with db:
            db.execute("INSERT OR REPLACE INTO metrics VALUES (?,?)", (key, json.dumps(values)))
        return values
    row = db.execute("SELECT summary FROM metrics WHERE scope=?", (key,)).fetchone()
    return json.loads(row[0]) if row else {}


def stored_state(db, catalog, art):
    history = tuple(Run(*row) for row in db.execute("SELECT * FROM runs ORDER BY rowid DESC"))
    failed = frozenset((cid, kind) for cid, kind in db.execute("SELECT * FROM failed") if cid in catalog.cards)
    pairs = []
    for cid, digest, original, hd in db.execute("SELECT * FROM pairs ORDER BY rowid DESC"):
        asset = art.faces.get(cid)
        if asset and asset.is_hd and asset.digest == digest:
            pairs.append((catalog.cards[cid].name, original, hd))
            if len(pairs) == 12:
                break
    return dict(history=history, failures=failed, pairs=tuple(pairs))
