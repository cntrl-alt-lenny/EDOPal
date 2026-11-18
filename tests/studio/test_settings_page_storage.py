import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import main
from config import Config
from content.cache import PackCache
from content.catalogue import resolve_manifest_url
from studio.pages.settings_shortcuts import (
    Shortcut,
    change_shortcut,
    desktop_entry,
    shortcut_status,
)
from studio.pages.settings_storage import inspect_storage


def test_storage_uses_offline_titles_and_excludes_unrelated_files(
    game: Config, tmp_path: Path
) -> None:
    cache = PackCache.for_config(game)
    pack = cache.root / "pack-blue-grid"
    pack.mkdir(parents=True)
    (pack / "1.zip").write_bytes(b"12345")
    previews = cache.root / "preview-blue-grid"
    previews.mkdir()
    (previews / "sample.image").write_bytes(b"123")
    (tmp_path / "unrelated.iso").write_bytes(bytes(10000))
    (tmp_path / "soundtracks").mkdir()
    (tmp_path / "soundtracks/song.flac").write_bytes(bytes(321))
    url = resolve_manifest_url(game.content_manifest_url)
    folder = cache.root / ("manifest-" + hashlib.sha256(url.encode()).hexdigest())
    folder.mkdir()
    data = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/content-release-draft.json").read_bytes()
    )
    entry = data["packs"][0]
    entry.update(id="blue-grid", title="Blue Horizon", withheld=False)
    data["packs"] = [entry]
    (folder / "catalogue.json").write_text(
        json.dumps({"manifest": json.dumps(data)}), encoding="utf-8"
    )
    total = inspect_storage(game, cache)
    assert total.has_catalogue
    assert [(pack.title, pack.size) for pack in total.packs] == [("Blue Horizon", 8)]
    assert total.parts["Music library"] == 321
    assert sum(total.parts.values()) < 10000


def test_desktop_entry_escapes_paths_and_updates_moved_app(tmp_path: Path) -> None:
    first = tmp_path / "Games with spaces" / "Sync%20"
    second = tmp_path / "Moved app"
    link = Shortcut("Desktop", tmp_path / "desktop" / "Studio.desktop", False)
    change_shortcut(link, first, "", remove=False)
    assert "Sync%%20" in link.path.read_text(encoding="utf-8")
    assert shortcut_status(link, second, str(first)) == "moved"
    change_shortcut(link, second, str(first), remove=False)
    assert link.path.read_text(encoding="utf-8") == desktop_entry(second)


def test_explicit_release_check_distinguishes_offline_from_current() -> None:
    session = MagicMock()
    response = session.get.return_value.__aenter__.return_value
    response.status = 200
    response.json = AsyncMock(return_value={"tag_name": "v5.5.0"})
    assert asyncio.run(main.check_for_update(session, "5.5.0", strict=True)) is None
    response.status = 503
    with pytest.raises(OSError):
        asyncio.run(main.check_for_update(session, "5.5.0", strict=True))
    assert asyncio.run(main.check_for_update(session, "5.5.0")) is None
