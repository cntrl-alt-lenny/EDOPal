# Regenerate the EDOPal screenshots

Run this PowerShell block from the repository root with the project’s Python environment active. It uses the offline renderer, stages its output in a temporary folder and copies the selected 1280×800 views into this directory. The screenshots use fictional decks, players, music and pack listings. Review the resulting PNGs before publishing.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
@'
import os
import sys
from dataclasses import replace
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools import music_screenshots
from tools import studio_screenshots as renderer

views = {
    None: {
        "01-hd-cards": "edopal-overview",
        "05-sync-summary": "hd-cards-results",
        "skins-maker-wallpapers": "skins-theme-maker",
        "replays-detail": "replays",
        "backup-06-keep-both": "backup-transfer",
        "health-overview": "health-check",
        "35-about": "settings",
    },
    "sleeves": {"05-designer": "sleeves-designer"},
    "sounds": {"04-mixer": "sounds"},
    "music": {
        "09-album-preview": "music",
        "25-music-and-cover-packs": "content-packs",
    },
    "decks": {"decks-04-preview": "tournament-decks"},
    "mydecks": {"mydecks-10-extra-deck": "my-decks"},
}

grab = renderer.grab_window
demo_pack = music_screenshots.demo_pack

def public_window(window):
    update_visible = window.release.isVisible()
    window.release.hide()
    try:
        return grab(window)
    finally:
        window.release.setVisible(update_visible)

def demo_credits(root, kind):
    pack, archive = demo_pack(root, kind)
    return replace(
        pack,
        author="EDOPal demo",
        attribution=f"{pack.title} by EDOPal demo.",
        source_url="https://example.test/edopal",
    ), archive

with TemporaryDirectory(prefix=".docs-shots-", dir=Path.cwd()) as folder:
    work = Path(folder)
    profile = work / "profile"
    profile.mkdir()
    with (
        patch.dict(os.environ, dict.fromkeys(
            ("LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME", "TEMP", "TMP"), str(profile)
        )),
        patch.object(renderer, "grab_window", public_window),
        patch.object(music_screenshots, "demo_pack", demo_credits),
        patch("main._write_report", return_value=None),
    ):
        for page in views:
            output = work / (page or "all")
            args = ["studio_screenshots.py", "--out", str(output), "--size", "1280x800"]
            if page:
                args += ["--page", page]
            with patch.object(sys, "argv", args):
                renderer.main_cli()
        destination = Path("docs/screenshots")
        for page, filenames in views.items():
            source = work / (page or "all") / "1280x800"
            for capture, public_name in filenames.items():
                copyfile(source / f"{capture}.png", destination / f"{public_name}.png")
'@ | python -
```

The capture wrapper suppresses the synthetic update notice and sync report path. The pack capture uses demo credits. The [gallery](../gallery.md) lists the published views.
