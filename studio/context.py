"""Page services and the selected installation shared throughout the window."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Signal

import config
from artwork_flow import recommend_deck_scope
from config import Config
from studio.tasks import RunCoro, TaskRunner


@dataclass(frozen=True)
class Installation:
    path: str = ""
    ready: bool = False
    recommend_decks: bool = False

    @classmethod
    def inspect(cls, path: str) -> "Installation":
        if not path:
            return cls()
        ready = config.folder_has_card_databases(path)
        if not ready and not config.looks_like_edopro_folder(path):
            return cls()
        return cls(path, ready, ready and recommend_deck_scope(path, str(Path(path) / "pics")))


class WindowServices(Protocol):
    def navigate(self, key: str) -> bool: ...
    def confirm(self, title: str, message: str) -> bool: ...
    def choose_folder(self, initial: str) -> str | None: ...
    def sync_deck_files(self, game: Path, paths: tuple[Path, ...]) -> bool: ...


class AppContext(QObject):
    installation_changed = Signal(object)
    notice = Signal(str)
    busy_changed = Signal(bool)
    installations_requested = Signal()

    def __init__(
        self, cfg: Config, version: str, run_coro: RunCoro, apply_choices: Callable
    ) -> None:
        super().__init__()
        self.cfg, self.version = cfg, version
        self.run_coro, self.apply_choices = run_coro, apply_choices
        self.installation = Installation()
        self.tasks = TaskRunner(self)
        self.exit_code = 0
        self.busy = False
        self._host: WindowServices | None = None

    def attach(self, host: WindowServices) -> None:
        self._host = host

    def navigate(self, page_id: str) -> bool:
        return self._host is not None and self._host.navigate(page_id)

    def confirm(self, title: str, message: str) -> bool:
        return self._host is not None and self._host.confirm(title, message)

    def choose_folder(self, initial: str) -> str | None:
        return self._host.choose_folder(initial) if self._host else None

    def sync_deck_files(self, game: Path, paths: tuple[Path, ...]) -> bool:
        return self._host is not None and self._host.sync_deck_files(game, paths)

    def set_installation(self, installation: Installation) -> None:
        self.installation = installation
        if installation.path:
            self.cfg.set_edopro_path(installation.path)
        else:
            self.cfg.edopro_path = self.cfg.pics_path = ""
        self.installation_changed.emit(installation)

    def set_busy(self, value: bool) -> None:
        self.busy = value
        self.busy_changed.emit(value)

    def notify(self, message: str) -> None:
        self.notice.emit(message)
