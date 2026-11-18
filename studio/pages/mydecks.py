"""Personal deck library for the selected EDOPro installation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout

from decks import DeckIndexer, Library, Preview, SavedDeck, encode_ydke
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.mydecks_checks import Comparison, FormatCheck
from studio.pages.mydecks_copies import CopyReview
from studio.pages.mydecks_files import (
    Receipt,
    publish,
    remove_copy,
    require_closed,
    reveal_file,
)
from studio.pages.mydecks_library import Collection
from studio.pages.mydecks_logic import file_problem, quantity, section_counts
from studio.pages.mydecks_widgets import Contents, DeckHero, Screen, button
from studio.widgets import ElidedLabel, Panel, label, margins


class MyDecksPage(Page):
    id, title, icon = "mydecks", "My decks", "deck"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.history: list[Screen] = []
        self.epoch = 0
        self.active = self.writing = False
        self.index: DeckIndexer | None = None
        self.lib: Library | None = None
        self.receipts: dict[Path, Receipt] = {}
        self.retry_action: Callable | None = None
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(0)
        self.status = Panel(t.SM)
        status_row = QHBoxLayout()
        self.message = label("", "body", True)
        status_row.addWidget(self.message, 1)
        self.retry = button("Retry", self._retry)
        status_row.addWidget(self.retry)
        self.status.body.addLayout(status_row)
        self.status.hide()
        body.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.slide = QPropertyAnimation(self)
        self.slide.setPropertyName(b"pos")
        self.slide.setDuration(t.MOTION_MS)
        self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.scroll, 1)

    @property
    def view(self) -> Screen:
        return self.history[-1]

    def shown(self) -> None:
        self.active = True
        self.refresh()

    def hidden(self) -> bool:
        if self.writing:
            return False
        self.active = False
        self.epoch += 1
        return True

    def installation_changed(self, installation: Installation) -> None:
        self.epoch += 1
        self.index = DeckIndexer(Path(installation.path)) if installation.path else None
        self.lib = None
        if self.active:
            self.refresh()

    def _forget(self, screen: Screen) -> None:
        if isinstance(screen, (FormatCheck, Comparison)):
            screen.leave()
        screen.hide()
        screen.deleteLater()

    def _push(self, screen: Screen, *, root: bool = False) -> None:
        self.slide.stop()
        old = self.scroll.takeWidget()
        if old:
            old.hide()
            old.setParent(self)
        if root:
            for previous in self.history:
                self._forget(previous)
            self.history.clear()
        self.history.append(screen)
        self.scroll.setWidget(screen)
        self.scroll.verticalScrollBar().setValue(0)
        self.status.hide()
        if screen.initial:
            screen.initial.setFocus()
        if not root:
            self._slide_in(t.XL)

    def _slide_in(self, offset: int) -> None:
        end = self.view.pos()
        self.slide.setTargetObject(self.view)
        self.slide.setStartValue(end + QPoint(offset, 0))
        self.slide.setEndValue(end)
        self.slide.start()

    def escape(self) -> bool:
        if self.writing:
            return True
        if len(self.history) < 2:
            return False
        self.slide.stop()
        self.scroll.takeWidget()
        self._forget(self.history.pop())
        self.status.hide()
        self.scroll.setWidget(self.view)
        if self.view.initial:
            self.view.initial.setFocus()
        self._slide_in(-t.XL)
        return True

    def _state(
        self, title: str, description: str, action: str = "", callback: Callable | None = None
    ) -> None:
        screen = Screen()
        hero = DeckHero(description)
        hero.headline.setText(title)
        hero.setMinimumHeight(t.HERO_MIN)
        hero.setMaximumHeight(t.HERO_MAX)
        hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        screen.body.addWidget(hero, 1)
        if action and callback:
            screen.initial = button(action, callback, "primary")
            screen.body.addWidget(screen.initial, 0, Qt.AlignmentFlag.AlignLeft)
        self._push(screen, root=True)

    def refresh(self) -> None:
        if self.writing:
            return
        self.epoch += 1
        epoch = self.epoch
        install = self.ctx.installation
        if not install.path:
            self._state(
                "Choose your game",
                "Select an EDOPro installation to open its deck library.",
                "Choose folder",
                self.ctx.installations_requested.emit,
            )
            return
        if not install.ready:
            self._state(
                "Waiting for cards",
                "Open EDOPro once to update its card data, then check again.",
                "Check again",
                self._inspect,
            )
            return
        index = self.index
        if index is None:
            self.index = index = DeckIndexer(Path(install.path))
        self._state("Reading your decks", "Finding saved decks and looking up their cards.")
        job = self.ctx.tasks.start(lambda _: index.scan())

        def ready(lib: Library) -> None:
            if self.epoch != epoch:
                return
            self.lib = lib
            view = Collection(
                lib, self.detail, self.check, self.compare, self.import_code, self.refresh
            )
            self._push(view, root=True)
            current_paths = {d.path for d in lib.decks}
            if latest := next(
                (r for r in reversed(self.receipts.values()) if r.path in current_paths),
                None,
            ):
                self._receipt_bar(view, latest)

        job.result.connect(ready)
        job.error.connect(
            lambda exc: (
                self._state("Library unavailable", str(exc), "Retry", self.refresh)
                if epoch == self.epoch
                else None
            )
        )

    def _inspect(self) -> None:
        path, epoch = self.ctx.installation.path, self.epoch
        self.view.initial.setEnabled(False)
        job = self.ctx.tasks.start(lambda _: Installation.inspect(path))
        job.result.connect(
            lambda selection: self.ctx.set_installation(selection) if epoch == self.epoch else None
        )
        job.error.connect(
            lambda exc: (
                self._state("Folder unavailable", str(exc), "Retry", self.refresh)
                if epoch == self.epoch
                else None
            )
        )

    def check(self, deck: SavedDeck | None = None, selected: int = 0) -> None:
        self._push(FormatCheck(self.lib, self.ctx.tasks, self.escape, self.check, deck, selected))

    def compare(self) -> None:
        self._push(Comparison(self.lib, self.ctx.tasks, self.escape))

    def import_code(self, deck: SavedDeck | None = None) -> None:
        screen = CopyReview(self.lib, self.ctx.tasks, self.escape, self._save, deck)
        if deck is None:
            screen.code.textChanged.connect(self.status.hide)
        screen.name.textChanged.connect(self.status.hide)
        self._push(screen)

    def detail(self, deck: SavedDeck) -> None:
        screen = Screen(deck.name, self.escape)
        path = ElidedLabel()
        path.set_value(str(deck.path))
        screen.body.addWidget(path)
        panel = Panel(t.LG)
        info = QHBoxLayout()
        info.addWidget(
            label(
                section_counts(deck.preview.counts) if deck.preview else "Unreadable deck",
                "subheading",
                True,
            ),
            1,
        )
        info.addWidget(button("Check format", lambda: self.check(deck), "primary", "check"))
        panel.body.addLayout(info)
        if deck.preview and deck.preview.unresolved:
            n = len(deck.preview.unresolved)
            panel.body.addWidget(
                label(
                    f"The game is missing information for {quantity(n, 'card')}. "
                    "Review possible replacements in Repair a copy.",
                    "body",
                    True,
                )
            )
        buttons = QHBoxLayout()
        buttons.addWidget(button("Show file", lambda: self._reveal(deck.path), symbol="folder"))
        if deck.preview:
            buttons.addWidget(
                button("Copy code", lambda: self._copy_code(deck.preview), symbol="link")
            )
            buttons.addWidget(button("Get HD artwork", lambda: self._art(deck), symbol="download"))
            if deck.preview.unresolved:
                buttons.addWidget(button("Repair a copy", lambda: self.import_code(deck)))
        buttons.addStretch()
        panel.body.addLayout(buttons)
        screen.body.addWidget(panel)
        if deck.preview:
            screen.contents = Contents(self.ctx.tasks, self.lib.game, deck.preview)
            screen.body.addWidget(screen.contents, 1)
        else:
            screen.body.addWidget(
                label(
                    f"The game’s saved deck could not be read. {file_problem(deck.error)}",
                    "body",
                    True,
                )
            )
            screen.body.addStretch()
        screen.body.addWidget(
            label("To edit cards, open Deck Edit in EDOPro and choose this deck.", "muted", True)
        )
        self._push(screen)
        if receipt := self.receipts.get(deck.path):
            self._receipt_bar(screen, receipt)

    def _copy_code(self, preview: Preview) -> None:
        try:
            code = encode_ydke(preview.sections)
        except ValueError as exc:
            self._notice(str(exc))
            return
        QApplication.clipboard().setText(code)
        self._notice("Sharing code copied. Paste it to share all three sections.")

    def _notice(self, message: str, retry: Callable | None = None) -> None:
        self.retry_action = retry
        self.retry.setVisible(retry is not None)
        self.message.setText(message)
        self.status.show()

    def _retry(self) -> None:
        if self.retry_action and not self.writing:
            self.retry_action()

    def _reveal(self, path: Path) -> None:
        epoch = self.epoch
        job = self.ctx.tasks.start(lambda _: reveal_file(path))
        job.error.connect(lambda exc: self._notice(str(exc)) if epoch == self.epoch else None)

    def _art(self, deck: SavedDeck) -> None:
        game = self.lib.game

        def start(_: None) -> None:
            if not self.ctx.sync_deck_files(game, (deck.path,)):
                self._notice("Artwork could not start. Refresh the library and try again.")

        self._write("Checking that EDOPro is closed…", lambda: require_closed(game), start)

    def _save(self, name: str, preview: Preview) -> None:
        game = self.lib.game

        def saved(receipt: Receipt) -> None:
            self.receipts[receipt.path] = receipt
            self.refresh()

        self._write("Saving a new deck copy…", lambda: publish(game, name, preview), saved)

    def _receipt_bar(self, screen: Screen, receipt: Receipt) -> None:
        row = QHBoxLayout()
        msg = ElidedLabel()
        msg.set_value(f"Saved {receipt.path.name} · Available next time you start EDOPro.")
        row.addWidget(msg, 1)
        if receipt.stamp:
            row.addWidget(button("Remove saved copy", lambda: self._undo(receipt)))
        else:
            msg.set_value(
                f"Saved {receipt.path.name}. Use Show file to locate the copy for removal."
            )
            row.addWidget(button("Show file", lambda: self._reveal(receipt.path)))
        screen.body.insertLayout(1, row)

    def _undo(self, receipt: Receipt) -> None:
        if not self.ctx.confirm(
            "Remove saved copy?", f"Remove only {receipt.path.name} from your deck library?"
        ):
            return
        game = self.lib.game

        def removed(_: None) -> None:
            self.receipts.pop(receipt.path, None)
            self.refresh()

        self._write("Removing the saved copy…", lambda: remove_copy(game, receipt), removed)

    def _write(self, title: str, work: Callable, done: Callable) -> None:
        if self.writing or self.ctx.busy:
            return
        self.writing = True
        self.ctx.set_busy(True)
        self.scroll.setEnabled(False)
        self._notice(title + " Stay on this page until it finishes.")
        job = self.ctx.tasks.start(lambda _: work())

        def release() -> None:
            self.writing = False
            self.ctx.set_busy(False)
            self.scroll.setEnabled(True)

        def success(result: Any) -> None:
            release()
            self.status.hide()
            done(result)

        def failed(error: BaseException) -> None:
            release()
            self._notice(str(error), lambda: self._write(title, work, done))

        job.result.connect(success)
        job.error.connect(failed)
