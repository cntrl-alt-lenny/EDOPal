from collections.abc import Callable
from dataclasses import replace
from threading import Event
from unittest.mock import AsyncMock

import pytest
from pytestqt.qtbot import QtBot

from content import catalogue, libraries
from content.download import DownloadError, DownloadFailure, Progress
from content.manifest import Manifest
from studio.context import Installation
from studio.shell import StudioWindow
from tools.studio_skins_screenshots import library_archive


@pytest.mark.parametrize(("section", "kind"), [("skins", "wallpaper"), ("sleeves", "sleeves")])
def test_pack_browser_install_use_and_remove(
    make_window: Callable[..., StudioWindow],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    kind: str,
) -> None:
    window = make_window()
    monkeypatch.setattr(window, "confirm", lambda *_: True)
    window.ctx.set_installation(Installation())
    window.navigate(section)
    page = window.current
    qtbot.waitUntil(lambda: page.gallery.more.isEnabled())
    pack, body = library_archive(kind)
    other = replace(pack, kind="music", id="soundtrack")
    source = catalogue.Catalogue(Manifest(1, (pack, other)), catalogue.CatalogueSource.LIVE)
    monkeypatch.setattr(catalogue, "fetch_catalogue", AsyncMock(return_value=source))
    browser = page.packs
    page.gallery.more.click()
    qtbot.waitUntil(lambda: not browser.loading)
    assert browser.menu.count() == 1 and browser.rows[0].state == "Not downloaded"
    assert pack.author in browser.credits.text() and pack.attribution in browser.credits.text()
    assert pack.license in browser.credits.text() and browser.link.toolTip() == pack.source_url
    entered = Event()

    async def stalled(*args: object, **kwargs: object) -> None:
        kwargs["phase"]("downloading")
        kwargs["progress"](Progress(100, 200, 20))
        entered.set()
        assert kwargs["cancel"].wait(4)
        raise DownloadError(DownloadFailure.CANCELLED, "Cancelled")

    with monkeypatch.context() as patch:
        patch.setattr(libraries, "make_available", stalled)
        browser.action.click()
        qtbot.waitUntil(lambda: entered.is_set() and browser.progress.value() == 50)
        assert window.navigate("cards")
        assert window.navigate(section)
        assert browser.isVisible() and browser.cancel.isEnabled() and page.escape()
        browser.cancel.click()
        qtbot.waitUntil(lambda: browser.action.text() == "Resume")
    with monkeypatch.context() as patch:
        patch.setattr(libraries, "make_available", AsyncMock(side_effect=ValueError("Bad image")))
        browser.action.click()
        qtbot.waitUntil(lambda: "Invalid pack" in browser.feedback.text())
        assert browser.action.text() == "Retry" and pack.attribution in browser.credits.text()

    folder = browser.cache.root / ("pack-" + pack.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pack.version}--{pack.sha256}.zip").write_bytes(body)
    browser.refresh.click()
    qtbot.waitUntil(lambda: browser.action.text() == "Add to library")
    browser.action.click()
    qtbot.waitUntil(lambda: browser.rows[0].state == "Available in library")
    page.escape()
    if section == "skins":
        qtbot.waitUntil(lambda: page.maker.wallpapers.count() == 2)
        page.gallery.create.click()
        page.maker.fine.click()
        page.maker.wallpapers.activated.emit(1)
        qtbot.waitUntil(
            lambda: (
                page.view.preview.completed is not None
                and page.view.preview.completed.theme.image == page.maker.theme.image
            )
        )
        assert page.maker.theme.image and not page.view.preview.canvas.pix.isNull()
        page.maker.save_btn.click()
        qtbot.waitUntil(lambda: page.mode == "detail" and page.job is None)
        theme = page.look.theme
        page.escape()
    else:
        qtbot.waitUntil(lambda: not page.loading)
        page.gallery.source.setCurrentText("Pack sleeve")
        assert [item.name for item in page.gallery.visible] == ["Evening lake"]
        assert pack.attribution in page.gallery.visible[0].tooltip

    # Cache eviction and a withdrawn catalogue entry must leave the installed edition manageable.
    browser.cache.remove_pack(pack.id)
    monkeypatch.setattr(
        catalogue, "fetch_catalogue", AsyncMock(side_effect=ValueError("Invalid catalogue URL"))
    )
    page.gallery.more.click()
    qtbot.waitUntil(lambda: not browser.loading)
    assert "Offline" in browser.source_hint.text() and browser.rows[0].status is None
    assert browser.remove.isEnabled() and pack.attribution in browser.credits.text()
    browser.remove.click()
    qtbot.waitUntil(lambda: not browser.job and not browser.loading and not browser.rows)
    assert "Pack removed" in browser.feedback.text()
    page.escape()
    if section == "skins":
        qtbot.waitUntil(lambda: page.maker.wallpapers.count() == 1)
        assert not page.store.library.image(theme).isNull()
    else:
        qtbot.waitUntil(lambda: not page.loading)
        assert not page.gallery.visible
    window.navigate("cards")
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
