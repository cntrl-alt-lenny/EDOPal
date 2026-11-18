"""The home, selection, and review screens in the backup page."""

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import backup
import config
from studio import theme as t
from studio.pages.backup_data import (
    CATEGORIES,
    COMPARE_GROUPS,
    PLATFORMS,
    RESTORE_GROUPS,
    SAVE_CATEGORIES,
    Mode,
    Review,
    Row,
    amount,
    date_text,
    is_interrupted,
    totals,
)
from studio.pages.backup_views import (
    ArrowFocus,
    CategoryChoice,
    GroupedList,
    ReviewList,
    action,
    actions,
    information,
    path_label,
    serif,
)
from studio.widgets import (
    ActionTile,
    Button,
    Hero,
    Notice,
    Panel,
    SegmentedControl,
    glyph,
    label,
    margins,
)

UNDO_DESCRIPTION = "Undo puts back the files and preferences this restore replaced and removes the copies it added."
POLICIES: tuple[backup.Policy, ...] = ("keep_mine", "take_theirs", "keep_both")


class Screen(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        margins(self.body, t.XXL)
        self.body.setSpacing(t.LG)
        self.focus: QWidget | None = None

    def heading(
        self,
        title: str,
        back: Callable[[], None] | None,
        path: str = "",
        *,
        detail: QLabel | None = None,
    ) -> None:
        if back:
            self.back = action("Back", back, "quiet", "back")
            self.focus = self.back
        header = QHBoxLayout()
        header.setSpacing(t.LG)
        if back:
            header.addWidget(self.back, 0, Qt.AlignmentFlag.AlignTop)
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        copy.addWidget(serif(title, t.DISPLAY - t.SM))
        if detail is not None:
            copy.addWidget(detail)
        elif path:
            copy.addWidget(path_label(path, Path(path).name))
        header.addLayout(copy, 1)
        self.body.addLayout(header)

    def banner(
        self, title: str, description: str, symbol: str, back: Callable[[], None] | None = None
    ) -> None:
        if back:
            button = action("Back", back, "quiet", "back")
            self.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            self.focus = button
        hero = Hero("Backup & transfer", title, description, symbol)
        hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body.addWidget(hero, 1)


class HomeScreen(Screen):
    def __init__(
        self,
        path: str,
        ready: bool,
        save: Callable[[], None],
        choose: Callable[[bool], None],
        change_folder: Callable[[], None],
        recovery: Callable[[backup.Transaction, Mode], None],
    ) -> None:
        super().__init__()
        self.has_installation = bool(path)
        self.ready = ready
        self.pending = False
        self.recovery = recovery
        self.hero = Hero(
            "Backup & transfer",
            "Take your decks",
            "Save your decks, replays and preferences to one ZIP you can move to another device.",
            "check",
            emphasis="with you.",
        )
        self.body.addWidget(self.hero)
        if path:
            self.save = ActionTile(
                "Save a backup", "Create a ZIP of your personal files.", "backup"
            )
            self.restore = ActionTile(
                "Restore", "Open a ZIP from this or another device.", "download"
            )
            self.compare = ActionTile(
                "Compare", "See what differs from a saved backup.", "coverage"
            )
            self.compare.setAccessibleName("Compare with a backup")
            row = QHBoxLayout()
            row.setSpacing(t.MD)
            tiles = (self.save, self.restore, self.compare)
            self.arrows = ArrowFocus(self, tiles, len(tiles))
            for tile in tiles:
                row.addWidget(tile, 1)
            self.body.addLayout(row)
            self.save.clicked.connect(save)
            self.restore.clicked.connect(lambda: choose(False))
            self.compare.clicked.connect(lambda: choose(True))
            self.save.setEnabled(False)
            self.restore.setEnabled(False)
            self.focus = self.compare
            if not ready:
                notice = Notice()
                notice.show_message(
                    "EDOPro is still setting up its card library. You can already back up or restore personal files here."
                )
                self.body.addWidget(notice)
            self.history = Panel(t.XL)
            self.history.body.addWidget(serif("Restore history", t.HEADING + t.XS))
            self.history.body.addWidget(label("Checking for previous restores…", "body", True))
            self.body.addWidget(self.history, 1)
        else:
            panel = information(
                "folder",
                "Choose your EDOPro folder",
                "Your backup and restore history belongs to the game folder you select.",
            )
            self.focus = action("Choose folder", change_folder, "primary", "folder")
            panel.body.addWidget(self.focus, 0, Qt.AlignmentFlag.AlignRight)
            self.body.addWidget(panel)

    def set_history(
        self, records: Sequence[backup.Transaction], error: str, retry: Callable[[], None]
    ) -> None:
        panel = self.history
        panel.body.takeAt(1).widget().deleteLater()
        pending = next((tx for tx in records if is_interrupted(tx)), None)
        self.pending = pending is not None
        can_write = not pending and not error
        self.save.setEnabled(can_write)
        self.restore.setEnabled(can_write)
        if pending:
            self.body.insertWidget(1, recovery_panel(pending, self.recovery))
        if error:
            panel.body.addWidget(label(error, "body", True))
            panel.body.addWidget(action("Retry", retry), 0, Qt.AlignmentFlag.AlignLeft)
            self.body.setStretchFactor(panel, 0)
            self.body.addStretch()
            return
        records = tuple(tx for tx in records if not is_interrupted(tx))
        if not records:
            row = QHBoxLayout()
            row.setSpacing(t.MD)
            row.addWidget(glyph("backup", t.ICON_BIG, t.MUTED))
            copy = QVBoxLayout()
            copy.setSpacing(t.XS)
            copy.addWidget(label("No completed restores for this installation.", "body", True))
            copy.addWidget(
                label("After you restore a backup, you can undo it here.", "muted", True)
            )
            row.addLayout(copy, 1)
            panel.body.addLayout(row)
            self.body.setStretchFactor(panel, 0)
            self.body.addStretch()
            return
        view = ReviewList("Restore history")
        view.setMinimumHeight(t.BUTTON_H + t.MD)
        view.replace(
            [
                Row(
                    "Restore completed" if tx.status == "complete" else "Restore undone",
                    date_text(tx.updated_at, with_time=True)
                    + (
                        f"  ·  {tx.files:,} {'file' if tx.files == 1 else 'files'}"
                        if tx.files is not None
                        else ""
                    ),
                )
                for tx in records
            ]
        )
        panel.body.addWidget(view, 1)
        undo = Button("Undo selected restore", "secondary", "back")
        panel.body.addWidget(undo, 0, Qt.AlignmentFlag.AlignLeft)
        undo.clicked.connect(lambda: self.recovery(records[view.currentIndex().row()], "rollback"))
        view.selectionModel().currentChanged.connect(
            lambda current, _previous: undo.setEnabled(
                can_write and current.isValid() and records[current.row()].status == "complete"
            )
        )
        view.setCurrentIndex(view.model().index(0, 0))


def recovery_panel(
    transaction: backup.Transaction, execute: Callable[[backup.Transaction, Mode], None]
) -> Panel:
    undoing = transaction.status == "rolling_back"
    panel = Panel(t.XL)
    row = QHBoxLayout()
    row.setSpacing(t.LG)
    row.addWidget(glyph("warning", t.ICON_BIG), 0, Qt.AlignmentFlag.AlignTop)
    copy = QVBoxLayout()
    copy.setSpacing(t.XS)
    copy.addWidget(
        serif("Undo was interrupted" if undoing else "Restore was interrupted", t.HEADING + t.XS)
    )
    text = (
        "Continue putting back replaced files and preferences and removing copies added by this restore."
        if undoing
        else "Finish applying the backup, or roll back to put back replaced files and preferences and remove copies already added."
    )
    copy.addWidget(label(text, "body", True))
    row.addLayout(copy, 1)
    rollback = action(
        "Continue undo" if undoing else "Roll back",
        lambda: execute(transaction, "rollback"),
        "primary" if undoing else "secondary",
        "back",
    )
    row.addWidget(rollback)
    if not undoing:
        row.addWidget(
            action("Finish restore", lambda: execute(transaction, "finish"), "primary", "arrow")
        )
    panel.body.addLayout(row)
    return panel


class SaveScreen(Screen):
    def __init__(
        self,
        items: dict[str, backup.Total],
        back: Callable[[], None],
        save: Callable[[tuple[str, ...]], None],
        selected: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.heading("Save a backup", back)
        self.body.addWidget(
            label(
                "Select what to put in the ZIP. Decks and replays are selected by default.",
                "body",
                True,
            ),
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        grid = QGridLayout()
        grid.setSpacing(t.MD)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.choices: dict[str, CategoryChoice] = {}
        for i, key in enumerate(SAVE_CATEGORIES):
            choice = CategoryChoice(key, items[key])
            choice.setChecked(key in selected)
            choice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            choice.setMinimumHeight(t.TILE_H)
            self.choices[key] = choice
            grid.addWidget(choice, i // 2, i % 2)
        self.arrows = ArrowFocus(self, tuple(self.choices.values()), 2)
        self.body.addLayout(grid, 1)
        footer = Panel(t.XL)
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        self.total = serif("", t.HEADING + t.XS)
        copy.addWidget(self.total)
        copy.addWidget(label("Selected items", "goldKicker"))
        row.addLayout(copy, 1)
        self.save = action(
            "Choose where to save", lambda: save(self.selected()), "primary", "backup"
        )
        row.addWidget(self.save)
        footer.body.addLayout(row)
        self.body.addWidget(footer)
        self.body.addWidget(
            label(
                "Card artwork, downloaded game content, device settings, replay notes and imported music stay on this device.",
                "muted",
                True,
            ),
            0,
            Qt.AlignmentFlag.AlignBottom,
        )

        def update() -> None:
            picked = self.selected()
            total = backup.Total(
                sum(items[k].count for k in picked), sum(items[k].bytes for k in picked)
            )
            self.total.setText(amount(total) if total.count else "Select a category with items")
            self.save.setEnabled(total.count > 0)

        for choice in self.choices.values():
            choice.toggled.connect(update)
        update()

    def selected(self) -> tuple[str, ...]:
        return tuple(key for key, choice in self.choices.items() if choice.isChecked())


class ArchiveScreen(Screen):
    def __init__(
        self,
        path: str,
        manifest: backup.Manifest,
        compare: bool,
        back: Callable[[], None],
        choose: Callable[[], None],
        review: Callable[[], None],
    ) -> None:
        super().__init__()
        self.heading("Compare with a backup" if compare else "Restore from a backup", back, path)
        source = PLATFORMS.get(manifest.source_platform, "Other device")
        items = totals(manifest)
        overall = backup.Total(
            sum(item.count for item in items.values()), sum(item.bytes for item in items.values())
        )
        info = Panel(t.XL)
        info.body.addWidget(glyph("backup", t.BRAND_ICON))
        info.body.addWidget(serif("Inside this backup", t.HEADING + t.XS))
        info.body.addWidget(serif(amount(overall), t.DISPLAY - t.SM))
        info.body.addStretch()
        info.body.addWidget(label("SAVED ON", "goldKicker"))
        info.body.addWidget(label(f"{source} · {date_text(manifest.created_at)}", "body", True))
        version = f"EDOPro {manifest.edopro_version}   ·   " if manifest.edopro_version else ""
        info.body.addWidget(
            label(f"{version}{config.APP_NAME} {manifest.app_version}", "muted", True)
        )
        included = Panel(t.XL)
        included.body.addWidget(serif("Included categories", t.HEADING + t.XS))
        included.body.addStretch()
        for key, total in items.items():
            title, _, symbol = CATEGORIES[key]
            panel = Panel(t.MD)
            panel.setStyleSheet(f"QFrame[role='panel'] {{ background: {t.RAISED}; }}")
            row = QHBoxLayout()
            row.setSpacing(t.MD)
            row.addWidget(glyph(symbol))
            row.addWidget(label(title, "body", True), 1)
            row.addWidget(label(amount(total), "small"))
            panel.body.addLayout(row)
            included.body.addWidget(panel)
        if not items:
            included.body.addWidget(label("This backup has no saved categories.", "body", True))
        included.body.addStretch()
        summary = QHBoxLayout()
        summary.setSpacing(t.LG)
        summary.addWidget(info, 2)
        summary.addWidget(included, 3)
        self.body.addLayout(summary, 1)
        self.review = action(
            "Compare files" if compare else "Preview changes",
            review,
            "primary",
            "arrow",
        )
        self.body.addWidget(
            actions(
                action("Choose another backup", choose),
                self.review,
                description="Comparison leaves both copies as they are."
                if compare
                else "Nothing has been changed yet. Preview what this backup can restore.",
            )
        )
        self.focus = self.review


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
                ("Keep mine", "Recommended. Keep the version here.", "check"),
                ("Use backup", "Replace the version here.", "download"),
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
            change = "1 change" if review.writes == 1 else f"{review.writes:,} changes"
            self.status.setText(f"Nothing has been changed yet. {change} ready to apply.")
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
