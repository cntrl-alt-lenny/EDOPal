"""Page services and the selected installation shared throughout the window."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, QSettings, Signal, Slot

import config
from artwork_flow import recommend_deck_scope
from config import Config
from studio.tasks import RunCoro, TaskRunner


class GameOpenError(RuntimeError):
    """A write needs another game-process check before it can proceed."""


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
    select_installation_requested = Signal(str)
    preferences_changed = Signal()
    update_changed = Signal()
    update_requested = Signal()

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
        self.state = QSettings(
            str(Path(cfg.config_path).with_suffix(".studio.ini")), QSettings.Format.IniFormat
        )
        self.latest = ""
        self.update_status = ""
        self.checking_update = False
        self._host: WindowServices | None = None

    def attach(self, host: WindowServices) -> None:
        self._host = host

    @Slot()
    def detach(self) -> None:
        self._host = None
        self.tasks.abandon_all()

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
            self.state.remove("games/chooseOnStart")
            known = self.known_installations()
            if Path(installation.path) not in map(Path, known):
                self.state.setValue("games/known", [*known, installation.path])
        else:
            self.cfg.edopro_path = self.cfg.pics_path = ""
        self.installation_changed.emit(installation)

    def known_installations(self) -> list[str]:
        return self.state.value("games/known", [], type=list)

    def save_card_preferences(
        self, values: dict[str, bool | str], done: Callable[[], None]
    ) -> None:
        if self.busy:
            return
        self.set_busy(True)
        path = self.cfg.config_path
        job = self.tasks.start(lambda _: config.save_settings(path, values))

        def saved(success: bool) -> None:
            self.set_busy(False)
            if success:
                for key, value in values.items():
                    setattr(self.cfg, key, value)
                self.cfg.saved_original_text = self.cfg.original_text
                self.preferences_changed.emit()
                done()
            else:
                self.notify(
                    "Could not save these defaults. Check that your settings folder is writable, then retry."
                )

        job.result.connect(saved)

        def failed(exc: Exception) -> None:
            self.set_busy(False)
            self.notify(str(exc))

        job.error.connect(failed)

    @staticmethod
    def require_game_closed(game: Path) -> None:
        """Call inside the write worker, immediately before touching the game."""
        from music.settings import _game_running

        try:
            running = _game_running(game)
        except OSError as exc:
            raise GameOpenError(
                "EDOPal couldn’t check whether EDOPro is open. Close it, then choose Retry."
            ) from exc
        if running:
            raise GameOpenError("EDOPro is open. Close this copy of EDOPro, then choose Retry.")

    def set_busy(self, value: bool) -> None:
        self.busy = value
        self.busy_changed.emit(value)

    def notify(self, message: str) -> None:
        self.notice.emit(message)
