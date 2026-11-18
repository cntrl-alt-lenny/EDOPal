"""Synthetic Backup & transfer views for the Studio screenshot gallery."""

import json
import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Event
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

from PySide6.QtWidgets import QFileDialog, QLabel

import backup
from backup.core import Cancel, Progress
from studio.context import Installation
from studio.pages.backup import BackupPage
from studio.pages.backup_screens import ArchiveScreen, CompareScreen, HomeScreen, SaveScreen
from studio.shell import StudioWindow
from studio.widgets import Button


def sample(root: Path, relative: str, size: int, *, fill: bytes = b"x", age: int = 0) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fill * size)
    stamp = 1_794_996_000 - age * 86_400
    os.utime(path, (stamp, stamp))


def sample_backups(game: Path, root: Path) -> tuple[Path, Path]:
    source = root / "Games" / "Home PC"
    for i in range(18):
        sample(game, f"deck/Goat/Practice {i + 1:02}.ydk", 1480, age=i)
    for i in range(7):
        sample(game, f"replay/Weekend Duel {i + 1:02}.yrp", 47_000 + 2000 * i, age=i)
    for i in range(4):
        sample(game, f"screenshots/Side Event {i + 1:02}.png", 810_000, age=i)
    sample(game, "puzzles/Lethal in One.lua", 1800)
    sample(root, "sleeves/Celestial.png", 96_000)
    sample(root, "themes/Evening.json", 840)
    sample(game, "deck/Edison/Blackwing.ydk", 1770, age=12)
    sample(game, "deck/Edison/Gladiator Beast.ydk", 1940, age=10)
    sample(game, "deck/Water Control.ydk", 2050, age=9)
    sample(game, "deck/Dark World.ydk", 1900, age=9)
    sample(source, "deck/Edison/BLACKWING.ydk", 2290, age=3)
    sample(source, "deck/Edison/Gladiator Beast.ydk", 2050, age=4)
    sample(source, "deck/Water Control.ydk", 2280, age=5)
    sample(source, "deck/Dark World.ydk", 1960, age=6)
    sample(source, "deck/Goat/Practice 01.ydk", 1480, age=3)
    sample(source, "deck/Goat/Practice 02.ydk", 1480, age=3)
    sample(source, "deck/Edison/Hero Beat.ydk", 1770, age=3)
    sample(source, "deck/Edison/Frog Monarch.ydk", 1710, age=3)
    sample(source, "replay/Final Table.yrp", 184_000, age=1)
    sample(source, "puzzles/The Last Turn.lua", 4410, age=1)
    (game / "config").mkdir(exist_ok=True)
    (game / "config/system.conf").write_text(
        "nickname = Local Duelist\nmute_opponent = 1\n", encoding="utf-8"
    )
    (source / "config").mkdir(exist_ok=True)
    (source / "config/system.conf").write_text(
        "nickname = Home Duelist\nmute_opponent = 1\n", encoding="utf-8"
    )
    incoming = root / "Home PC backup.zip"
    future = root / "Newer backup.zip"
    backup.create(
        backup.Installation(source, root / "pc-data"),
        incoming,
        (*backup.DEFAULTS, "puzzles", "preferences"),
    )
    with ZipFile(incoming) as old, ZipFile(future, "w", ZIP_STORED) as new:
        for name in old.namelist():
            content = old.read(name)
            if name == "manifest.json":
                doc = json.loads(content)
                doc["format_version"] = 2
                content = json.dumps(doc).encode()
            new.writestr(name, content)
    return incoming, future


def capture_backup(
    window: StudioWindow,
    root: Path,
    shot: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
) -> None:
    ctx = window.ctx
    game = Path(ctx.installation.path)
    incoming, future = sample_backups(game, root)
    saved = root / "Steam Deck travel copy.zip"
    selected = Installation(str(game), True)
    ctx.set_installation(selected)
    page = window.pages["backup"]
    assert isinstance(page, BackupPage)

    def click(title: str) -> None:
        control = next(
            widget
            for widget in page.screen.findChildren(Button)
            if widget.isVisible() and title in {widget.text(), widget.accessibleName()}
        )
        control.click()

    def showing(title: str) -> bool:
        return any(
            label.isVisible() and title in label.text()
            for label in page.screen.findChildren(QLabel)
        )

    def home() -> None:
        settle(lambda: isinstance(page.screen, HomeScreen) and page.job is None)

    path = incoming
    with (
        patch.object(QFileDialog, "getSaveFileName", return_value=(str(saved), "")),
        patch.object(QFileDialog, "getOpenFileName", side_effect=lambda *_: (str(path), "")),
        patch.object(ctx, "confirm", return_value=True),
        patch("studio.pages.backup._game_running", return_value=False) as process,
        patch("backup.restore._game_running", return_value=False),
    ):
        window.navigate("backup")
        home()
        shot("backup-01-home")
        click("Save a backup")
        settle(lambda: isinstance(page.screen, SaveScreen))
        shot("backup-02-save-selection")
        release_save = Event()
        real_create = backup.create

        def slow_save(
            install: backup.Installation,
            target: Path,
            categories: tuple[str, ...],
            *,
            progress: Progress,
            cancel: Cancel,
        ) -> backup.Manifest:
            def sample_progress(done: int, total: int, name: str) -> None:
                assert progress is not None
                if done >= 16:
                    progress(done, total, name)
                if done == 16:
                    release_save.wait(5)

            return real_create(install, target, categories, progress=sample_progress, cancel=cancel)

        with patch.object(backup, "create", slow_save):
            try:
                click("Choose where to save")
                settle(lambda: page.writing and page.progress.bar.value() >= 16)
                shot("backup-17-save-progress")
            finally:
                release_save.set()
            settle(lambda: showing("Backup saved"))
        shot("backup-03-saved")
        click("Back")
        home()
        click("Restore")
        settle(lambda: isinstance(page.screen, ArchiveScreen))
        shot("backup-04-archive-contents")
        click("Preview changes")
        settle(lambda: page.plan is not None)
        shot("backup-05-preview")
        click("Keep both")
        settle(lambda: page.plan is not None and page.plan.policy == "keep_both")
        shot("backup-06-keep-both")
        process.return_value = True
        click("Restore to this installation")
        settle(lambda: showing("Close EDOPro to continue"))
        shot("backup-07-game-running")
        process.return_value = False

        release = Event()
        real_execute = backup.execute

        def slow(
            plan: backup.Plan,
            *,
            progress: Progress,
            cancel: Cancel,
            require_closed: bool,
        ) -> backup.Transaction:
            def sample_progress(done: int, total: int, name: str) -> None:
                assert progress is not None and cancel is not None
                if done >= 5:
                    progress(done, total, name)
                if done == 5:
                    release.wait(5)

            return real_execute(
                plan, progress=sample_progress, cancel=cancel, require_closed=require_closed
            )

        with patch.object(backup, "execute", slow):
            try:
                click("Retry")
                settle(lambda: page.writing and page.progress.bar.value() >= 5)
                shot("backup-08-restore-progress")
            finally:
                release.set()
            settle(lambda: showing("Restore complete"))
        shot("backup-09-restored")
        click("Back")
        home()
        shot("backup-10-history")
        click("Undo selected restore")
        settle(lambda: showing("Previous files restored"))
        shot("backup-11-undone")
        click("Back")
        home()
        click("Compare with a backup")
        settle(lambda: isinstance(page.screen, ArchiveScreen))
        click("Compare files")
        settle(lambda: isinstance(page.screen, CompareScreen))
        click("Name clashes  2")
        shot("backup-12-comparison")
        click("Back")
        path = future
        click("Choose another backup")
        settle(lambda: showing("This backup needs another version"))
        shot("backup-13-newer-format")

        install = backup.Installation(game, root)
        plan = backup.plan_restore(incoming, install)
        stopped = Event()

        def stop(done: int, _total: int, _path: str) -> None:
            if done > len(plan.manifest.entries):
                stopped.set()

        with suppress(backup.Cancelled):
            backup.execute(plan, progress=stop, cancel=stopped.is_set)
        window.navigate("cards")
        window.navigate("backup")
        home()
        shot("backup-14-interrupted")
        click("Roll back")
        settle(lambda: showing("Previous files restored"))
        ctx.set_installation(Installation(str(game)))
        home()
        shot("backup-15-installation-setting-up")
        ctx.set_installation(Installation())
        shot("backup-16-no-installation")
