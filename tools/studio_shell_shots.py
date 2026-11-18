"""Additional shell and preferences states for the offline gallery."""

import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import main
from studio.context import Installation
from studio.pages.settings_shortcuts import Shortcut
from studio.shell import StudioWindow


def render_shell_states(
    window: StudioWindow,
    shot: Callable[[str], None],
    settle: Callable[[Callable[[], bool]], None],
    root: Path,
    selected: Installation,
) -> None:
    ctx = window.ctx
    ctx.set_installation(selected)
    card = window.pages["cards"]
    card.open_view("options")
    with patch("music.settings._game_running", return_value=True):
        card.options.start.click()
        settle(lambda: card.state == "blocked")
        shot("19-close-game")
    card.back.click()
    settle(lambda: card.options.undo.isVisible())
    shot("20-last-sync")
    with patch.object(ctx, "confirm", return_value=True):
        picture = Path(ctx.cfg.pics_path) / "1100.jpg"
        downloaded = picture.read_bytes()
        picture.write_bytes(b"Edited after the sync")
        card.options.undo.click()
        settle(lambda: card.state == "results")
        shot("39-edited-artwork-kept")
        picture.write_bytes(downloaded)
        card.results.undo.click()
        settle(lambda: card.state == "results")
        shot("38-restored-artwork")
        card.results.done.click()
    failures = main.DownloadStats()
    failures.record_failure(1001, "Dark Magician", transient=True)
    failures.record_field_failure(1002, "Duel field", transient=True)
    with patch.object(ctx, "run_coro", AsyncMock(return_value=failures)):
        card.options.start.click()
        settle(lambda: card.state == "results")
        shot("40-network-retry")
        card.results.done.click()
    for i, key in enumerate(("mydecks", "replays", "backup", "health"), 21):
        window.navigate(key)
        shot(f"{i}-{key}")
    window.navigate("settings")
    page = window.current
    second = root / "Games" / "Portable"
    second.mkdir()
    (second / "EDOPro.exe").touch()
    ctx.set_installation(Installation(str(second)))
    ctx.set_installation(selected)
    page.open_section("games")
    shot("25-game-folders")
    page.open_section("defaults")
    shot("26-card-defaults")
    page.open_section("storage")
    settle(lambda: page.message.text() == "")
    shot("27-storage-empty")
    pack = page.cache.root / "pack-midnight-circuit"
    pack.mkdir(parents=True)
    (pack / "1.0.0.zip").write_bytes(bytes(24576))
    page.open_section("storage")
    settle(lambda: bool(page.remove_buttons))
    page.detail.ensureWidgetVisible(page.clear_packs)
    shot("28-downloaded-packs")
    with (
        patch.object(ctx, "confirm", return_value=True),
        patch.object(
            page.cache,
            "clear",
            side_effect=OSError("A pack is in use. Try again when its download finishes."),
        ),
    ):
        page.clear_packs.click()
        settle(lambda: not ctx.busy)
        page.detail.ensureWidgetVisible(page.message)
        shot("41-pack-in-use")
    page.open_section("updates")
    with patch("main.check_for_update", new=AsyncMock(return_value=None)):
        page.check.click()
        settle(lambda: not ctx.checking_update)
    shot("29-latest-version")
    with patch("main.check_for_update", new=AsyncMock(return_value="v5.6.0")):
        page.check.click()
        settle(lambda: not ctx.checking_update)
    shot("30-update-available")
    with patch("main.check_for_update", new=AsyncMock(side_effect=OSError)):
        page.check.click()
        settle(lambda: not ctx.checking_update)
    shot("31-update-offline")
    page.open_section("shortcuts")
    shot("32-source-shortcuts")
    windows = sys.platform == "win32"
    filename = "Studio.lnk" if windows else "Studio.desktop"
    menu = "Start menu" if windows else "Applications menu"
    links = tuple(Shortcut(name, root / name / filename, windows) for name in ("Desktop", menu))
    with patch("studio.pages.settings.shortcut_locations", return_value=links):
        page.open_section("shortcuts")
        settle(lambda: page.message.text() == "")
        shot("33-shortcuts")
        from studio.widgets import Button

        create = next(
            b
            for b in page.detail.findChildren(Button)
            if b.isVisibleTo(page.detail) and b.text() == "Create shortcut"
        )
        create.click()
        settle(lambda: links[0].path.is_file() and not ctx.busy and page.message.text() == "")
        shot("34-shortcut-created")
    page.open_section("about")
    shot("35-about")
    ctx.state.remove("games/known")
    ctx.set_installation(Installation())
    page.open_section("games")
    shot("36-no-game-folders")
    ctx.set_installation(Installation(str(second)))
    shot("37-game-awaiting-data")
