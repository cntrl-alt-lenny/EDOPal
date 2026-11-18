"""Render the Studio gallery against a disposable, offline game installation."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402
from config import APP_NAME, Config  # noqa: E402
from studio import theme  # noqa: E402
from studio.context import AppContext, Installation  # noqa: E402
from studio.shell import StudioWindow  # noqa: E402
from tools.studio_backup_screenshots import capture_backup  # noqa: E402
from tools.studio_decks_screenshots import render_decks  # noqa: E402
from tools.studio_health_replays_shots import render_tools  # noqa: E402
from tools.studio_shell_shots import (  # noqa: E402
    render_artwork_recovery,
    render_picker_states,
    render_settings_states,
)
from tools.studio_skins_screenshots import render_skins  # noqa: E402

PAGE_RENDERERS = {"decks": render_decks}
PAGE_RENDERERS["formats"] = import_module("tools.studio_formats_screenshots").render_formats

PAGE_PROOFS = {"sleeves": "tools.studio_sleeves_shots"}


def create_demo(root: Path) -> Config:
    """Create a small, usable CDB and decks. Refuse to overwrite an existing game."""
    game = root / "Games" / "ProjectIgnis"
    game.mkdir(parents=True)
    (game / "deck").mkdir()
    (game / "deck/Spellcasters.ydk").write_text("#main\n1001\n1002\n1003\n", encoding="utf-8")
    with contextlib.closing(sqlite3.connect(game / "cards.cdb")) as conn, conn:
        conn.execute("CREATE TABLE datas(id INTEGER PRIMARY KEY, type INTEGER)")
        conn.execute("CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO datas VALUES(?,?)",
            ((1000 + n, main.FIELD_SPELL_TYPE if n < 16 else 1) for n in range(128)),
        )
        conn.executemany(
            "INSERT INTO texts VALUES(?,?)", ((1000 + n, f"Studio card {n}") for n in range(128))
        )
    from PIL import Image

    pics = game / "pics"
    (pics / "field").mkdir(parents=True)
    image = Image.new("RGB", (48, 64), (86, 75, 64))
    for card in range(1000, 1096):
        image.save(pics / f"{card}.jpg")
        if card < 1010:
            image.save(pics / "field" / f"{card}.jpg")
    return Config(["--config", str(root / "config.json"), "--edopro-path", str(game), "--gui"])


class DemoSync:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.outcome = "download"

    async def __call__(self, cfg: Config) -> main.DownloadStats | None:
        if cfg.stats:
            return await main.run(cfg)
        if self.outcome == "idle":
            return None
        if self.outcome == "error":
            raise main.SyncInputError(
                "The selected databases contain no readable cards. Open EDOPro to update them, then try again."
            )
        stats = main.DownloadStats()
        task = cfg.gui_progress.add_task("Syncing card artwork", total=125)
        cfg.gui_progress.advance(task, 78)
        cfg.gui_progress.update(task, description="Syncing Dark Magician")
        while not self.release.is_set() and not cfg.cancel_event.is_set():
            await asyncio.sleep(0.01)
        stats.ok_hd = 78
        if not cfg.cancel_event.is_set():
            stats.ok_hd = 121
            stats.field_ok = 4
            stats.skipped = 96
            stats.record_failure(154, "Unreleased printing")
            stats.record_failure(155, "Network retry", transient=True)
            stats.record_failure(156, "Token")
            stats.record_failure(160000000, "Anime card")
            from artwork_flow import record_artwork_change

            # Exercise the real restore record with one small, local artwork file.
            pictures = Path(cfg.pics_path)
            new = pictures / "1100.jpg"
            artwork = (pictures / "1000.jpg").read_bytes()
            record_artwork_change(new, artwork)
            new.write_bytes(artwork)
        if cfg.save_report:
            main._write_report(stats, cfg, 16.2)
        return stats


def settle(predicate: Callable[[], bool], timeout: float = 6) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("Studio did not reach the requested view")
        QApplication.processEvents()
        time.sleep(0.01)


def grab_window(window: StudioWindow) -> QPixmap:
    window.setFocus(Qt.FocusReason.MouseFocusReason)
    window.animation.setCurrentTime(theme.MOTION_MS)
    # Nested layouts can post another resize when their scrollbars appear.
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, theme.MOTION_MS)
    return window.grab()


def render(out: Path, size: tuple[int, int], root: Path, *, page: str | None = None) -> list[Path]:
    app = QApplication.instance() or QApplication([APP_NAME])
    theme.apply(app)
    cfg = create_demo(root)
    fake = DemoSync()
    ctx = AppContext(cfg, main.VERSION, fake, main._apply_gui_choices)
    window = StudioWindow(ctx, startup=False)
    selected = Installation.inspect(cfg.edopro_path)
    ctx.set_installation(selected)
    card = window.pages["cards"]
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []

    def shot(name: str) -> None:
        pixmap = grab_window(window)
        target = out / f"{name}.png"
        if not pixmap.save(str(target), "PNG"):
            raise OSError(f"Could not save {target}")
        files.append(target)

    window.resize(*size)
    window.show()
    window.setFocus(Qt.FocusReason.OtherFocusReason)
    try:
        if page == "sounds":
            from tools.studio_sounds_screenshots import render_sounds

            render_sounds(window, root, selected, shot, settle)
            return files
        if page == "settings":
            render_settings_states(window, shot, settle, root, selected)
            return files
        if page in PAGE_RENDERERS:
            PAGE_RENDERERS[page](window, selected, shot, settle)
            return files
        settle(lambda: card.options.values[0].text() == "75%")
        shot("01-hd-cards")
        card.options.settings.click()
        card.settings.toggles["save_report"].setChecked(True)
        shot("02-sync-settings")
        card.settings.coverage.click()
        settle(lambda: card.state == "results")
        shot("03-coverage")
        card.results.done.click()
        card.options.start.click()
        settle(lambda: card.done == 78)
        shot("04-sync-progress")
        fake.release.set()
        settle(lambda: card.state == "results")
        shot("05-sync-summary")
        card.results.failures.click()
        shot("14-unavailable-artwork")
        card.results.done.click()
        ctx.set_installation(Installation())
        shot("06-no-installation")
        render_picker_states(window, shot, settle, root)
        ctx.set_installation(selected)
        with patch("config.find_edopro_folders", return_value=[cfg.edopro_path]):
            window.open_picker()
            settle(lambda: window.picker.list.count() == 1)
            shot("07-installation-picker")
            window.picker.close_picker()
        if page is None:
            for number, key in enumerate(("skins", "sleeves", "sounds", "music", "decks"), 8):
                if renderer := PAGE_RENDERERS.get(key):
                    renderer(window, selected, shot, settle, overview=f"{number:02}-{key}")
                    continue
                window.navigate(key)
                if key == "skins":
                    render_skins(window, shot, settle)
                if key == "music":
                    settle(lambda: window.current.state == "home")
                if key in PAGE_PROOFS:
                    import_module(PAGE_PROOFS[key]).prepare_overview(window)
                if key == "sounds":
                    settle(lambda: not ctx.tasks.jobs)
                shot(f"{number:02}-{key}")
        window.navigate("cards")
        fake.release.clear()
        card.options.start.click()
        settle(lambda: card.done == 78)
        card.progress.cancel.click()
        settle(lambda: card.state == "results")
        shot("15-sync-cancelled")
        for outcome, filename in (("idle", "16-up-to-date"), ("error", "17-sync-error")):
            card.results.done.click()
            fake.outcome = outcome
            card.options.start.click()
            settle(lambda: card.state == "results")
            shot(filename)
        card.results.done.click()
        ctx.set_installation(Installation(selected.path))
        shot("18-awaiting-databases")
        render_artwork_recovery(window, shot, settle, selected)
        if page is None:
            for i, key in enumerate(("mydecks", "replays", "backup", "health"), 21):
                window.navigate(key)
                settle(lambda: not ctx.tasks.jobs)
                shot(f"{i}-{key}")
        if page != "cards":
            render_settings_states(window, shot, settle, root, selected)
        if page is None:
            render_tools(window, selected, shot, settle)
            capture_backup(window, root, shot, settle)
    finally:
        fake.release.set()
        settle(lambda: not ctx.tasks.jobs)
        window.close()
        app.processEvents()
    return files


def parse_size(raw: str) -> tuple[int, int]:
    try:
        w, h = map(int, raw.lower().split("x"))
        if w < 1 or h < 1:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a positive size such as 1280x800") from exc
    return w, h


# Pages that bring their own synthetic installation render through a module
# exposing render(out, size, root). Pages in PAGE_RENDERERS above draw inside
# the shared demo window instead. Add new pages to one of the two.
STANDALONE_RENDERERS = {
    "mydecks": ("tools.studio_screenshots_mydecks", "render"),
    "music": ("tools.music_screenshots", "render_music"),
    "sleeves": ("tools.studio_sleeves_shots", "render"),
}


def standalone_renderer(page: str) -> Callable[[Path, tuple[int, int], Path], list[Path]]:
    module, function = STANDALONE_RENDERERS[page]
    return getattr(import_module(module), function)


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--size", type=parse_size, action="append", help="WxH, repeatable")
    parser.add_argument(
        "--page",
        choices=sorted(
            {*PAGE_RENDERERS, *STANDALONE_RENDERERS, "sounds", "cards", "settings", "shell"}
        ),
        help="Render the full set of states for one page",
    )
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([APP_NAME])
    app.setQuitOnLastWindowClosed(False)
    for width, height in args.size or [(1280, 800), (1600, 1000)]:
        name = f"{width}x{height}"
        with (
            tempfile.TemporaryDirectory(prefix=".studio-preview-", dir=ROOT) as temporary,
            patch("music.settings._game_running", return_value=False),
        ):
            target, size, root = args.out / name, (width, height), Path(temporary)
            if args.page in STANDALONE_RENDERERS:
                files = standalone_renderer(args.page)(target, size, root)
            else:
                files = render(target, size, root, page=args.page)
        print(f"Rendered {len(files)} views at {name} in {files[0].parent}")


if __name__ == "__main__":
    main_cli()
