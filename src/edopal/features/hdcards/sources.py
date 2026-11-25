"""Reviewed full-face evidence and immutable Rush pack indexes, 25 November 2026."""

from functools import cache

from .reviewed import CURRENT, MAPPED

DIRECT = CURRENT | MAPPED.keys()

RUSH_BASE = "https://raw.githubusercontent.com/Yoshi80/Rush-HD-Pictures/9762da49934ff423f8414b5d0d3312b1231f270f"
RUSH_ORR = "https://raw.githubusercontent.com/Yoshi80/Rush-HD-ORR-Extension/8fdbe29becbca99157720069db50e689910f8169"
RAW_HOST = "raw.githubusercontent.com"


@cache
def rush_index():
    from .rush_blobs import ORR, RUSH

    # Prefer the standard printing. The extension adds the remaining Over Rush prints.
    return {
        cid: (f"{base}/pics/{cid}.png", sha)
        for tree, base in ((ORR, RUSH_ORR), (RUSH, RUSH_BASE))
        for cid, sha in tree.items()
    }


MIN_WIDTH, MIN_HEIGHT = 400, 580


def is_hd(width, height):
    return width >= MIN_WIDTH and height >= MIN_HEIGHT and width < height
