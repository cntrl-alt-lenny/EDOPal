"""Offline, reproducible Health check and Replays gallery."""

import hashlib
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from doctor import inspect_installation, storage_breakdown
from replays import AnnotationStore, ReplayNotes, index_replays
from studio import theme as t
from studio.context import Installation
from studio.shell import StudioWindow
from studio.widgets import Button


def seed_replays(game: Path, profile: Path) -> None:
    folder = game / "replay"
    folder.mkdir(exist_ok=True)
    store = AnnotationStore(profile)
    duels = (
        ("_LastReplay", "Mira", "Rowan", 9, 0x2E800, "", ()),
        (
            "Autumn final",
            "Rowan",
            "Jules",
            12,
            0x7F80D072C,
            "Autumn cup final",
            ("tournament", "final"),
        ),
        ("Side deck practice", "Mira", "Theo", 7, 0x2E800, "Testing the side deck", ("practice",)),
        ("Rush rematch", "Jules", "Mira", 5, 0x807F20200, "Friday rematch", ("rush", "rematch")),
        (
            "Qualifying round",
            "Theo",
            "Rowan",
            8,
            0x620000,
            "Last round of qualifiers",
            ("tournament",),
        ),
        ("First practice", "Mira", "Jules", 6, 0xD2800, "Opening hand practice", ("practice",)),
    )
    for i, (file, first, second, turns, rules, title, tags) in enumerate(duels):
        payload = b"".join(n.encode("utf-16-le").ljust(40, b"\0") for n in (first, second))
        payload += struct.pack("<Q", rules) + struct.pack("<BIB", 40, 1, 1) * turns
        data = (
            struct.pack(
                "<4s5I8s", b"yrpX", 720937, 256, 1794759300 - 86400 * i, len(payload), 0, bytes(8)
            )
            + payload
        )
        (folder / f"{file}.yrpX").write_bytes(data)
        if title:
            note = "Watch turn seven again before the next round." if i == 1 else ""
            store.set(hashlib.sha256(data).hexdigest(), ReplayNotes(title, note, tags))
    (folder / "Interrupted recording.yrp").write_bytes(b"yrp")


@contextmanager
def hold_read(target: str, read: Callable) -> Iterator[None]:
    # Let the gallery capture the loading view even on a fast, empty installation.
    release = Event()

    def delayed(*args: object, **kwargs: object) -> object:
        if not release.wait(5):
            raise TimeoutError("Loading view was not captured")
        return read(*args, **kwargs)

    with patch(target, delayed):
        try:
            yield
        finally:
            release.set()


def render_tools(
    window: StudioWindow,
    selected: Installation,
    capture: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
) -> None:
    def shot(name: str) -> None:
        # A scroll surface can post another layout request when its scrollbar appears.
        QTest.qWait(t.MOTION_MS)
        capture(name)

    ctx = window.ctx
    game = Path(selected.path)
    fresh = game.parent / "Fresh installation"
    fresh.mkdir(exist_ok=True)
    (fresh / "EDOPro.exe").touch()
    config = game / "config"
    config.mkdir(exist_ok=True)
    (game / "EDOPro.exe").touch()
    (config / "configs.json").write_text('{"repos":[]}', encoding="utf-8")
    (config / "system.conf").write_text(
        "skin = Midnight\nenable_music = 0\ntextfont = bundled 12\nnumfont = bundled\n",
        encoding="utf-8",
    )
    (game / "sound").mkdir(exist_ok=True)
    (game / "sound/draw.wav").write_bytes(bytes(24000))
    ctx.set_installation(Installation())
    window.navigate("health")
    page = window.current
    shot("health-no-installation")
    with patch("doctor.report._game_running", return_value=False):
        with hold_read("studio.pages.health.inspect_installation", inspect_installation):
            ctx.set_installation(selected)
            shot("health-loading")
        settle(lambda: page.state == "overview")
        shot("health-overview")
        toggle = next(
            b for b in page.findChildren(Button) if b.accessibleName() == "Active skin is missing"
        )
        toggle.click()
        shot("health-expanded")
        page.support.click()
        shot("health-support")
        page.escape()
        with hold_read("studio.pages.health.storage_breakdown", storage_breakdown):
            page.disk.click()
            shot("health-storage-loading")
        settle(lambda: page.state == "storage")
        shot("health-storage")
        page.escape()
        (config / "system.conf").write_text(
            "skin = none\ntextfont = bundled 12\nnumfont = bundled\n", encoding="utf-8"
        )
        bot = game / "WindBot"
        (bot / "Decks").mkdir(parents=True, exist_ok=True)
        (bot / "WindBot.exe").touch()
        (bot / "ExecutorBase.dll").touch()
        (bot / "bots.json").write_text('[{"deck": "Spellcasters"}]', encoding="utf-8")
        codes = range(1000, 1024)
        (bot / "Decks/AI_Spellcasters.ydk").write_text("#main\n" + "".join(f"{i}\n" for i in codes))
        (game / "lflist.conf").write_text("!Practice cup\n" + "".join(f"{i} 0\n" for i in codes))
        page.rerun.click()
        settle(lambda: page.state == "overview")
        shot("health-passed")
        toggle = next(
            b
            for b in page.scroll.widget().findChildren(Button)
            if b.accessibleName() == "Some practice decks exceed installed card limits"
        )
        toggle.click()
        details = next(
            b
            for b in page.scroll.widget().findChildren(Button)
            if b.text() == "Details · See all 24"
        )
        details.click()
        shot("health-evidence")
        page.escape()
        with patch(
            "studio.pages.health.storage_breakdown",
            side_effect=OSError("The drive is unavailable."),
        ):
            page.disk.click()
            settle(lambda: page.state == "storage_error")
            shot("health-storage-error")
        ctx.set_installation(Installation(str(fresh)))
        settle(lambda: page.state == "overview")
        shot("health-awaiting-data")
    with patch(
        "studio.pages.health.inspect_installation",
        side_effect=OSError("The installation folder could not be opened."),
    ):
        page.rerun.click()
        settle(lambda: page.state == "error")
        shot("health-error")
    window.navigate("replays")
    page = window.current
    settle(lambda: page.state == "empty")
    shot("replays-awaiting-data")
    ctx.set_installation(Installation())
    shot("replays-no-installation")
    with hold_read("studio.pages.replays.index_replays", index_replays):
        ctx.set_installation(selected)
        shot("replays-loading")
    settle(lambda: page.state == "empty")
    shot("replays-empty")
    with patch(
        "studio.pages.replays.index_replays",
        side_effect=PermissionError("The replay folder could not be opened."),
    ):
        page.refresh()
        settle(lambda: page.state == "error")
        shot("replays-error")
    seed_replays(game, Path(ctx.cfg.config_path).parent)
    page.refresh()
    settle(lambda: page.state == "library")
    shot("replays-library")
    page.search.setText("tournament")
    shot("replays-search")
    page.list.activated.emit(page.list.records.index(0))
    shot("replays-detail")
    page.form.name.setText("Autumn cup · final round")
    shot("replays-edit")
    page.form.save.click()
    settle(lambda: page.state == "detail")
    shot("replays-notes-saved")
    window.notice.hide()
    page.escape()
    page.search.setText("unknown player")
    shot("replays-no-matches")
    page.search.setText("interrupted")
    page.list.activated.emit(page.list.records.index(0))
    shot("replays-unreadable")
    page.escape()
    page.search.clear()
    with patch("studio.pages.replays._game_running", return_value=True):
        page.preserve.click()
        settle(lambda: page.state == "write_error")
        shot("replays-close-game")
    with patch("studio.pages.replays._game_running", return_value=False):
        retry = next(b for b in page.scroll.widget().findChildren(Button) if b.text() == "Retry")
        retry.click()
        shot("replays-saving")
        settle(lambda: page.state == "library")
        shot("replays-preserved")
    QApplication.processEvents()
