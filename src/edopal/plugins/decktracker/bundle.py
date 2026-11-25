"""Build the local first-party gallery package from this checkout for the packs publisher."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PACK_ID = "plugin-deck-tracker"
VERSION = "1.1.0"


def build(destination: Path):
    """Create an isolated local catalog. Refuse to replace an existing catalog or archive."""
    root = Path(__file__).resolve().parent
    payload = {f"decktracker/{p.name}": p.read_bytes() for p in root.glob("*.py") if p.name != "bundle.py"}
    payload.update({f"decktracker/{p.name}": p.read_bytes() for p in root.glob("*.webp")})
    payload["LICENSE"] = (root.parents[3] / "LICENSE").read_bytes()
    identity = dict(id=PACK_ID, name="Deck Tracker", kind="plugin", version=VERSION)
    manifest = dict(schema=1, **identity, files={name: hashlib.sha256(data).hexdigest() for name, data in payload.items()})
    payload["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()

    destination.mkdir(parents=True, exist_ok=True)
    catalog = destination / "catalog.json"
    archive = destination / f"{PACK_ID}-{VERSION}.zip"
    if catalog.exists():
        raise FileExistsError(catalog)
    with ZipFile(archive, "x") as package:
        for name, data in sorted(payload.items()):
            package.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
    binary = archive.read_bytes()
    entry = dict(**identity, url=archive.name, size=len(binary), sha256=hashlib.sha256(binary).hexdigest())
    with catalog.open("x", encoding="utf-8") as file:
        json.dump(dict(schema=1, packs=[entry]), file, indent=2)
        file.write("\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory for the ZIP and its local catalog")
    print(build(parser.parse_args().output))
