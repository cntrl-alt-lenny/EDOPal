"""Render a page (or the whole window) to PNG without a display.

    QT_QPA_PLATFORM=offscreen python -m edopal.dev.preview hdcards --out shots/hdcards.png
    python -m edopal.dev.preview shell --size 1920x1080 --out shots/shell-1080.png

``--game`` defaults to tests/fixtures/game; ``--packs`` to ./packs (local edopal-packs checkout).
``--state NAME`` is passed to a page's optional ``preview_state(name)`` method so features can
show e.g. "syncing", "done", "new-cards" states for screenshots.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def make_core(game: Path | None, packs: Path, home: Path):
    from edopal.core import Core
    from edopal.core.fakes import DirectStorage, LocalPacks, StaticInstalls
    from edopal.core.jobs import InlineJobRunner
    from edopal.core.paths import Paths
    from edopal.core.settings import Settings

    paths = Paths(home / "config", home / "data", home / "cache").ensure()
    return Core(
        paths=paths,
        settings=Settings(paths.config / "settings.json"),
        installs=StaticInstalls(game),
        storage=DirectStorage(paths.data),
        packs=LocalPacks(packs, paths.cache),
        jobs=InlineJobRunner(),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="feature id, or 'shell' for the full window")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--game", default=str(ROOT / "tests/fixtures/game"))
    ap.add_argument("--packs", default=str(ROOT / "packs"))
    ap.add_argument("--state", default=None)
    a = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from edopal import app as edoapp
    from edopal.ui import theme

    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply(qapp)
    w, h = (int(x) for x in a.size.lower().split("x"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_game = Path(tmp) / "game"
        import shutil

        if a.game and Path(a.game).exists():
            shutil.copytree(a.game, tmp_game, symlinks=True)  # never touch the real fixture/game
        core = make_core(tmp_game if a.game else None, Path(a.packs), Path(tmp) / "home")
        if a.target == "shell":
            widget = edoapp.build_window(core, start=a.state)
        else:
            spec = next(s for s in edoapp.load_features(core) if s.id == a.target)
            widget = spec.page(core)
            if a.state and hasattr(widget, "preview_state"):
                widget.preview_state(a.state)
        widget.resize(w, h)
        widget.show()
        for _ in range(5):
            qapp.processEvents()
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(out))
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
