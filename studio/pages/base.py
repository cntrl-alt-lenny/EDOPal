from collections.abc import Callable, Iterator, Mapping

from PySide6.QtWidgets import QVBoxLayout, QWidget

from studio.context import AppContext, Installation
from studio.widgets import EmptyState, margins


class Page(QWidget):
    id: str
    title: str
    icon: str

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.ctx = context

    def shown(self) -> None:
        """Called after navigation has made this the current page."""

    def hidden(self) -> bool:
        """Return False to keep the current page visible."""
        return True

    def installation_changed(self, installation: Installation) -> None:
        """Called on every page when a folder has been inspected and selected."""

    def escape(self) -> bool:
        """Return True after handling Escape."""
        return False

    def request_close(self) -> bool:
        return self.hidden()


class Placeholder(Page):
    description: str
    category: str

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.addWidget(EmptyState(self.title, self.description, self.icon, self.category))


class PageRegistry(Mapping[str, Page]):
    """Keep sidebar order independent of which widgets the player has visited."""

    def __init__(self, types: tuple[type[Page], ...], build: Callable[[type[Page]], Page]) -> None:
        self.types = {page.id: page for page in types}
        self.built: dict[str, Page] = {}
        self._build = build

    def __getitem__(self, key: str) -> Page:
        if key not in self.built:
            self.built[key] = self._build(self.types[key])
        return self.built[key]

    def __contains__(self, key: object) -> bool:
        return key in self.types

    def __iter__(self) -> Iterator[str]:
        return iter(self.types)

    def __len__(self) -> int:
        return len(self.types)
