"""Read-only music inventory for diagnostics and transfer verification."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from music.audio import FORMATS, _digest
from music.catalogue import LOCAL, SCENES, catalog, library_root
from music.discovery import physical_tracks


def report(installation: Path, root: Path) -> str:
    games = catalog(root=root)
    seen: dict[str, dict[str, set[str]]] = {
        key: {scene: set() for scene in SCENES} for key in games
    }
    unassigned = []
    for scene in SCENES:
        for row in physical_tracks(installation, scene, root, games, digest_file=_digest):
            if row.game not in seen:
                seen[row.game] = {s: set() for s in SCENES}
            seen[row.game][scene].add(row.digest)
            if row.game == LOCAL and row.source != "bundled":
                unassigned.append(str(row.path))
    counts: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for key in sorted(seen):
        counts[tuple(len(seen[key][scene]) for scene in SCENES)].append(key)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in FORMATS:
            parts = path.relative_to(root).parts
            if len(parts) != 3 or parts[0] not in games or parts[1] not in SCENES:
                unassigned.append(str(path))
    lines = [" ".join(SCENES) + " | game IDs"]
    for values, keys in sorted(counts.items()):
        lines.append(" ".join(map(str, values)) + " | " + ", ".join(keys))
    return "\n".join([*lines, "Unassigned files", *sorted(set(unassigned))])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, metavar="INSTALLATION")
    parser.add_argument("--library", type=Path, default=library_root())
    args = parser.parse_args()
    try:
        print(report(args.report, args.library))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
