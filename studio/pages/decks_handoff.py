"""Selection passed from a tournament receipt to the personal deck library."""

from pathlib import Path

from PySide6.QtCore import QObject

from studio.context import AppContext

HD_ARTWORK = "Get HD artwork"


class SavedDeckSelection(QObject):
    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.paths: tuple[Path, ...] = ()


def saved_deck_selection(context: AppContext) -> SavedDeckSelection:
    channel = context.findChild(SavedDeckSelection)
    return channel if channel is not None else SavedDeckSelection(context)
