import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

from tools.studio_screenshots import ROOT


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
