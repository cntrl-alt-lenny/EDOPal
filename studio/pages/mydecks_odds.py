"""Tag the saved Main Deck and recalculate exact opening-hand odds."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSignalBlocker,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListView,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from decks import Preview
from decks.history import GROUP_NAME_LIMIT, DeckStore, TagGroup, Track
from decks.odds import opening_combo, opening_distribution
from studio import theme as t
from studio.pages.mydecks_logic import card_name, quantity
from studio.pages.mydecks_widgets import Screen, button, choice
from studio.tasks import Job, TaskRunner
from studio.widgets import Panel, SearchField, label, margins

TagEdits = dict[tuple[int, int], bool]
TagRows = tuple[tuple[int, str], ...]


class TagCards(QAbstractListModel):
    tagged = Signal(int, bool)

    def __init__(self, parent: QListView) -> None:
        super().__init__(parent)
        self.rows: TagRows = ()
        self.checked: frozenset[int] = frozenset()

    def set_cards(self, rows: TagRows) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def set_checked(self, checked: frozenset[int]) -> None:
        if checked == self.checked:
            return
        self.checked = checked
        if self.rows:
            self.dataChanged.emit(
                self.index(0), self.index(len(self.rows) - 1), [Qt.ItemDataRole.CheckStateRole]
            )

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        code, name = self.rows[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if code in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return code
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return name
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsUserCheckable if index.isValid() else flags

    def setData(
        self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.CheckStateRole:
            return False
        code = self.rows[index.row()][0]
        checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        self.checked = self.checked | {code} if checked else self.checked - {code}
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.tagged.emit(code, checked)
        return True


class OddsScreen(Screen):
    def __init__(
        self,
        track: Track,
        deck: Preview,
        store: DeckStore,
        tasks: TaskRunner,
        back: Callable[[], object],
        confirm: Callable[[str, str], bool],
    ) -> None:
        super().__init__("Opening-hand odds", back)
        self.body.setSpacing(t.XS)
        self.body.setContentsMargins(t.XXL, t.MD, t.XXL, t.MD)
        self.track, self.deck, self.store, self.tasks = track, deck, store, tasks
        self.confirm = confirm
        self.groups: dict[int, TagGroup] = {}
        self.main: Counter[int] = Counter()
        self.pending: TagEdits = {}
        self.writer: Job | None = None
        self.blocked = False
        self.live = True
        self.body.addWidget(label(track.path.stem, "subheading", True))
        self.body.addWidget(
            label(
                "Random 5-card or 6-card hand from Main. Searching and drawing effects aren’t modelled.",
                "muted",
                True,
            )
        )
        group_controls = QHBoxLayout()
        self.group = choice("Group to count", ())
        self.group.setMinimumWidth(t.TILE_LARGE)
        group_controls.addWidget(self.group, 1)
        self.remove = button("Remove group", self._remove_group, "quiet")
        group_controls.addWidget(self.remove)

        self.add_row = QWidget()
        form = QHBoxLayout(self.add_row)
        margins(form, 0)
        self.name = QLineEdit()
        self.name.setAccessibleName("New group name")
        self.name.setPlaceholderText("New group name")
        self.name.setMaxLength(GROUP_NAME_LIMIT)
        form.addWidget(self.name, 1)
        self.add = button("Add group", self._add_group, "primary")
        form.addWidget(self.add)
        self.workspace = QWidget()
        panels = QHBoxLayout(self.workspace)
        margins(panels, 0)
        panels.setSpacing(t.MD)
        cards = Panel(t.MD)
        cards.body.addWidget(label("Main Deck cards", "subheading"))
        cards.body.addLayout(group_controls)
        self.search = SearchField("Find a Main Deck card")
        cards.body.addWidget(self.search)
        self.cards = QListView()
        self.cards.setObjectName("installList")
        self.cards.setAccessibleName("Main Deck cards. Space toggles the tag.")
        self.cards.setMinimumHeight(t.TILE_H)
        self.cards.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.cards.setUniformItemSizes(True)
        self.model = TagCards(self.cards)
        self.filter = QSortFilterProxyModel(self.cards)
        self.filter.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.filter.setSourceModel(self.model)
        self.cards.setModel(self.filter)
        cards.body.addWidget(self.cards, 1)
        self.marked = label("", "muted", True)
        cards.body.addWidget(self.marked)
        panels.addWidget(cards, 3)

        results = Panel(t.MD)
        results.body.addWidget(label("Chance of opening", "subheading"))
        controls = QHBoxLayout()
        controls.addWidget(label("Exactly", "body"))
        self.hits = QSpinBox()
        self.hits.setAccessibleName("Exact number of cards from the group")
        self.hits.setRange(0, 6)
        self.hits.setValue(1)
        self.hits.setMinimumHeight(t.BUTTON_H)
        controls.addWidget(self.hits)
        self.avoid = choice("Combination. Group to avoid", ())
        controls.addWidget(self.avoid, 1)
        results.body.addLayout(controls)
        self.table = QTableWidget(4, 3)
        self.table.setAccessibleName("Opening-hand probabilities for five and six cards")
        self.table.setHorizontalHeaderLabels(("Outcome", "First · 5", "Second · 6"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(t.XXL)
        self.table.setStyleSheet(f"""
            QTableWidget {{ background: {t.PANEL}; alternate-background-color: {t.RAISED}; border: 0; }}
            QHeaderView::section {{ background: {t.RAIL}; color: {t.MUTED};
                padding: {t.XS}px; border: 0; }}
        """)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, t.HUGE + t.XXL)
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height() + 4 * t.XXL)
        results.body.addWidget(self.table)
        results.body.addStretch()
        panels.addWidget(results, 4)
        self.body.addWidget(self.workspace, 1)
        self.status = label("Reading card groups…", "body", True)
        self.body.addWidget(self.status)
        self.retry = button("Retry", self._retry, "primary")
        self.retry.hide()
        self.body.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.addWidget(self.add_row)
        self.empty_space = QWidget()
        self.body.addWidget(self.empty_space, 1)
        self.group.currentIndexChanged.connect(self._selected)
        self.avoid.currentIndexChanged.connect(self._calculate)
        self.hits.valueChanged.connect(self._calculate)
        self.model.tagged.connect(self._tag)
        self.search.textChanged.connect(self.filter.setFilterFixedString)
        self.name.textChanged.connect(self._controls)
        self.name.returnPressed.connect(self.add.click)
        self.initial = self.name
        self._load()

    @property
    def saving(self) -> bool:
        return self.writer is not None

    def leave(self) -> None:
        self.live = False
        self.read.cancel()

    def _load(self) -> None:
        self.ready = False
        self.retry.hide()
        self._controls()
        self._calculate()
        store, track, deck = self.store, self.track, self.deck

        def work() -> tuple[tuple[TagGroup, ...], Counter[int], TagRows]:
            main = Counter(deck.sections.main)
            rows = tuple(
                (code, f"{main[code]} × {name}")
                for name, code in sorted(
                    ((card_name(deck, code), code) for code in main),
                    key=lambda pair: pair[0].casefold(),
                )
            )
            return store.groups(track), main, rows

        self.read = self.tasks.start(lambda _: work())
        self.read.result.connect(self._loaded)
        self.read.error.connect(self._read_failed)

    def _loaded(self, result: tuple[tuple[TagGroup, ...], Counter[int], TagRows]) -> None:
        if self.live:
            groups, self.main, rows = result
            self.model.set_cards(rows)
            self.ready = True
            self._set_groups(groups)
            self.status.setText(
                "Choose a group and tag its cards."
                if groups
                else "Name a group, then tag its cards."
            )

    def _read_failed(self, _: Exception) -> None:
        if self.live:
            self.status.setText("Couldn’t read the saved card groups.")
            self.retry.show()
            self.add_row.hide()

    def _set_groups(self, groups: tuple[TagGroup, ...], name: str = "") -> None:
        selected, avoided = self.group.currentData(), self.avoid.currentData()
        self.groups = {group.id: group for group in groups}
        for (group, code), checked in self.pending.items():
            self._set_local_tag(group, code, checked)
        with QSignalBlocker(self.group), QSignalBlocker(self.avoid):
            self.group.clear()
            self.avoid.clear()
            self.avoid.addItem("Combination · Choose a group to avoid", None)
            for group in self.groups.values():
                self.group.addItem(group.name, group.id)
                self.avoid.addItem(f"Avoid {group.name}", group.id)
                if group.name == name:
                    selected = group.id
            self.group.setCurrentIndex(max(0, self.group.findData(selected)))
            self.avoid.setCurrentIndex(max(0, self.avoid.findData(avoided)))
        self._selected()

    def _selected(self) -> None:
        group = self.groups.get(self.group.currentData())
        self.model.set_checked(group.cards if group else frozenset())
        self.cards.setEnabled(bool(group))
        self._controls()
        self._calculate()

    def _controls(self) -> None:
        enabled = self.ready and not self.saving and not self.blocked
        self.add.setEnabled(enabled and bool(self.name.text().strip()))
        self.name.setEnabled(enabled)
        self.remove.setEnabled(enabled and self.group.currentData() is not None)
        self.group.setEnabled(bool(self.groups))
        self.search.setEnabled(bool(self.groups) and bool(self.main))
        self.workspace.setVisible(bool(self.groups))
        self.empty_space.setVisible(not self.groups)
        self.add_row.setVisible(self.ready and not self.blocked)

    def _calculate(self) -> None:
        group = self.groups.get(self.group.currentData())
        avoid = self.groups.get(self.avoid.currentData())
        n = self.hits.value()
        combination = f"At least one, no {avoid.name}" if avoid else "At least one, none avoided"
        for i, text in enumerate(("At least one", f"Exactly {n}", "None", combination)):
            self.table.setItem(i, 0, self._cell(text))
        tagged = sum(self.main[code] for code in group.cards) if group else 0
        absent = len(group.cards - self.main.keys()) if group else 0
        self.marked.setText(
            (
                f"{tagged} tagged {'copy' if tagged == 1 else 'copies'} in Main"
                + (f" · {quantity(absent, 'tag')} kept for absent cards" if absent else "")
            )
            if group
            else "Create a group to start tagging cards."
        )
        for column, size in enumerate((5, 6), 1):
            distribution = opening_distribution(self.main, group.cards, size) if group else ()
            values = (None, None, None, None)
            if distribution:
                values = (
                    1 - distribution[0],
                    distribution[n] if n <= size else Fraction(0),
                    distribution[0],
                    opening_combo(self.main, group.cards, avoid.cards, size) if avoid else None,
                )
            for row, value in enumerate(values):
                item = self._cell(f"{float(value):.2%}" if value is not None else "—", value)
                if value is None and group and not distribution:
                    item.setToolTip(f"The Main Deck has fewer than {size} cards.")
                self.table.setItem(row, column, item)

    @staticmethod
    def _cell(text: str, value: Fraction | None = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setToolTip(f"Exact probability {value}" if value is not None else text)
        if value is not None:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QColor(t.PALE))
        return item

    def _set_local_tag(self, group: int, code: int, checked: bool) -> None:
        if current := self.groups.get(group):
            cards = current.cards | {code} if checked else current.cards - {code}
            self.groups[group] = replace(current, cards=cards)

    def _tag(self, code: int, checked: bool) -> None:
        ident = self.group.currentData()
        if ident is None:
            return
        self._set_local_tag(ident, code, checked)
        self.pending[ident, code] = checked
        self._calculate()
        self._flush()

    def _flush(self) -> None:
        if self.saving or self.blocked or not self.pending:
            return
        batch, self.pending = self.pending, {}
        store, track = self.store, self.track
        self._write(lambda: store.set_tags(track, batch), edits=batch)

    def _add_group(self) -> None:
        name = " ".join(self.name.text().split())
        store, track = self.store, self.track
        self._write(lambda: store.add_group(track, name), name=name)

    def _remove_group(self) -> None:
        group = self.groups[self.group.currentData()]
        if self.confirm("Remove card group", f"Remove the group “{group.name}” and its tags?"):
            store, track = self.store, self.track
            self._write(lambda: store.remove_group(track, group.id))

    def _write(
        self,
        work: Callable[[], tuple[TagGroup, ...]],
        *,
        name: str = "",
        edits: TagEdits | None = None,
    ) -> None:
        self.status.setText("Saving card groups…")
        self.writer = self.tasks.start(lambda _: work())
        self._controls()

        def saved(groups: tuple[TagGroup, ...]) -> None:
            self.writer = None
            if name:
                self.name.clear()
            self._set_groups(groups, name)
            self.status.setText("Card groups saved.")
            self._flush()

        def failed(error: Exception) -> None:
            self.writer = None
            if edits:
                self.pending = edits | self.pending
                self.blocked = True
                self.status.setText("Tag changes are only in this view. Retry to save them.")
                self.retry.show()
            else:
                self.status.setText(
                    str(error)
                    if isinstance(error, ValueError)
                    else "Couldn’t save the card group. Try again."
                )
                self._flush()
            self._controls()

        self.writer.result.connect(saved)
        self.writer.error.connect(failed)

    def _retry(self) -> None:
        self.retry.hide()
        if self.ready:
            self.blocked = False
            self._flush()
        else:
            self._load()
