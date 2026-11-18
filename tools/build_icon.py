"""Generate the Linux, Windows and Finder icons from assets/app-icon.png."""

from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ICON = ROOT / "assets/app-icon.png"


def build_icons(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE_ICON) as source:
        image = ImageOps.pad(source.convert("RGBA"), (1024, 1024), color=(0, 0, 0, 0))
    image.save(directory / "app-icon.png")
    image.save(directory / "app-icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    image.save(directory / "app-icon.icns")


if __name__ == "__main__":
    build_icons(ROOT / "build")
