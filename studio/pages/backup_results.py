"""Progress, completion, and retry screens for the backup page."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt

import backup
import config
from studio import theme as t
from studio.pages.backup_data import Mode, error_text
from studio.pages.backup_jobs import WriteError
from studio.pages.backup_screens import UNDO_HELP, Screen, recovery_panel
from studio.pages.backup_views import UndoStatus, action, actions, path_label
from studio.pages.health_layout import row, technical_details
from studio.widgets import GameClosedBlock, Panel, ProgressBlock, label


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
            self.task(title, description)
            self.progress.begin("Preparing files…")
            self.progress.cancel.setText("Cancel backup operation")
        else:
            self.heading(title, cancel)
            self.progress.begin(description)
            self.progress.cancel.hide()
        self.progress.cancel.clicked.connect(cancel)
        self.body.addWidget(self.progress)
        if writing:
            self.focus = self.progress.cancel
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
        self.task("Backup saved", f"Saved and checked {items}.")
        panel = Panel(t.LG)
        name = path_label(str(path), path.name)
        name.setProperty("role", "subheading")
        panel.body.addWidget(name)
        panel.body.addWidget(path_label(str(path.parent)))
        self.body.addWidget(panel)
        reveal = action("Open backup folder", reveal_folder, "secondary", "folder")
        done = action("Done", back, "primary", "check")
        self.body.addWidget(
            actions(
                reveal,
                done,
                description="Copy this file to your other device using a USB drive or cloud storage. "
                f"Open {config.APP_NAME} on the other device, select your EDOPro folder, "
                "then choose Restore from backup.",
            )
        )
        self.body.addStretch()
        self.focus = done


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
        self.task(title, "The changes will be available the next time you start EDOPro.")
        done = action("Done", back, "primary", "check")
        self.undo = action("Undo this restore", undo, "secondary", "back")
        self.undo.hide()
        self.status = UndoStatus(
            "Checking undo availability…"
            if complete
            else "This restore is marked as undone in history.",
        )
        footer = Panel(t.LG)
        controls = row()
        controls.addWidget(self.status, 1)
        controls.addWidget(self.undo)
        controls.addWidget(done)
        footer.body.addLayout(controls)
        self.body.addWidget(footer)
        self.body.addStretch()
        self.focus = done

    def show_undo(self, preview: backup.UndoPreview) -> None:
        self.status.show_preview(preview, UNDO_HELP)
        self.undo.setVisible(preview.available)


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
        return_to: str,
    ) -> None:
        super().__init__()
        if isinstance(problem.error, backup.GameRunning):
            self.return_link(back, return_to)
            block = GameClosedBlock()
            self.body.addWidget(block)
            self.body.addStretch()
            block.retry.clicked.connect(retry)
            block.show_reason("EDOPro is open in the selected game folder.")
            self.focus = block.retry
            return
        title, description = error_text(problem.error, title)
        pending = problem.interrupted
        if pending:
            self.heading(title, back, path, return_to=return_to)
            self.body.addWidget(recovery_panel(pending, recovery))
            self.body.addStretch()
            return
        else:
            if isinstance(problem.error, backup.Cancelled) and not problem.history_unavailable:
                description = "Cancelled before any changes were published."
            self.task(title, description, back, return_to=return_to)
            if path:
                self.body.addWidget(path_label(path))
        if problem.history_unavailable:
            self.body.addWidget(
                label(
                    "Check restore history for interrupted work before trying again.", "body", True
                )
            )
        retry_button = action(retry_label, retry, "primary")
        self.body.addWidget(retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        if not isinstance(problem.error, backup.Cancelled):
            technical_details(self.body, str(problem.error))
        self.body.addStretch()
        self.focus = retry_button
