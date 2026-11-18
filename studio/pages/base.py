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
