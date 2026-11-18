import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image
from pytestqt.qtbot import QtBot

from studio.shell import StudioWindow
from tools.studio_screenshots import ROOT, grab_window


def test_small_window_capture_settles_the_music_grid(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    from tools.music_screenshots import recording

    window.resize(960, 600)
    # The empty-library view deliberately hides the scene grid.
    recording(Path(window.ctx.installation.path) / "sound/BGM/menu/Welcome.wav", 1)
    window.navigate("music")
    page = window.current
    qtbot.waitUntil(lambda: page.state == "home")
    assert grab_window(window).size() == window.size()
    # Whether the page overflows depends on its content; what must hold after a
    # settled capture is that no scene button is squeezed below its minimum.
    assert all(b.height() >= b.minimumSizeHint().height() for b in page.scene_buttons.values())


def test_screenshot_cli_writes_all_views_at_requested_size(tmp_path: Path) -> None:
    process = subprocess.run(
        [
            sys.executable,
            "tools/studio_screenshots.py",
            "--out",
            str(tmp_path),
            "--size",
            "1280x800",
        ],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    files = list((tmp_path / "1280x800").glob("*.png"))
    assert f"Rendered {len(files)} views" in process.stdout
    names = {path.stem for path in files}
    shell = [
        "hd-cards",
        "sync-settings",
        "coverage",
        "sync-progress",
        "sync-summary",
        "no-installation",
        "installation-picker",
        "skins",
        "sleeves",
        "sounds",
        "music",
        "decks",
        "settings",
        "unavailable-artwork",
        "sync-cancelled",
        "up-to-date",
        "sync-error",
        "awaiting-databases",
    ]
    assert {f"{i:02}-{name}" for i, name in enumerate(shell, 1)} <= names
    assert {
        "19-close-game",
        "35-about",
        "38-restored-artwork",
        "41-pack-in-use",
        "42-picker-empty",
        "43-picker-invalid",
        "44-shortcut-name-in-use",
    } <= names
    skins = [
        "active-gallery",
        "applied",
        "applying",
        "awaiting-game",
        "close-game",
        "empty-library",
        "gallery",
        "light",
        "maker-colour",
        "maker-overrides",
        "maker-photo",
        "maker-quick",
        "maker-wallpapers",
        "my-library",
        "no-installation",
        "no-results",
        "offline",
        "pack-applied",
        "pack-cancelled",
        "pack-detail",
        "pack-download",
        "pack-gallery",
        "pack-highlight",
        "pack-ready",
        "pack-restored",
        "pack-verification-error",
        "preview-deck",
        "preview-duel",
        "preview-menu",
        "previous-look",
        "recovered",
        "rename",
        "saved-theme",
    ]
    assert {f"skins-{name}" for name in skins} <= names
    for path in files:
        with Image.open(path) as image:
            assert image.size == (1280, 800)
            assert image.getbbox() is not None


@pytest.mark.parametrize(
    "page,last_state",
    [
        ("skins", "skins-pack-restored"),
        ("backup", "backup-14-interrupted"),
        ("health", "health-evidence"),
        ("replays", "replays-large-library"),
    ],
)
def test_cli_renders_only_selected_page_scenarios(
    tmp_path: Path, page: str, last_state: str
) -> None:
    subprocess.run(
        [
            sys.executable,
            "tools/studio_screenshots.py",
            "--page",
            page,
            "--out",
            str(tmp_path),
            "--size",
            "960x600",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=90,
    )
    images = list((tmp_path / "960x600").glob("*.png"))
    assert len(images) > 5
    prefixes = (page + "-", "wallpaper-catalogue-") if page == "skins" else (page + "-",)
    assert all(
        path.stem.startswith(prefixes)
        or (page == "skins" and path.stem == "sleeves-with-active-theme")
        for path in images
    )
    assert last_state in {p.stem for p in images}
