"""Generate the Linux, Windows and Finder icons from assets/app-icon.png."""

from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ICON = ROOT / "assets/app-icon.png"


def build_icons(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE_ICON) as source:
        image = ImageOps.pad(source.convert("RGBA"), (1024, 1024), color=(0, 0, 0, 0))
    image.save(directory / "app-icon.png")
    small_frames = []
    small_source = SOURCE_ICON.with_name("app-icon-small.png")
    if small_source.is_file():
        with Image.open(small_source) as small:
            # A replacement launcher icon must never inherit a previous logo's face.
            if small.info.get("SourceSHA256") == sha256(SOURCE_ICON.read_bytes()).hexdigest():
                small_frames = [
                    small.resize((s, s), Image.Resampling.LANCZOS) for s in (16, 20, 24)
                ]
    image.save(
        directory / "app-icon.ico",
        sizes=[(s, s) for s in (16, 20, 24, 32, 48, 64, 128, 256)],
        append_images=small_frames,
    )
    image.save(directory / "app-icon.icns")


if __name__ == "__main__":
    build_icons(ROOT / "build")
