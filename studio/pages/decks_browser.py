"""Local controls for browsing published events and standings."""

from datetime import date
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QInputEvent, QKeyEvent, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.icons import icon
from studio.pages.decks_flow import STARTER_FORMATS
from studio.widgets import Button, ElidedLabel, Hero, Panel, SearchField, ToggleSwitch, glyph, label
from tournament_decks import Deck, Event


class Rows(QTreeWidget):
    def __init__(
        self, columns: tuple[str, ...], *, multiple: bool = False, dense: bool = False
    ) -> None:
        super().__init__()
        self.multiple = multiple
        self.row_height = t.XXL if dense else t.BUTTON_H
        self.setHeaderLabels(columns)
        self.setAccessibleName("Standings" if multiple else "Tournament events")
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setMouseTracking(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(t.BUTTON_H * 2)
        self.header().setStretchLastSection(False)
        if dense:
            self.header().setFixedHeight(t.XXL)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.header().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        if multiple:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setStyleSheet(f"""
            QTreeWidget {{
                background: {t.RAIL}; border: {t.HAIR}px solid {t.EDGE};
                border-radius: {t.R_SMALL}; outline: 0;
            }}
            QTreeWidget:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}
            QTreeWidget::item {{ padding-left: {t.SM}px; border-bottom: {t.HAIR}px solid {t.PANEL}; }}
            QTreeWidget::item:selected {{ background: {t.ACTIVE}; color: {t.PALE}; }}
            QTreeWidget::item:hover:!selected {{ background: {t.PANEL}; }}
            QHeaderView {{ background: {t.RAISED}; }}
            QHeaderView::section {{
                padding: {t.SM}px; background: {t.RAISED}; color: {t.MUTED};
                border: 0; font-size: {t.SMALL}px;
            }}
        """)

    def fixed_column(self, column: int, width: int) -> None:
        self.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(column, width)

    def row(self, values: tuple[str, ...]) -> QTreeWidgetItem:
        item = QTreeWidgetItem(values)
        item.setSizeHint(0, QSize(0, self.row_height))
        for column, value in enumerate(values):
            item.setToolTip(column, value)
        self.addTopLevelItem(item)
        return item

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.currentItem() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.itemActivated.emit(self.currentItem(), self.currentColumn())
            event.accept()
        else:
            super().keyPressEvent(event)

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().drawRow(painter, option, index)
        if (self.multiple or self.hasFocus()) and index == self.currentIndex().siblingAtColumn(0):
            painter.save()
            painter.setPen(QPen(QColor(t.GOLD), t.HAIR))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(t.HAIR, t.HAIR, -t.HAIR, -t.HAIR))
            painter.restore()

    def selectionCommand(
        self, index: QModelIndex, event: QInputEvent | None = None
    ) -> QItemSelectionModel.SelectionFlag:
        if self.multiple and index.isValid() and isinstance(event, QKeyEvent):
            # Keep a marked batch while moving the preview focus. Space toggles a row.
            flags = QItemSelectionModel.SelectionFlag
            if event.key() == Qt.Key.Key_Space:
                return flags.Toggle | flags.Rows
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and (
                event.modifiers() == Qt.KeyboardModifier.ControlModifier
                or (not event.modifiers() and len(self.selectedItems()) > 1)
            ):
                return flags.NoUpdate
        return super().selectionCommand(index, event)


class ListMessage(Panel):
    def __init__(self, *, compact: bool = False) -> None:
        super().__init__(t.LG)
        self.setStyleSheet(f'QFrame[role="panel"] {{ background: {t.RAIL}; }}')
        align = Qt.AlignmentFlag.AlignLeft if compact else Qt.AlignmentFlag.AlignHCenter
        if not compact:
            self.body.addStretch()
        self.icon = None if compact else glyph("search", t.ICON_BIG)
        if self.icon:
            symbol = QHBoxLayout()
            symbol.addStretch()
            symbol.addWidget(self.icon)
            symbol.addStretch()
            self.body.addLayout(symbol)
        self.title = label("", "subheading", True)
        self.description = label("", "muted", True)
        for widget in (self.title, self.description):
            widget.setAlignment(align)
            self.body.addWidget(widget)
        self.retry = Button("Retry")
        self.body.addWidget(self.retry, 0, align)
        self.body.addStretch()

    def set_copy(self, title: str, description: str, retry: bool, action: str = "Retry") -> None:
        if self.icon:
            self.icon.setPixmap(
                icon("warning" if retry else "search").pixmap(
                    self.icon.size(), self.icon.devicePixelRatioF()
                )
            )
        self.title.setText(title)
        self.description.setText(description)
        self.retry.setText(action)
        self.retry.setVisible(retry)


class FormatPicker(QComboBox):
    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("Tournament format")
        self.addItems(STARTER_FORMATS)
        self.setCurrentText("Goat")
        self.setMinimumHeight(t.BUTTON_H)
        self.setMinimumWidth(t.TILE_SIZE[0])
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setStyleSheet(f"""
            QComboBox {{
                background: {t.RAISED}; border: {t.HAIR}px solid {t.EDGE};
                border-radius: {t.R_SMALL}; padding: 0 {t.XXL}px 0 {t.MD}px;
            }}
            QComboBox:hover {{ border-color: {t.MUTED}; }}
            QComboBox:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}
            QComboBox::drop-down {{ width: {t.XXL}px; border: none; }}
            QComboBox QAbstractItemView {{ background: {t.PANEL}; color: {t.INK}; }}
        """)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.isEnabled():
            painter = QPainter(self)
            painter.translate(self.width() - t.LG, self.height() / 2)
            painter.rotate(90)
            icon("chevron", t.TEXT).paint(painter, -t.SM, -t.SM, t.LG, t.LG)


class Browser(QWidget):
    def __init__(self, preview: QWidget) -> None:
        super().__init__()
        body = QVBoxLayout(self)
        body.setSpacing(t.MD)
        body.setContentsMargins(t.XXL, t.XL, t.XXL, t.XL)
        self.hero = Hero(
            "Deck library",
            "Tournament decks",
            "Browse tournament results and bring published decks into EDOPro.",
            "decks",
        )
        self.hero.setFixedHeight(t.TILE_LARGE)
        body.addWidget(self.hero)

        self.toolbar = QWidget()
        toolbar = QHBoxLayout(self.toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(t.SM)
        self.format = FormatPicker()
        self.search = SearchField("Search by event name or code")
        self.search.setMinimumHeight(t.BUTTON_H)
        self.submit = Button("Search")
        self.credit = Button("Format Library", "quiet", "link")
        toolbar.addWidget(self.format)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.submit)
        toolbar.addWidget(self.credit)
        body.addWidget(self.toolbar)

        self.preview_panel = preview
        self.layout_mode = ""
        self.events_open = True
        self.preview_open = False
        self.has_installation = False
        self.has_event = False
        self.results = QWidget()
        self.panels = QGridLayout(self.results)
        self.panels.setSpacing(t.MD)
        self.panels.setContentsMargins(0, 0, 0, 0)
        self.events_panel = events_panel = Panel(t.LG)
        events_panel.body.setSpacing(t.SM)
        event_heading = QHBoxLayout()
        event_heading.addWidget(label("Events", "heading"), 1)
        self.open_standings = Button("Standings", "quiet", "arrow")
        event_heading.addWidget(self.open_standings)
        events_panel.body.addLayout(event_heading)

        self.event_context = label("", "muted", True)
        events_panel.body.addWidget(self.event_context)
        self.events = Rows(("Tournament", "Date"), dense=True)
        self.events.fixed_column(1, t.TILE_H + t.XL)
        self.event_message = ListMessage(compact=True)
        events_panel.body.addWidget(self.events, 1)
        events_panel.body.addWidget(self.event_message, 1)
        self.paging = QWidget()
        paging = QHBoxLayout(self.paging)
        paging.setContentsMargins(0, 0, 0, 0)
        self.previous = Button("Previous", "quiet", "back")
        self.next = Button("Next", "quiet", "arrow")
        self.page_number = label("Page 1", "muted")
        paging.addWidget(self.previous)
        paging.addStretch()
        paging.addWidget(self.page_number)
        paging.addStretch()
        paging.addWidget(self.next)
        events_panel.body.addWidget(self.paging)

        self.standings_panel = standings_panel = Panel(t.LG)
        standings_panel.body.setSpacing(t.XS)
        self.standings_title = ElidedLabel()
        self.standings_title.setProperty("role", "heading")
        self.standings_title.set_value("Standings")
        standings_heading = QHBoxLayout()
        standings_heading.addWidget(self.standings_title, 1)
        self.open_events = Button("Browse events", "quiet", "search")
        standings_heading.addWidget(self.open_events)
        standings_panel.body.addLayout(standings_heading)
        self.event_meta = label("Public decklists", "muted", True)
        standings_panel.body.addWidget(self.event_meta)
        self.standings = Rows(("Place", "Player", "Deck"), multiple=True, dense=True)
        self.standings.fixed_column(0, t.HUGE + t.SM)
        self.standings_message = ListMessage(compact=True)
        standings_panel.body.addWidget(self.standings, 1)
        standings_panel.body.addWidget(self.standings_message, 1)
        self.tools = QWidget()
        tools = QHBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        self.event_link = Button("View event", "quiet", "link")
        self.select_all = Button("Select all", "quiet")
        self.open_preview = Button("Preview deck", "secondary", "arrow")
        tools.addWidget(self.event_link)
        tools.addStretch()
        tools.addWidget(self.select_all)
        tools.addWidget(self.open_preview)
        standings_panel.body.addWidget(self.tools)
        self.preview_back = Button("Back to standings", "quiet", "back")
        self.preview_back.clicked.connect(self.show_standings)
        body.insertWidget(2, self.preview_back, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self.results, 1)

        self.unavailable = Panel()
        self.unavailable.body.addWidget(glyph("folder", t.BRAND_ICON))
        self.unavailable.body.addWidget(label("Choose an EDOPro installation", "heading", True))
        self.unavailable.body.addWidget(
            label("Choose where your tournament decks will be saved.", "body", True)
        )
        self.choose = Button("Choose folder", "primary", "folder")
        self.unavailable.body.addWidget(self.choose, 0, Qt.AlignmentFlag.AlignLeft)
        self.unavailable.body.addStretch()
        body.addWidget(self.unavailable, 1, Qt.AlignmentFlag.AlignTop)

        footer = Panel(t.MD)
        footer.body.setSpacing(t.SM)
        self.footer = footer
        self.goat = ToggleSwitch(
            "Use GOAT card versions",
            "Save a separate historical copy.",
            False,
        )
        self.goat.setMinimumWidth(t.TILE_LARGE + t.TILE_H)
        self.footer_row = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.footer_row.setSpacing(t.SM)
        footer.body.addLayout(self.footer_row)
        self.footer_row.addWidget(self.goat)
        actions = QHBoxLayout()
        actions.setSpacing(t.SM)
        details = QVBoxLayout()
        details.setSpacing(t.XS)
        self.selection_count = label("0 selected", "subheading")
        self.destination = ElidedLabel()
        self.destination.setMinimumWidth(t.TILE_LARGE + t.LG)
        details.addWidget(self.selection_count)
        details.addWidget(self.destination)
        actions.addLayout(details, 1)
        actions.setAlignment(details, Qt.AlignmentFlag.AlignVCenter)
        self.recent = Button("Last save", "quiet")
        self.save = Button("Add to EDOPro", "primary", "download")
        actions.addWidget(self.recent)
        actions.addWidget(self.save)
        self.footer_row.addLayout(actions, 1)
        self.readiness = label(
            "Open EDOPro once to download its cards before playing or getting artwork.",
            "small",
            True,
        )
        footer.body.addWidget(self.readiness)
        body.addWidget(footer)
        self.open_events.clicked.connect(self.browse_events)
        self.open_standings.clicked.connect(self.show_standings)
        self._reflow("split")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.footer_row.setDirection(
            QBoxLayout.Direction.LeftToRight
            if event.size().width() >= t.HERO_MAX + 2 * t.TILE_LARGE
            else QBoxLayout.Direction.TopToBottom
        )
        width = event.size().width()
        if width >= t.WIDE_CONTENT + 2 * t.XXL:
            self._reflow("wide")
        elif width >= t.HERO_MAX + 2 * t.TILE_LARGE:
            self._reflow("split")
        else:
            self._reflow("stacked")

    def _reflow(self, mode: str) -> None:
        if mode == self.layout_mode:
            return
        self.layout_mode = mode
        grid = self.panels
        for panel in (self.events_panel, self.standings_panel, self.preview_panel):
            grid.removeWidget(panel)
        for i in range(3):
            grid.setColumnStretch(i, 0)
            grid.setRowStretch(i, 0)
        if mode == "wide":
            grid.addWidget(self.events_panel, 0, 0)
            grid.addWidget(self.standings_panel, 0, 1)
            grid.addWidget(self.preview_panel, 0, 2)
        else:
            grid.addWidget(self.events_panel, 0, 0)
            grid.addWidget(self.standings_panel, 0, 0)
            if mode == "stacked":
                grid.addWidget(self.preview_panel, 0, 0)
            else:
                grid.addWidget(self.preview_panel, 0, 1)
        grid.setRowStretch(0, 1)
        if mode != "stacked":
            self.preview_open = False
        self._show_archive_panel()

    def _show_archive_panel(self) -> None:
        wide = self.layout_mode == "wide"
        stacked = self.layout_mode == "stacked"
        compact_preview = stacked and self.preview_open
        has_events = self.events.topLevelItemCount() > 0
        has_decks = self.standings.topLevelItemCount() > 0
        show_events = not compact_preview and (wide or self.events_open)
        show_standings = self.has_event and not compact_preview and (wide or not self.events_open)
        show_preview = has_decks and (compact_preview or not stacked and show_standings)
        self.open_events.setVisible(not wide and has_decks)
        self.open_standings.setVisible(not wide and self.has_event)
        self.open_preview.setVisible(stacked)
        self.paging.setVisible(has_events)
        self.tools.setVisible(has_decks)
        self.hero.setVisible(not compact_preview and not (stacked and show_standings))
        self.toolbar.setVisible(self.has_installation and not compact_preview)
        self.preview_back.setVisible(compact_preview)
        self.events_panel.setVisible(show_events)
        self.standings_panel.setVisible(show_standings)
        self.preview_panel.setVisible(show_preview)
        self.footer.setVisible(
            self.has_installation and has_decks and (show_preview or show_standings)
        )
        top = Qt.AlignmentFlag.AlignTop
        fill = Qt.AlignmentFlag(0)
        if wide:
            self.panels.addWidget(self.events_panel, 0, 0, 1, 1 if self.has_event else 3)
            self.panels.setColumnStretch(0, 4)
            self.panels.setColumnStretch(1, 5 if self.has_event else 0)
            self.panels.setColumnStretch(2, 8 if show_preview else 0)
        elif not stacked:
            self.panels.setColumnStretch(0, 4)
            self.panels.setColumnStretch(1, 6 if show_preview else 0)
        self.panels.setAlignment(self.events_panel, fill if has_events else top)
        self.panels.setAlignment(self.standings_panel, fill if has_decks else top)

    def show_preview(self) -> None:
        if self.layout_mode == "stacked" and self.standings.currentItem():
            self.preview_open = True
            self._show_archive_panel()
            self.preview_back.setFocus()

    def browse_events(self) -> None:
        self.preview_open = False
        self.events_open = True
        self._show_archive_panel()
        if self.events.isVisible():
            self.events.setFocus()

    def show_standings(self, *, focus: bool = True) -> None:
        self.preview_open = False
        self.events_open = False
        self._show_archive_panel()
        if focus and self.standings.isVisible():
            self.standings.setFocus()

    def set_formats(self, choices: tuple[str, ...]) -> None:
        selected = self.format.currentText()
        with QSignalBlocker(self.format):
            self.format.clear()
            self.format.addItems(choices)
            if selected not in choices:
                self.format.addItem(selected)
            self.format.setCurrentText(selected)

    def set_events(self, events: tuple[Event, ...]) -> None:
        with QSignalBlocker(self.events):
            self.events.clear()
            for event in events:
                try:
                    day = date.fromisoformat(event.date).strftime("%d %b %Y")
                except ValueError:
                    day = event.date or "—"
                self.events.row((event.name, day))
            if events:
                self.events.setCurrentItem(self.events.topLevelItem(0))
        self.events.setVisible(bool(events))
        self.event_message.setVisible(not events)
        self._show_archive_panel()

    def set_standings(self, decks: tuple[Deck, ...], saved: dict[int, set[Path]]) -> None:
        with QSignalBlocker(self.standings):
            self.standings.clear()
            for deck in decks:
                item = self.standings.row((str(deck.place or "—"), deck.builder, deck.name))
                item.setData(0, Qt.ItemDataRole.UserRole, deck.id)
                if deck.id in saved:
                    item.setForeground(2, QColor(t.GREEN))
                    item.setText(2, f"✓  {deck.name}")
                    item.setToolTip(2, f"{deck.name} · Saved in this installation")
            if decks:
                self.standings.setCurrentItem(self.standings.topLevelItem(0))
        self.standings.setVisible(bool(decks))
        self.standings_message.setVisible(not decks)
        self._show_archive_panel()
