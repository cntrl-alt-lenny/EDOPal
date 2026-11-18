"""Progress, completion, and retry screens for the backup page."""

from collections.abc import Callable
from pathlib import Path

import backup
from studio import theme as t
from studio.pages.backup_data import Mode, error_text
from studio.pages.backup_jobs import WriteError
from studio.pages.backup_screens import UNDO_DESCRIPTION, Screen, recovery_panel
from studio.pages.backup_views import action, actions, path_label
from studio.widgets import Panel, ProgressBlock


class BusyScreen(Screen):
    def __init__(
        self,
        title: str,
        description: str,
        cancel: Callable[[], None],
        *,
        writing: bool,
    ) -> None:
        super().__init__()
        self.progress = ProgressBlock()
        if writing:
            self.banner(title, description, "backup")
            self.progress.begin("Preparing files…")
        else:
            self.heading(title, cancel)
            self.progress.begin(description)
        self.progress.cancel.clicked.connect(cancel)
        self.body.addWidget(self.progress)
        if writing:
            self.focus = self.progress.cancel
        else:
            self.body.addStretch()


class SavedScreen(Screen):
    def __init__(
        self,
        path: Path,
        manifest: backup.Manifest,
        back: Callable[[], None],
        reveal_folder: Callable[[], None],
    ) -> None:
        super().__init__()
        count = len(manifest.entries)
        items = "1 item" if count == 1 else f"{count:,} items"
        self.banner("Backup saved", f"Saved and checked {items}.", "check", back)
        panel = Panel(t.LG)
        name = path_label(str(path), path.name)
        name.setProperty("role", "subheading")
        panel.body.addWidget(name)
        panel.body.addWidget(path_label(str(path)))
        self.body.addWidget(panel)
        reveal = action("Open backup folder", reveal_folder, "primary", "folder")
        self.body.addWidget(
            actions(
                reveal,
                description="Copy this file to your other device, then choose Restore there.",
            )
        )
        self.focus = reveal


class RestoredScreen(Screen):
    def __init__(
        self,
        transaction: backup.Transaction,
        back: Callable[[], None],
        undo: Callable[[], None],
    ) -> None:
        super().__init__()
        complete = transaction.status == "complete"
        title = "Restore complete" if complete else "Previous files restored"
        self.banner(
            title, "The changes will be available the next time you start EDOPro.", "check", back
        )
        button = action(
            "Undo this restore" if complete else "Back to backups",
            undo if complete else back,
            "secondary" if complete else "primary",
            "back",
        )
        self.body.addWidget(
            actions(
                button,
                description=UNDO_DESCRIPTION
                if complete
                else "This restore is marked as undone in history.",
            )
        )
        self.focus = button


class ErrorScreen(Screen):
    def __init__(
        self,
        problem: WriteError,
        title: str,
        back: Callable[[], None],
        retry: Callable[[], None],
        retry_label: str,
        path: str,
        recovery: Callable[[backup.Transaction, Mode], None],
    ) -> None:
        super().__init__()
        title, description = error_text(problem.error, title)
        pending = problem.interrupted
        if pending:
            self.heading(title, back, path)
            self.body.addWidget(recovery_panel(pending, recovery))
            self.body.addStretch()
            if not isinstance(problem.error, backup.GameRunning):
                return
        else:
            self.banner(title, description, "warning", back)
            if path:
                self.body.addWidget(path_label(path))
        note = description if pending else ""
        if problem.history_unavailable:
            note = "Check restore history for interrupted work before trying again."
        retry_button = action(retry_label, retry, "primary")
        self.body.addWidget(actions(retry_button, description=note))
        self.focus = retry_button
