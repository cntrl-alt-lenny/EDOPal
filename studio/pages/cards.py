from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from artwork_flow import Summary, coverage_summary, make_run_config, sync_summary, valid_decks
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.card_results import ResultView
from studio.pages.card_views import OptionsView, SettingsView, page_layout, scroll
from studio.tasks import FolderRequest, Job
from studio.widgets import Hero, ProgressBlock, margins


class CardsPage(Page):
    id, title, icon = "cards", "HD Cards", "cards"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.job: Job | None = None
        self.summary: Summary | None = None
        self.total = self.done = 0
        self.folder_cancelled = False
        self.options = OptionsView()
        self.settings = SettingsView(context.cfg)
        self.results = ResultView()
        progress_view = QWidget()
        stage = page_layout(progress_view)
        stage.addStretch()
        self.banner = Hero(
            "HD Cards", "Artwork sync", "Updating the selected installation.", "download"
        )
        stage.addWidget(self.banner)
        self.progress = ProgressBlock()
        stage.addWidget(self.progress)
        stage.addStretch()
        self.stack = QStackedWidget()
        self.views = {
            key: scroll(widget)
            for key, widget in (
                ("options", self.options),
                ("settings", self.settings),
                ("progress", progress_view),
                ("results", self.results),
            )
        }
        for view in self.views.values():
            self.stack.addWidget(view)
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.addWidget(self.stack)
        self.results.close_button.clicked.connect(lambda: self.window().close())
        self.options.connect_game.clicked.connect(lambda: context.installations_requested.emit())
        self.options.start.clicked.connect(self.start_sync)
        self.options.settings.clicked.connect(lambda: self.open_view("settings"))
        for button in (self.options.coverage, self.settings.coverage):
            button.clicked.connect(self.show_coverage)
        for button in (self.settings.done, self.settings.back, self.results.done):
            button.clicked.connect(lambda: self.open_view("options"))
        self.progress.cancel.clicked.connect(self.cancel)
        self.options.scope.changed.connect(self._scope_status)
        self.installation_changed(context.installation)

    @property
    def state(self) -> str:
        return next(key for key, view in self.views.items() if view is self.stack.currentWidget())

    def open_view(self, key: str) -> None:
        self.stack.setCurrentWidget(self.views[key])
        self.views[key].verticalScrollBar().setValue(0)
        focus = {
            "settings": self.settings.back,
            "options": self.options.start,
            "results": self.results.done,
            "progress": self.progress.cancel,
        }[key]
        if focus.isEnabled():
            focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def installation_changed(self, installation: Installation) -> None:
        self.options.connect_game.setVisible(not installation.path)
        self.options.scope.setVisible(bool(installation.path))
        self.options.start.setEnabled(installation.ready)
        self.options.coverage.setEnabled(installation.ready)
        self.options.scope.setEnabled(installation.ready)
        self.settings.coverage.setEnabled(installation.ready)
        self.options.scope.select(int(installation.recommend_decks or self.ctx.cfg.my_decks))
        self._scope_status()
        if not self.job and self.state == "results":
            self.open_view("options")

    def _scope_status(self) -> None:
        install = self.ctx.installation
        if not install.path:
            message = "Choose a game folder above to enable artwork sync."
        elif not install.ready:
            message = "Open EDOPro once to download its card databases."
        elif install.recommend_decks and self.options.scope.group.checkedId() == 1:
            message = "My decks is recommended for this installation."
        else:
            message = "Existing artwork is kept unless you choose to refresh it."
        self.options.status.setText(message)

    @Slot()
    def start_sync(self) -> None:
        self._launch(False)

    @Slot()
    def show_coverage(self) -> None:
        self._launch(True)

    def sync_deck_files(self, game_path: Path, paths: tuple[Path, ...]) -> bool:
        """Schedule a validated, isolated deck-only sync. Errors appear inline."""
        if self.ctx.busy or not self.ctx.installation.ready or not self.ctx.navigate(self.id):
            return False
        if not paths:
            self.ctx.notify("Choose at least one saved deck.")
            return False
        return self._launch(False, game_path, tuple(paths))

    def _launch(
        self, stats_only: bool, game_path: Path | None = None, paths: tuple[Path, ...] = ()
    ) -> bool:
        if self.ctx.busy or not self.ctx.installation.ready:
            return False
        picks = {key: switch.isChecked() for key, switch in self.settings.toggles.items()}
        picks.update(my_decks=self.options.scope.group.checkedId() == 1, stats=stats_only)
        self.imported_run = bool(paths)
        self.run_config = cfg = make_run_config(self.ctx.cfg, picks, self.ctx.apply_choices, paths)
        cfg.update_checked_by_shell = True
        self.ctx.set_busy(True)
        self.ctx.exit_code = self.done = self.total = 0
        self.summary = None
        self.folder_cancelled = False
        scope = "Full library" if not cfg.my_decks else "My decks"
        if paths:
            scope = "Imported decks"
        self.banner.description.setText(
            "Reading artwork already on disk."
            if stats_only
            else f"{scope}  ·  {Path(cfg.edopro_path).name}"
        )
        self.progress.begin(
            "Measuring artwork coverage…" if stats_only else "Reading card databases…"
        )
        if paths:
            self.progress.detail.setText(f"Artwork for {len(paths)} imported deck(s)")
        self.open_view("progress")

        def prepare() -> None:
            if game_path is not None and not valid_decks(cfg.edopro_path, game_path, paths):
                raise ValueError("Choose saved decks in the selected installation’s deck folder.")

        self.job = self.ctx.tasks.sync(cfg, self.ctx.run_coro, prepare)
        self.job.progress.connect(self._event)
        self.job.result.connect(self._result)
        self.job.error.connect(self._error)
        self.job.finished.connect(self._finish)
        return True

    @Slot(object)
    def _event(self, event: tuple) -> None:
        kind = event[0]
        cancelling = self.job is not None and self.job.cancel_event.is_set()
        if kind == "task":
            self.total, self.done = event[3], 0
            if not cancelling:
                self.progress.phase.setText(event[2])
            self.progress.advance_to(self.done, self.total)
        elif kind == "adv":
            self.done += event[2]
            self.progress.advance_to(self.done, self.total)
        elif kind == "desc" and not cancelling:
            self.progress.detail.setText(event[2])
        elif kind == "notice":
            self.ctx.notify(event[1])
        elif kind == "coverage":
            self.summary = coverage_summary(event[1])
        elif kind == "folder":
            request: FolderRequest = event[1]
            path = None
            try:
                if not cancelling and not self.imported_run:
                    path = self.ctx.choose_folder(request.initial)
            finally:
                self.folder_cancelled = path is None
                request.answer(path)

    @Slot(object)
    def _result(self, stats: Any) -> None:
        if self.summary is None:
            self._make_summary({"stats": stats})

    @Slot(object)
    def _error(self, exc: BaseException) -> None:
        if isinstance(exc, SystemExit):
            self.ctx.exit_code = exc.code if isinstance(exc.code, int) else 1
            self._make_summary(
                {"exit": self.ctx.exit_code, "error": getattr(exc, "user_message", None)}
            )
        else:
            self.ctx.exit_code = 1
            self._make_summary({"error": f"{type(exc).__name__}: {exc}"})

    def _make_summary(self, payload: dict) -> None:
        self.summary = sync_summary(
            self.run_config,
            payload,
            self.job.cancel_event.is_set(),
            self.folder_cancelled,
            self.run_config.stats,
        )

    @Slot()
    def _finish(self) -> None:
        self.job = None
        cfg = self.run_config
        self.ctx.cfg.saved_original_text = cfg.saved_original_text
        self.ctx.cfg.era_art_applied = cfg.era_art_applied
        if cfg.edopro_path != self.ctx.installation.path:
            # The backend only adopts a replacement after validating its databases.
            self.ctx.set_installation(Installation(cfg.edopro_path, ready=True))
        self.ctx.set_busy(False)
        self.results.show_result(self.summary)
        self.open_view("results")

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.progress.cancel.setEnabled(False)
            self.progress.phase.setText("Finishing current work…")

    def hidden(self) -> bool:
        if self.job:
            self.ctx.notify("Wait for the current artwork job or cancel it first.")
            return False
        return True

    def request_close(self) -> bool:
        if self.job:
            self.cancel()
        return True

    def escape(self) -> bool:
        if self.job:
            self.cancel()
        elif self.state != "options":
            self.open_view("options")
        else:
            return False
        return True
