"""Read-only CLI. Supplying a config path selects its parent for Studio inventory."""

import argparse
from pathlib import Path

from . import DEFAULTS, Installation, inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True, metavar="INSTALLATION")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    for cat, total in inventory(Installation.at(args.inventory, args.config)).items():
        default = "on" if cat in DEFAULTS else "off"
        print(f"{cat:<12} {total.count:>5} entries  {total.bytes:>14,} bytes  default {default}")


if __name__ == "__main__":
    main()
