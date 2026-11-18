"""Measure two real Music page reads against an isolated 84-recording library."""

from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import IO, Any
from unittest.mock import patch

from music.catalogue import SCENES
from studio.pages.music_model import read_library


def benchmark(root: Path) -> None:
    game, library = root / "game", root / "profile/soundtracks"
    block = bytes(1 << 20)
    for index in range(84):
        if index < len(SCENES):
            folder = game / "sound/BGM" / list(SCENES)[index]
        else:
            folder = library / "tf5" / ("deck" if index < 24 else "duel")
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{index:03}.flac").open("wb") as output:
            output.write(index.to_bytes(4, "little") + block[4:])
            for _ in range(15):
                output.write(block)

    open_file = Path.open
    reads: list[int] = []

    def counted(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path.suffix == ".flac" and path.is_relative_to(root):
            reads.append(path.stat().st_size)
        return open_file(path, *args, **kwargs)

    for visit in (1, 2):
        reads.clear()
        start = perf_counter()
        with patch.object(Path, "open", counted):
            read_library(game, library, lambda: None)
        print(
            f"Visit {visit}: {perf_counter() - start:.3f} s, {len(reads)} recording opens, {sum(reads):,} bytes"
        )


if __name__ == "__main__":
    with TemporaryDirectory(dir=Path.cwd(), prefix=".music-benchmark-") as temporary:
        benchmark(Path(temporary))
