"""Open the relevant review or control through each destination's existing UI."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from decks import Library
from doctor import Finding
from studio.pages.cards import CardsPage
from studio.pages.music import MusicPage
from studio.pages.music_model import SCENES
from studio.pages.mydecks import MyDecksPage
from studio.pages.mydecks_library import Collection
from studio.pages.sleeves import SleevesPage
from studio.pages.sounds import SoundsPage
from studio.tasks import Job


def open_finding(source: QWidget, item: Finding, key: str) -> None:
    from studio.shell import StudioWindow

    window = source.window()
    if not isinstance(window, StudioWindow):
        return
    ctx = window.ctx
    install = ctx.installation
    # The destination starts its read during navigation. Other jobs can keep running.
    previous = ctx.tasks.jobs.copy()
    if not ctx.navigate(key):
        return
    page = window.current

    def active() -> bool:
        return (
            not window.closing
            and isValid(page)
            and page.isVisible()
            and ctx.installation == install
        )

    def after(job: Job, callback: Callable[[], None]) -> None:
        def show() -> None:
            if active():
                callback()

        job.finished.connect(show)

    if isinstance(page, CardsPage):
        if item.id.startswith("artwork."):
            page.show_coverage()
        elif item.id == "storage.low" and page.job is None:
            page.open_view("options")
            page.options.scope.buttons[1].setFocus()
    elif isinstance(page, MyDecksPage):
        epoch = page.epoch
        for job in ctx.tasks.jobs - previous:
            job.result.connect(
                lambda result: _show_deck(page, epoch, item, result) if active() else None
            )
    elif isinstance(page, MusicPage) and page.job:
        generation = page.generation
        after(page.job, lambda: _show_music(page, generation, item))
    elif isinstance(page, SoundsPage):
        page.open_view("overview")
        target = page.overview.toggle
        if item.id != "sound.disabled":
            target = page.bar.undo if item.id == "state.interrupted" else page.bar.restore
        if page.scan_job:
            after(page.scan_job, target.setFocus)
        else:
            target.setFocus()
    elif isinstance(page, SleevesPage):
        generation = page.generation
        for job in ctx.tasks.jobs - previous:
            after(job, lambda: page.undo.setFocus() if page.generation == generation else None)


def _show_deck(page: MyDecksPage, epoch: int, item: Finding, result: object) -> None:
    if page.epoch != epoch or not isinstance(result, Library) or page.lib is not result:
        return
    if not isinstance(page.view, Collection):
        return
    if item.id == "banlists.missing":
        if not result.banlists:
            page.check()
        return
    path = item.evidence.get("path")
    deck = next((deck for deck in result.decks if deck.path == path), None)
    if deck:
        page.detail(deck)
    elif result.issues:
        page.view.show_issues()
    elif item.id.startswith("banlists."):
        page.check()


def _show_music(page: MusicPage, generation: int, item: Finding) -> None:
    if page.generation != generation or page.state != "home":
        return
    if item.id == "music.disabled" and page.data and page.data.enabled is False:
        page.toggle_music.setFocus()
        page.scroll.ensureWidgetVisible(page.toggle_music)
        return
    path = item.evidence.get("path")
    missing = item.evidence.get("missing")
    scene = ""
    if isinstance(path, Path) and path.name == "pending.json":
        scene = path.parent.name
    elif isinstance(missing, tuple) and missing:
        scene = str(missing[0])
    if scene in SCENES:
        page.open_scene(scene)
        if item.id == "state.interrupted" and page.albums.undo.isEnabled():
            page.albums.undo.setFocus()
            page.scroll.ensureWidgetVisible(page.albums.undo)
