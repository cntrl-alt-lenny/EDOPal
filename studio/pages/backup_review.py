"""Conflict policies and file review for backup and restore."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout

import backup
from studio import theme as t
from studio.pages.backup_data import COMPARE_GROUPS, RESTORE_GROUPS, Review
from studio.pages.backup_screens import Screen
from studio.pages.backup_views import GroupedList, action, path_label
from studio.widgets import Notice, Panel, SegmentedControl, label, margins

POLICIES: tuple[backup.Policy, ...] = ("keep_mine", "take_theirs", "keep_both")


class PreviewScreen(Screen):
    def __init__(
        self,
        path: str,
        destination: str,
        back: Callable[[], None],
        choose_policy: Callable[[backup.Policy], None],
        restore: Callable[[], None],
    ) -> None:
        super().__init__()
        source, target = Path(path).name, Path(destination).name
        paths = path_label(f"{path}\n{destination}", f"{source}  →  Restore to {target}")
        self.heading("Restore preview", back, detail=paths)
        self.policy = SegmentedControl(
            [
                ("Keep current files", "Recommended", "check"),
                ("Replace with backup", "Use the backup version.", "download"),
                ("Keep both", "Add the backup as a separate copy.", "library"),
            ]
        )
        self.policy.setAccessibleName("When versions differ")
        self.policy.changed.connect(lambda index: choose_policy(POLICIES[index]))
        self.notice = Notice()
        self.body.addWidget(self.policy)
        self.body.addWidget(self.notice)
        self.grouped = GroupedList(
            RESTORE_GROUPS,
            {
                "add": "There are no new items to add.",
                "conflict": "There are no conflicts to resolve.",
                "identical": "There are no identical items.",
            },
        )
        self.body.addWidget(self.grouped, 1)
        self.restore = action("Restore to this installation", restore, "primary", "download")
        self.status = label("", "body", True)
        footer = Panel(t.LG)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.restore)
        footer.body.addLayout(row)
        self.body.addWidget(footer)
        self.set_loading()

    def compact(self, small: bool) -> None:
        self.grouped.compact(small)
        for index, button in enumerate(self.policy.buttons):
            button.hint.setVisible(not small or index == 0)
            button.setMinimumHeight(t.BUTTON_H if small else t.TILE_H)
            margins(button.layout(), t.XS if small else t.LG)
        self.policy.buttons[0].setToolTip("Recommended for conflicts.")

    def set_loading(self) -> None:
        self.status.setText("Nothing has been changed yet. Checking files…")
        self.restore.setEnabled(False)
        self.grouped.hide()
        self.notice.show_message("")

    def show_review(self, review: Review) -> None:
        messages = []
        if review.case_clashes:
            messages.append(
                "Names that differ in letter case can match on Windows and be separate on Steam Deck."
            )
        if review.preference_conflicts and self.policy.group.checkedId() == 2:
            messages.append("Conflicting game preferences are saved separately.")
        self.notice.show_message(" ".join(messages))
        selected = next((key for key, tab in self.grouped.tabs.items() if tab.isChecked()), None)
        self.grouped.set_groups(review.groups, selected)
        self.grouped.show()
        self.policy.setVisible(bool(review.groups["conflict"]))
        if review.writes:
            self.status.setText(
                "Review the proposed changes before restoring. Nothing has been changed yet."
            )
        else:
            self.status.setText(
                "Nothing has been changed yet. This choice has no changes to apply."
            )
        self.restore.setEnabled(bool(review.writes))


class CompareScreen(Screen):
    def __init__(self, path: str, review: Review, back: Callable[[], None]) -> None:
        super().__init__()
        self.heading("Backup comparison", back, path)
        self.body.addWidget(
            label(
                "This installation and the backup were compared without changing either.",
                "body",
                True,
            )
        )
        if review.case_clashes:
            notice = Notice()
            notice.show_message(
                "Names that differ only by spelling or letter case can refer to the same file on Windows."
            )
            self.body.addWidget(notice)
        self.grouped = GroupedList(
            COMPARE_GROUPS, dict.fromkeys(COMPARE_GROUPS, "There are no items in this group.")
        )
        self.grouped.set_groups(review.groups)
        self.body.addWidget(self.grouped, 1)

    def compact(self, small: bool) -> None:
        self.grouped.compact(small)
