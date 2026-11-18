"""Read-only console listing for the replay shelf."""

import argparse
import sqlite3
from pathlib import Path

from . import AnnotationStore, index_replays, search_replays


def main() -> None:
    parser = argparse.ArgumentParser(description="List local EDOPro replay files.")
    parser.add_argument("--list", required=True, metavar="INSTALLATION", type=Path)
    parser.add_argument("--config", type=Path, help="Read annotations beside this config file.")
    args = parser.parse_args()
    if not (args.list / "replay").is_dir():
        parser.error("The installation has no replay folder.")
    store = AnnotationStore(args.config.resolve().parent) if args.config else None
    try:
        entries = search_replays(index_replays(args.list, annotations=store))
    except sqlite3.Error:
        parser.error("The Studio replay annotations could not be read.")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for entry in entries:
        date = (
            entry.header.date.isoformat()
            if entry.header and entry.header.date
            else "date unavailable"
        )
        turns = entry.details.turns if entry.details else None
        detail = f"{turns} turns" if turns is not None else "turn count unavailable"
        last = " [last replay]" if entry.is_last else ""
        print(f"{ascii(entry.path.name)}{last} | {date} | {entry.error or detail}")
    print(f"{len(entries)} replay file(s).")


if __name__ == "__main__":
    main()
