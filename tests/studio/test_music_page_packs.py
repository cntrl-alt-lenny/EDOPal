"""Exercise pack controls through the shell with local archives and an offline catalogue."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from content.catalogue import Catalogue, CatalogueSource
from content.download import DownloadError, DownloadFailure, Progress
from content.manifest import Manifest
from studio.context import Installation
from studio.pages import music_packs
from studio.pages.music import MusicPage
from studio.widgets import Button
from tools.music_screenshots import demo_pack


def click(container: QWidget, text: str) -> None:
    QApplication.processEvents()
    next(b for b in container.findChildren(Button) if b.text() == text and b.isVisible()).click()


def messages(container: QWidget) -> str:
    return "\n".join(text.text() for text in container.findChildren(QLabel) if text.isVisible())


def idle(qtbot: QtBot, page: MusicPage) -> None:
    qtbot.waitUntil(lambda: not page.ctx.tasks.jobs)
    QApplication.processEvents()


def test_cache_to_album_and_offline_removal(
    make_window: Callable, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.navigate("music")
    p = window.current
    idle(qtbot, p)
    assert "No music assigned yet" in messages(p)
    assert not any(b.isVisible() for b in p.scene_buttons.values())
    listing = Catalogue(Manifest(1, ()), CatalogueSource.LIVE)

    async def fetch(*args: object, **kwargs: object) -> Catalogue:
        assert get_ident() != main_thread
        return listing

    main_thread = get_ident()
    monkeypatch.setattr(music_packs, "fetch_catalogue", fetch)
    monkeypatch.setattr(p.ctx, "confirm", lambda *_: True)
    p.manage.click()
    click(p, "Get more music")
    idle(qtbot, p)
    assert "No packs available yet" in messages(p)
    rows = []
    for kind in ("music", "covers"):
        pack, archive = demo_pack(tmp_path, kind)
        group = p.packs.cache.root / f"pack-{pack.id}"
        group.mkdir(parents=True)
        shutil.copyfile(archive, group / f"{pack.version}--{pack.sha256}.zip")
        rows.append(pack)
    listing = Catalogue(Manifest(1, tuple(rows)), CatalogueSource.CACHED, "Offline")
    click(p, "Refresh catalogue")
    idle(qtbot, p)
    assert "Catalogue offline" in messages(p)
    for pack in rows:
        card = p.packs.cards[pack.id]
        text = messages(card)
        assert all(
            s in text
            for s in ("Downloaded", pack.author, pack.license, pack.attribution, pack.source_url)
        )
        click(card, "Add to library")
        idle(qtbot, p)
        assert "Available in library" in messages(p.packs.cards[pack.id])
    listing = Catalogue(
        Manifest(1, (replace(rows[0], version="2.0.0"), rows[1])), CatalogueSource.LIVE
    )
    click(p, "Refresh catalogue")
    idle(qtbot, p)
    update = messages(p.packs.cards[rows[0].id])
    assert "Update available" in update and "Added to your library." not in update
    click(p.packs.cards[rows[0].id], "View album")
    idle(qtbot, p)
    assert p.albums.key == "dm3"
    assert p.albums.tracks.item(0, 0).text() == "Night in the city"
    assert p.albums.tracks.item(0, 1).text() == "Music pack"
    assert p.data.albums["dm3"].cover.is_file()
    assert all(pack.attribution in messages(p) for pack in rows)
    p.albums.tracks.cellWidget(0, 2).click()
    p.albums.destinations["deck"].click()
    idle(qtbot, p)
    active = p.data.scenes["deck"].tracks[0]
    assert active.origin.id == rows[0].id
    p.escape()
    p.manage.click()
    listing = Catalogue(Manifest(1, ()), CatalogueSource.BUNDLED, "Offline")
    click(p, "Get more music")
    idle(qtbot, p)
    for pack in rows:
        card = p.packs.cards[pack.id]
        assert pack.attribution in messages(card)
        click(card, "Remove pack")
        idle(qtbot, p)
    assert not p.packs.items and active.path.is_file()
    p.escape()
    idle(qtbot, p)
    assert p.data.albums["dm3"].cover is None
    assert [r.source for r in p.data.albums["dm3"].tracks] == ["installed"]


def test_cancel_return_and_retry_failure_keep_pack_credits(
    make_window: Callable, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.navigate("music")
    p = window.current
    idle(qtbot, p)
    pack, _ = demo_pack(tmp_path, "music")
    incompatible = replace(pack, id="later", min_app_version="99.0.0")
    catalogue = Catalogue(Manifest(1, (pack, incompatible)), CatalogueSource.LIVE)

    async def fetch(*args: object, **kwargs: object) -> Catalogue:
        return catalogue

    events = []

    async def transfer(
        *args: object, cancel: Event, progress: Callable, phase: Callable, **kwargs: object
    ) -> None:
        events.append(cancel)
        if len(events) > 1:
            raise DownloadError(DownloadFailure.INTEGRITY, "Checksum mismatch.")
        phase("downloading")
        progress(Progress(512, 1024, 1000))
        while not cancel.is_set():
            await asyncio.sleep(0.005)
        raise DownloadError(DownloadFailure.CANCELLED, "Cancelled.")

    monkeypatch.setattr(music_packs, "fetch_catalogue", fetch)
    monkeypatch.setattr(music_packs, "make_available", transfer)
    p.manage.click()
    click(p, "Get more music")
    idle(qtbot, p)
    assert "Requires Studio 99.0.0" in messages(p.packs.cards["later"])
    assert pack.attribution in messages(p.packs.cards["later"])
    click(p.packs.cards[pack.id], "Download")
    qtbot.waitUntil(lambda: p.packs.progress.percent.text() == "50%")
    assert pack.source_url in messages(p.packs.cards[pack.id])
    assert window.navigate("cards")
    second = tmp_path / "Other game"
    second.mkdir()
    p.ctx.set_installation(Installation(str(second), True))
    assert window.navigate("music") and p.state == "packs"
    p.escape()
    idle(qtbot, p)
    text = messages(p.packs.cards[pack.id])
    assert "Download cancelled" in text and pack.attribution in text
    click(p.packs.cards[pack.id], "Resume")
    idle(qtbot, p)
    text = messages(p.packs.cards[pack.id])
    assert "Verification failed" in text and pack.attribution in text
    assert events[0] is not events[1] and not events[1].is_set()
    p.escape()
    idle(qtbot, p)
    assert p.state == "manage" and p.game == second
