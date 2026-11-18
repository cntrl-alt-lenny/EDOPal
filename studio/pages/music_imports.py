"""Inline import planning, review, transfer and per-file outcomes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QLineEdit,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

import music_import
import music_library as engine
from music.importing import ImportItem, ImportResult
from studio import theme as t
from studio.tasks import WorkerContext
from studio.widgets import ActionTile, ElidedLabel, Panel, label

from .music_model import SCENES, match_description, needs_game
from .music_receipt import Receipt
from .music_widgets import RecordHero, action, cells, row, table

if TYPE_CHECKING:
    from .music import MusicPage

STATUS = {
    "imported": "Added",
    "duplicate": "Already in library",
    "unsupported": "Unsupported",
    "failed": "Couldn’t add",
    "cancelled": "Skipped on cancellation",
}


class ChoiceDelegate(QStyledItemDelegate):
    """Only the active cell needs a picker, even for a large folder."""

    def __init__(self, choices: Sequence[tuple[str, str | None]], parent: QWidget) -> None:
        super().__init__(parent)
        self.choices = choices

    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QComboBox:
        combo = QComboBox(parent)
        combo.setAccessibleName(index.model().headerData(index.column(), Qt.Orientation.Horizontal))
        for title, value in self.choices:
            combo.addItem(title, value)
        combo.activated.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor: QComboBox, index: QModelIndex) -> None:
        editor.setCurrentIndex(max(0, editor.findData(index.data(Qt.ItemDataRole.UserRole))))

    def setModelData(
        self, editor: QComboBox, model: QAbstractItemModel, index: QModelIndex
    ) -> None:
        model.setData(index, editor.currentData(), Qt.ItemDataRole.UserRole)
        model.setData(index, editor.currentText(), Qt.ItemDataRole.DisplayRole)


class ImportViews:
    def __init__(self, page: MusicPage) -> None:
        self.page = page
        self.receipts: list[Receipt] = []
        self.reset()

    def reset(self) -> None:
        self.plan: list[ImportItem] = []
        self.results: list[ImportResult] = []
        self.result_receipt: Receipt | None = None
        self.target = ""
        self.manage_back = self.page.home

    def open_tools(self, back: Callable) -> None:
        self.manage_back = back
        self.manage()

    def manage(self) -> None:
        p = self.page
        layout = p.screen("manage", self.manage_back, "Add music", "YOUR LIBRARY")
        p.feedback(layout)
        actions = [
            (
                "Add a folder",
                "Review recordings and choose their games and scenes.",
                "folder",
                self.folder,
            ),
            (
                "Create a game",
                "Give your own soundtrack a place in the library.",
                "library",
                self.new_game,
            ),
            (
                "Import library",
                "Bring in a previously exported collection.",
                "download",
                partial(self.transfer, "import"),
            ),
            (
                "Export library",
                "Copy your games and recordings to a folder.",
                "arrow",
                partial(self.transfer, "export"),
            ),
            (
                "Power of Chaos",
                "Extract music from a game you own.",
                "cards",
                partial(self.transfer, "chaos"),
            ),
            ("Add files", "Choose an album for individual recordings.", "music", p.albums.gallery),
        ]
        grid = QGridLayout()
        grid.setSpacing(t.MD)
        self.tools = {}
        for i, (name, hint, symbol, callback) in enumerate(actions):
            tile = ActionTile(name, hint, symbol)
            tile.clicked.connect(callback)
            grid.addWidget(tile, i // 2, i % 2)
            self.tools[name] = tile
        layout.addLayout(grid, 1)
        self.undo_button(layout)
        packs = action("Get more music", p.packs.open, icon="download")
        layout.addWidget(packs, 0, Qt.AlignmentFlag.AlignRight)
        self.tools["Add a folder"].setFocus()

    def new_game(self) -> None:
        p = self.page
        layout = p.screen("create", self.manage, "Create a game", "YOUR LIBRARY")
        hero = RecordHero(
            "Personal collection",
            "Your new album",
            "Add recordings to this game after you create it.",
            "music",
        )
        hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(hero, 1)
        panel = Panel()
        panel.body.addWidget(label("Game name", "subheading"))
        self.name = QLineEdit()
        self.name.setAccessibleName("Game name")
        self.name.setPlaceholderText("Name your game or collection")
        panel.body.addWidget(self.name)
        self.create = action("Create game", self.create_game, "primary")
        self.create.setEnabled(False)
        self.name.textChanged.connect(lambda text: self.create.setEnabled(bool(text.strip())))
        self.name.textChanged.connect(
            lambda text: hero.headline.setText(
                hero.headline.fontMetrics().elidedText(
                    text.strip() or "Your new album",
                    Qt.TextElideMode.ElideRight,
                    max(t.TILE_SIZE[0], hero.headline.width()),
                )
            )
        )
        self.name.returnPressed.connect(self.create.click)
        panel.body.addWidget(
            label(
                "Find this album in the Personal series. You can undo its creation in Add music.",
                "muted",
                True,
            )
        )
        panel.body.addWidget(self.create, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(panel)
        self.name.setFocus()

    def create_game(self) -> None:
        p, title, root = self.page, self.name.text().strip(), self.page.root
        receipt = Receipt(root, "game creation")

        def work(_: WorkerContext) -> str:
            game = engine.create_game(title, root=root)
            receipt.record(root / game.id / "game.json")
            return game.id

        def done(key: str) -> None:
            self.receipts.append(receipt)
            p.refresh(partial(p.albums.open_game, key, back=self.manage))

        p.run(
            "Creating your game", work, done, lock=True, cancellable=False, fallback=self.new_game
        )

    def folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self.page, "Add a music folder")
        if selected:
            self.plan_paths([Path(selected)])

    def files(self, key: str) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self.page,
            "Add recordings to this album",
            "",
            "Audio (*.flac *.ogg *.mp3 *.wav);;All files (*)",
        )
        if selected:
            self.plan_paths([Path(path) for path in selected], key)

    def plan_paths(self, paths: list[Path], key: str = "") -> None:
        from .music import checkpoint

        p, root = self.page, self.page.root
        self.target = key

        def read(worker: WorkerContext) -> list[ImportItem]:
            return music_import.plan_import(paths, root=root, checkpoint=lambda: checkpoint(worker))

        def done(plan: list[ImportItem]) -> None:
            self.plan = [replace(x, game=key) for x in plan] if key else plan
            self.review()

        p.run("Finding recordings", read, done, fallback=p.albums.album if key else self.manage)

    def review(self) -> None:
        p = self.page
        layout = p.screen(
            "review",
            p.albums.album if self.target else self.manage,
            "Review recordings",
            "ADD MUSIC",
        )
        layout.addWidget(
            label(
                "Choose a game and a suggested scene for each recording. You can use it in any scene later.",
                "body",
                True,
            )
        )
        self.review_table = table(("File", "Game", "Scene", "Match"), "Import assignments")
        view = self.review_table
        games = sorted(
            ((a.game.title, a.game.id) for a in p.data.albums.values()),
            key=lambda v: v[0].casefold(),
        )
        view.setItemDelegateForColumn(1, ChoiceDelegate([("Choose game…", None), *games], view))
        view.setItemDelegateForColumn(
            2, ChoiceDelegate([(v[0], k) for k, v in SCENES.items()], view)
        )
        view.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.DoubleClicked
        )
        view.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for item in self.plan:
            album = p.data.albums.get(item.game)
            match = match_description(item, bool(self.target))
            i = cells(
                view,
                (
                    item.path.name,
                    album.game.title if album else "Choose game…",
                    SCENES[item.scene][0],
                    match,
                ),
            )
            file = view.item(i, 0)
            file.setToolTip(str(item.path))
            file.setFlags(
                (file.flags() & ~Qt.ItemFlag.ItemIsEditable) | Qt.ItemFlag.ItemIsUserCheckable
            )
            file.setCheckState(Qt.CheckState.Checked)
            view.item(i, 1).setData(Qt.ItemDataRole.UserRole, item.game)
            view.item(i, 2).setData(Qt.ItemDataRole.UserRole, item.scene)
            note = view.item(i, 3)
            note.setFlags(note.flags() & ~Qt.ItemFlag.ItemIsEditable)
            note.setToolTip(item.error or match)
        layout.addWidget(view, 1)
        self.hint = label("", "body", True)
        self.confirm = action("Add selected files", self.commit, "primary", "download")
        layout.addLayout(row(self.hint, self.confirm))
        view.itemChanged.connect(self.validate)
        view.cellClicked.connect(
            lambda r, c: view.editItem(view.item(r, c)) if c in (1, 2) else None
        )
        self.validate()
        view.setFocus()

    def choices(self) -> list[ImportItem]:
        view = self.review_table
        return [
            replace(
                item,
                game=view.item(i, 1).data(Qt.ItemDataRole.UserRole),
                scene=view.item(i, 2).data(Qt.ItemDataRole.UserRole),
            )
            for i, item in enumerate(self.plan)
            if view.item(i, 0).checkState() == Qt.CheckState.Checked
        ]

    def validate(self) -> None:
        plan = self.choices()
        missing = sum(needs_game(x, bool(self.target)) for x in plan)
        if missing:
            message = f"Files needing a game: {missing}. Choose a game or uncheck those files."
        elif not self.plan:
            message = "The folder contains no recordings."
        else:
            message = f"{len(plan)} of {len(self.plan)} files selected"
        self.hint.setText(message)
        self.confirm.setEnabled(bool(plan) and not missing)

    def commit(self) -> None:
        p, plan, root = self.page, self.choices(), self.page.root
        dedup = not self.target
        receipt = Receipt(root, "import")

        def work(worker: WorkerContext) -> list[ImportResult]:
            results = music_import.execute_import(
                plan,
                root=root,
                cancelled=worker.cancel.is_set,
                progress=lambda n, total: worker.progress((n, total)),
                dedup_library=dedup,
            )
            for item, result in zip(plan, results, strict=False):
                if result.status == "imported":
                    receipt.files[result.destination] = item.digest
            results.extend(ImportResult(x.path, "cancelled") for x in plan[len(results) :])
            return results

        def done(results: list[ImportResult]) -> None:
            self.results = results
            self.result_receipt = receipt if receipt.files else None
            if receipt.files:
                self.receipts.append(receipt)
            p.refresh(self.outcomes)

        p.run("Adding recordings", work, done, lock=True, fallback=self.review)

    def outcomes(self) -> None:
        p = self.page
        layout = p.screen("results", self.manage, "Import results", "YOUR LIBRARY")
        counts = Counter(r.status for r in self.results)
        layout.addWidget(
            label(
                "   ·   ".join(f"{name}  {counts[k]}" for k, name in STATUS.items() if counts[k]),
                "body",
                True,
            )
        )
        view = table(("File", "Result", "Details"), "Results by file")
        for result in self.results:
            i = cells(
                view,
                (
                    result.path.name,
                    STATUS[result.status],
                    result.reason or (result.destination.name if result.destination else ""),
                ),
            )
            view.item(i, 0).setToolTip(str(result.path))
        layout.addWidget(view, 1)
        if self.result_receipt:
            self.undo_button(layout)
        done = action("Browse games", p.albums.gallery, "primary", "library")
        layout.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        done.setFocus()

    def transfer(self, kind: str) -> None:
        p = self.page
        titles = {
            "import": "Import library",
            "export": "Export library",
            "chaos": "Import from Power of Chaos",
        }
        if kind == "chaos":
            choice, _ = QFileDialog.getOpenFileName(
                p, "Select original game audio", "", "Game audio (Voice.dat *.dat *.zip)"
            )
        else:
            caption = (
                "Choose an exported soundtrack collection"
                if kind == "import"
                else "Choose a folder for the copy"
            )
            choice = QFileDialog.getExistingDirectory(p, caption)
        if not choice:
            return
        path, root = Path(choice), p.root
        receipt = Receipt(
            path if kind == "export" else root,
            {"import": "library import", "export": "export", "chaos": "Power of Chaos import"}[
                kind
            ],
        )
        transfer = {
            "import": music_import.import_library,
            "export": music_import.export_library,
            "chaos": music_import.import_power_of_chaos,
        }[kind]

        def work(worker: WorkerContext) -> str:
            from .music import Stopped, checkpoint

            def update(done: int, total: int) -> None:
                worker.progress((done, total))
                if done < total:
                    checkpoint(worker)

            try:
                added, duplicates = transfer(
                    path,
                    root=root,
                    progress=update,
                    created=receipt.record,
                    checkpoint=lambda: checkpoint(worker),
                )
                return f"{titles[kind]} complete. {added} recordings copied, {duplicates} already present."
            except Stopped:
                return f"Stopped. Kept {len(receipt.files)} newly copied files."
            except (OSError, ValueError) as exc:
                return f"{titles[kind]} could not finish. {exc}"

        def done(message: str) -> None:
            if receipt.files:
                self.receipts.append(receipt)
            p.message = message
            p.refresh(self.manage)

        p.run(titles[kind], work, done, lock=True, destination=receipt.root, fallback=self.manage)

    def undo_button(self, layout: QVBoxLayout) -> None:
        if not self.receipts:
            return
        receipt = self.receipts[-1]
        btn = action(f"Undo {receipt.title}", partial(self.undo, receipt))
        location = ElidedLabel()
        location.set_value(str(receipt.root))
        layout.addLayout(
            row(
                label(
                    f"Undo removes only newly added files ({len(receipt.files)}).",
                    "muted",
                    True,
                ),
                location,
                btn,
            )
        )

    def undo_creation(self, layout: QVBoxLayout, key: str) -> None:
        if self.receipts and self.page.root / key / "game.json" in self.receipts[-1].files:
            self.undo_button(layout)

    def undo(self, receipt: Receipt) -> None:
        from .music import checkpoint

        p = self.page
        if p.ctx.confirm(
            "Undo this library change?",
            f"Remove the {len(receipt.files)} newly added files from “{receipt.title}”? Existing recordings are kept.",
        ):

            def done(_: object) -> None:
                self.receipts.remove(receipt)
                p.message = "Library change undone."
                p.refresh(self.manage)

            p.run(
                "Undoing library changes",
                lambda worker: receipt.undo(lambda: checkpoint(worker)),
                done,
                lock=True,
                destination=receipt.root,
                fallback=partial(p.refresh, self.manage),
            )
