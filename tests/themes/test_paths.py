import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from themes import PRESETS, ThemeLibrary, install_theme, remove_theme


@contextmanager
def redirected(target: Path, link: Path) -> Iterator[None]:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        yield
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()


def test_redirected_library_and_skin_subdirectories_preserve_external_files(tmp_path: Path) -> None:
    outside = tmp_path / "personal"
    outside.mkdir()
    sentinel = outside / "bg.png"
    sentinel.write_bytes(b"keep")
    with redirected(outside, tmp_path / "themes"), pytest.raises(OSError):
        ThemeLibrary(tmp_path / "themes").save(PRESETS[0])
    game = tmp_path / "game"
    game.mkdir()
    theme_id = "9" * 32
    name = install_theme(game, theme_id, replace(PRESETS[0], wallpaper="inherit"))
    folder = game / "skin" / name
    with redirected(outside, folder / "textures"), pytest.raises(OSError):
        install_theme(game, theme_id, PRESETS[0])
    assert sentinel.read_bytes() == b"keep"
    assert list(outside.iterdir()) == [sentinel]
    remove_theme(game, theme_id)
