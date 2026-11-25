"""Generate the Windows and macOS launcher icons from EDOPal's artwork."""

import argparse
from hashlib import sha256
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parents[1] / "src/edopal/assets"
ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def build_icons(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    icon = ASSETS / "app-icon.png"
    with Image.open(icon) as source, Image.open(ASSETS / "app-icon-small.png") as small:
        image = source.convert("RGBA")
        # The artist supplied a simplified face for the smallest Windows icons.
        frames = []
        if small.info.get("SourceSHA256") == sha256(icon.read_bytes()).hexdigest():
            frames = [small.resize((size, size), Image.Resampling.LANCZOS) for size in ICO_SIZES[:3]]
        image.save(directory / "app-icon.ico", sizes=[(size, size) for size in ICO_SIZES], append_images=frames)
        image.save(directory / "app-icon.icns")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    build_icons(parser.parse_args().directory)
