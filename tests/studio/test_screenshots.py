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
    assert "Rendered 18 views" in process.stdout
    files = list((tmp_path / "1280x800").glob("*.png"))
    assert len(files) == 18
    for path in files:
        with Image.open(path) as image:
            assert image.size == (1280, 800)
            assert image.getbbox() is not None
